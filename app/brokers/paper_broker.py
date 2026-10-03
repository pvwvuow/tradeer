"""The paper broker (spec C8): the same `Broker` interface as the live one, on live quotes.

Market orders fill at the live ask or bid plus the configured slippage; pending orders fill
when the price crosses them; SL and TP are checked against the live price on every analysis
cycle; commission is charged half on entry, half on exit. Profit comes from MT5's
`order_calc_profit` (the money one lot makes per 1.0 of price, read when the position opens),
never from the tick value. Positions, orders, deals and the balance are saved as JSON in the
local `sync_state` table, so paper trades survive a restart. Nothing is ever sent to MT5.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Protocol

from app.brokers.base import BrokerOrder, BrokerPosition, OrderResult
from app.brokers.market import MarketReads, Quote
from app.brokers.requests import OrderPlan
from app.core.execution_settings import ExecutionSettings
from app.domain.history import DEAL_ENTRY_IN, Deal
from app.domain.orders import exit_price, market_price, stop_distance_check
from app.domain.signals import Direction, OrderType
from app.mt5 import api
from app.risk.limits import AccountPicture, OpenPosition
from app.risk.limits_state import AccountMoney

FIRST_TICKET = 9_000_000_000
KEEP_DEALS = 2_000
DEFAULT_BALANCE = 10_000.0
STATE_KEY = "paper_state"
DEAL_REASON_EXPERT = 3
DEAL_REASON_SL = 4
DEAL_REASON_TP = 5
DONE = 10009
PLACED = 10008
PENDING_TYPES = {
    (Direction.LONG, OrderType.LIMIT): api.ORDER_TYPE_BUY_LIMIT,
    (Direction.SHORT, OrderType.LIMIT): api.ORDER_TYPE_SELL_LIMIT,
    (Direction.LONG, OrderType.STOP): api.ORDER_TYPE_BUY_STOP,
    (Direction.SHORT, OrderType.STOP): api.ORDER_TYPE_SELL_STOP,
}


class RiskReads(Protocol):
    def picture(
        self,
        *,
        day: str,
        day_start_server: int,
        read_at: float,
    ) -> AccountPicture | None: ...

    def loss_per_lot(
        self,
        symbol: str,
        direction: Direction,
        entry: float,
        sl: float,
    ) -> float | None: ...

    def margin(
        self,
        symbol: str,
        direction: Direction,
        volume: float,
        price: float,
    ) -> float | None: ...


class StateStore(Protocol):
    def get_state(self, key: str) -> str | None: ...

    def set_state(self, key: str, value: str) -> None: ...


@dataclass(frozen=True)
class PaperPosition:
    ticket: int
    symbol: str
    direction: Direction
    volume: float
    price_open: float
    sl: float
    tp: float
    magic: int
    comment: str
    time: int  # broker server time
    value_per_price: float  # account money one lot makes per 1.0 of price
    profit: float = 0.0  # floating


@dataclass(frozen=True)
class PaperOrder:
    ticket: int
    symbol: str
    direction: Direction
    order_type: OrderType
    volume: float
    price: float
    sl: float
    tp: float
    magic: int
    comment: str
    time: int
    expiration: int  # broker server time, 0 = none


@dataclass
class PaperState:
    balance: float
    next_ticket: int = FIRST_TICKET
    positions: dict[int, PaperPosition] = field(default_factory=dict)
    orders: dict[int, PaperOrder] = field(default_factory=dict)
    deals: list[Deal] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "balance": self.balance,
                "next_ticket": self.next_ticket,
                "positions": [asdict(item) for item in self.positions.values()],
                "orders": [asdict(item) for item in self.orders.values()],
                "deals": [asdict(item) for item in self.deals[-KEEP_DEALS:]],
            },
        )

    @classmethod
    def from_json(cls, text: str) -> PaperState:
        raw = json.loads(text)
        positions: dict[int, PaperPosition] = {}
        for item in raw.get("positions", []):
            item["direction"] = Direction(item["direction"])
            positions[int(item["ticket"])] = PaperPosition(**item)
        orders: dict[int, PaperOrder] = {}
        for item in raw.get("orders", []):
            item["direction"] = Direction(item["direction"])
            item["order_type"] = OrderType(item["order_type"])
            orders[int(item["ticket"])] = PaperOrder(**item)
        deals = [Deal(**item) for item in raw.get("deals", [])]
        return cls(
            balance=float(raw["balance"]),
            next_ticket=int(raw.get("next_ticket", FIRST_TICKET)),
            positions=positions,
            orders=orders,
            deals=deals,
        )


def _fail(text: str, price: float = math.nan) -> OrderResult:
    return OrderResult(False, None, f"paper: {text}", requested_price=price)


def _crossed(order: PaperOrder, quote: Quote) -> bool:
    current = market_price(order.direction, quote.bid, quote.ask)
    above = current >= order.price
    if order.order_type is OrderType.STOP:
        return above if order.direction is Direction.LONG else current <= order.price
    return current <= order.price if order.direction is Direction.LONG else above


class PaperBroker:
    """`Broker` that simulates fills on live quotes. Used from the analysis thread only."""

    def __init__(
        self,
        market: MarketReads,
        settings: Callable[[], ExecutionSettings],
        *,
        store: StateStore | None = None,
        account: Callable[[], str | None] | None = None,
        utc_now: Callable[[], float] = time.time,
    ) -> None:
        self._market = market
        self._settings = settings
        self._store = store
        self._account = account or (lambda: None)
        self._now = utc_now
        self._state: PaperState | None = None
        self._state_key = ""

    @property
    def mode(self) -> str:
        return "paper"

    # State --------------------------------------------------------------------------------
    def state(self) -> PaperState:
        key = f"{STATE_KEY}:{self._account() or 'none'}"
        if self._state is not None and key == self._state_key:
            return self._state
        loaded: PaperState | None = None
        if self._store is not None:
            text = self._store.get_state(key)
            if text:
                try:
                    loaded = PaperState.from_json(text)
                except (ValueError, KeyError, TypeError):
                    loaded = None
        if loaded is None:
            loaded = PaperState(balance=self._opening_balance())
        self._state, self._state_key = loaded, key
        return loaded

    def _opening_balance(self) -> float:
        start = self._settings().paper_start_balance
        if start > 0:
            return float(start)
        try:
            live = self._market.balance()
        except Exception:
            live = None
        return float(live) if live is not None and live > 0 else DEFAULT_BALANCE

    def _save(self) -> None:
        if self._store is not None and self._state is not None:
            self._store.set_state(self._state_key, self._state.to_json())

    def reset(self, balance: float | None = None) -> None:
        """Start the paper account again (no positions, the given or opening balance)."""
        self.state()
        self._state = PaperState(balance=balance if balance else self._opening_balance())
        self._save()

    def _ticket(self) -> int:
        state = self.state()
        ticket = state.next_ticket
        state.next_ticket += 1
        return ticket

    @property
    def balance(self) -> float:
        return self.state().balance

    @property
    def equity(self) -> float:
        state = self.state()
        return state.balance + sum(p.profit for p in state.positions.values())

    # Broker -------------------------------------------------------------------------------
    def open(self, plan: OrderPlan) -> OrderResult:
        quote = self._market.quote(plan.symbol)
        spec = self._market.spec(plan.symbol)
        if quote is None or spec is None:
            return _fail("no live price for the symbol", plan.price)
        point = spec.point if spec.point > 0 else 10.0**-plan.digits
        state = self.state()
        if plan.order_type is not OrderType.MARKET:
            kind = PENDING_TYPES[(plan.direction, plan.order_type)]
            order = PaperOrder(
                ticket=self._ticket(),
                symbol=plan.symbol,
                direction=plan.direction,
                order_type=plan.order_type,
                volume=plan.volume,
                price=plan.price,
                sl=plan.sl,
                tp=plan.tp,
                magic=plan.magic,
                comment=plan.comment,
                time=quote.time,
                expiration=int(plan.expiration or 0),
            )
            if _crossed(order, quote):
                return _fail("the price is already past the pending entry", plan.price)
            state.orders[order.ticket] = order
            self._save()
            text = f"{PLACED} PLACED: paper pending order {kind} placed"
            return OrderResult(
                True,
                PLACED,
                text,
                order=order.ticket,
                price=plan.price,
                volume=plan.volume,
                requested_price=plan.price,
                placed=True,
            )
        price = market_price(plan.direction, quote.bid, quote.ask)
        check = stop_distance_check(
            plan.direction,
            price,
            plan.sl,
            plan.tp,
            stops_level=spec.stops_level,
            point=point,
        )
        if not check.passed:
            return _fail(f"invalid stops ({check.detail})", price)
        fill = price + plan.direction.sign * self._settings().paper_slippage_points * point
        position = self._fill(plan.symbol, plan.direction, plan.volume, fill, plan, quote.time)
        if position is None:
            return _fail("MT5 could not value the position (order_calc_profit failed)", price)
        self._save()
        return OrderResult(
            True,
            DONE,
            f"{DONE} DONE: paper fill",
            order=position.ticket,
            deal=position.ticket,
            position=position.ticket,
            price=fill,
            volume=plan.volume,
            requested_price=price,
        )

    def _fill(
        self,
        symbol: str,
        direction: Direction,
        volume: float,
        fill: float,
        plan: OrderPlan | PaperOrder,
        server_time: int,
        ticket: int | None = None,
    ) -> PaperPosition | None:
        value = self._market.profit(symbol, Direction.LONG, 1.0, fill, fill + 1.0)
        if value is None or not math.isfinite(value) or value <= 0:
            return None
        state = self.state()
        number = ticket if ticket is not None else self._ticket()
        position = PaperPosition(
            ticket=number,
            symbol=symbol,
            direction=direction,
            volume=volume,
            price_open=fill,
            sl=plan.sl,
            tp=plan.tp,
            magic=plan.magic,
            comment=plan.comment,
            time=server_time,
            value_per_price=value,
        )
        state.positions[number] = position
        commission = -self._settings().paper_commission_per_lot * volume / 2.0
        self._deal(position, DEAL_ENTRY_IN, volume, fill, 0.0, commission, DEAL_REASON_EXPERT)
        state.balance += commission
        return position

    def _deal(
        self,
        position: PaperPosition,
        entry: int,
        volume: float,
        price: float,
        profit: float,
        commission: float,
        reason: int,
        server_time: int | None = None,
    ) -> Deal:
        state = self.state()
        buy = (position.direction is Direction.LONG) == (entry == DEAL_ENTRY_IN)
        moment = server_time if server_time is not None else position.time
        deal = Deal(
            ticket=self._ticket(),
            order=position.ticket,
            time=int(moment),
            time_msc=int(moment) * 1000,
            type=api.DEAL_TYPE_BUY if buy else api.DEAL_TYPE_SELL,
            entry=entry,
            magic=position.magic,
            position_id=position.ticket,
            reason=reason,
            volume=volume,
            price=price,
            commission=round(commission, 2),
            swap=0.0,
            profit=round(profit, 2),
            fee=0.0,
            symbol=position.symbol,
            comment=position.comment,
        )
        state.deals.append(deal)
        return deal

    def modify(self, position: BrokerPosition, sl: float, tp: float) -> OrderResult:
        state = self.state()
        found = state.positions.get(position.ticket)
        if found is None:
            return _fail("the position is closed")
        state.positions[found.ticket] = replace(found, sl=sl, tp=tp)
        self._save()
        return OrderResult(True, DONE, f"{DONE} DONE: paper SL/TP changed", order=found.ticket)

    def close(self, position: BrokerPosition, volume: float | None = None) -> OrderResult:
        found = self.state().positions.get(position.ticket)
        if found is None:
            return _fail("the position is already closed")
        quote = self._market.quote(found.symbol)
        spec = self._market.spec(found.symbol)
        if quote is None:
            return _fail("no live price for the symbol")
        point = spec.point if spec is not None and spec.point > 0 else 0.0
        price = exit_price(found.direction, quote.bid, quote.ask)
        slip = self._settings().paper_slippage_points * point
        fill = price - found.direction.sign * slip
        amount = found.volume if volume is None else min(volume, found.volume)
        self._exit(found, amount, fill, DEAL_REASON_EXPERT, quote.time)
        self._save()
        return OrderResult(
            True,
            DONE,
            f"{DONE} DONE: paper close",
            order=found.ticket,
            price=fill,
            volume=amount,
            requested_price=price,
        )

    def _exit(
        self,
        position: PaperPosition,
        volume: float,
        price: float,
        reason: int,
        server_time: int,
    ) -> None:
        state = self.state()
        profit = (price - position.price_open) * position.direction.sign
        money = profit * volume * position.value_per_price
        commission = -self._settings().paper_commission_per_lot * volume / 2.0
        self._deal(position, 1, volume, price, money, commission, reason, server_time)
        state.balance += round(money, 2) + round(commission, 2)
        rest = round(position.volume - volume, 8)
        positions = state.positions
        if rest <= 1e-9:
            positions.pop(position.ticket, None)
        else:
            positions[position.ticket] = replace(position, volume=rest)

    def cancel(self, order: BrokerOrder) -> OrderResult:
        removed = self.state().orders.pop(order.ticket, None)
        if removed is None:
            return _fail("the order is no longer pending")
        self._save()
        return OrderResult(True, DONE, f"{DONE} DONE: paper order cancelled", order=order.ticket)

    def positions(self, magics: Collection[int]) -> list[BrokerPosition] | None:
        return [
            BrokerPosition(
                ticket=p.ticket,
                symbol=p.symbol,
                direction=p.direction,
                volume=p.volume,
                price_open=p.price_open,
                sl=p.sl,
                tp=p.tp,
                profit=round(p.profit, 2),
                swap=0.0,
                magic=p.magic,
                comment=p.comment,
                time=p.time,
            )
            for p in self.state().positions.values()
            if p.magic in magics
        ]

    def orders(self, magics: Collection[int]) -> list[BrokerOrder] | None:
        return [
            BrokerOrder(
                ticket=o.ticket,
                symbol=o.symbol,
                direction=o.direction,
                pending_type=PENDING_TYPES[(o.direction, o.order_type)],
                volume=o.volume,
                price=o.price,
                sl=o.sl,
                tp=o.tp,
                magic=o.magic,
                comment=o.comment,
                expiration=o.expiration,
            )
            for o in self.state().orders.values()
            if o.magic in magics
        ]

    def deals(self, position: int) -> Sequence[Deal] | None:
        return [d for d in self.state().deals if d.position_id == position]

    # Simulation ---------------------------------------------------------------------------
    def step(self) -> list[str]:
        """Fill crossed pending orders, drop expired ones, hit SL/TP, update floating profit.
        Returns one line per event for the execution log."""
        state = self.state()
        symbols = {p.symbol for p in state.positions.values()}
        symbols |= {o.symbol for o in state.orders.values()}
        events: list[str] = []
        changed = False
        for symbol in sorted(symbols):
            quote = self._market.quote(symbol)
            if quote is None:
                continue
            for order in [o for o in state.orders.values() if o.symbol == symbol]:
                changed |= self._step_order(order, quote, events)
            for position in [p for p in state.positions.values() if p.symbol == symbol]:
                changed |= self._step_position(position, quote, events)
        if changed:
            self._save()
        return events

    def _step_order(self, order: PaperOrder, quote: Quote, events: list[str]) -> bool:
        orders = self.state().orders
        if order.expiration and quote.time >= order.expiration:
            orders.pop(order.ticket, None)
            events.append(f"Paper order {order.ticket} {order.symbol} expired")
            return True
        if not _crossed(order, quote):
            return False
        current = market_price(order.direction, quote.bid, quote.ask)
        if order.order_type is OrderType.STOP:
            point = self._point(order.symbol)
            slip = self._settings().paper_slippage_points * point
            long = order.direction is Direction.LONG
            worst = max(current, order.price) if long else min(current, order.price)
            fill = worst + order.direction.sign * slip
        else:
            fill = order.price
        orders.pop(order.ticket, None)
        filled = self._fill(
            order.symbol,
            order.direction,
            order.volume,
            fill,
            order,
            quote.time,
            ticket=order.ticket,
        )
        if filled is None:
            events.append(f"Paper order {order.ticket} could not be valued; it was dropped")
        else:
            events.append(f"Paper order {order.ticket} {order.symbol} filled at {fill:g}")
        return True

    def _point(self, symbol: str) -> float:
        try:
            spec = self._market.spec(symbol)
        except Exception:
            spec = None
        return spec.point if spec is not None and spec.point > 0 else 0.0

    def _step_position(self, position: PaperPosition, quote: Quote, events: list[str]) -> bool:
        price = exit_price(position.direction, quote.bid, quote.ask)
        sign = position.direction.sign
        if position.sl > 0 and (price - position.sl) * sign <= 0:
            fill = min(price, position.sl) if sign > 0 else max(price, position.sl)
            self._exit(position, position.volume, fill, DEAL_REASON_SL, quote.time)
            events.append(f"Paper position {position.ticket} hit its stop loss at {fill:g}")
            return True
        if position.tp > 0 and (price - position.tp) * sign >= 0:
            self._exit(position, position.volume, position.tp, DEAL_REASON_TP, quote.time)
            events.append(f"Paper position {position.ticket} hit its take profit")
            return True
        floating = (price - position.price_open) * sign * position.volume
        profit = round(floating * position.value_per_price, 2)
        if profit != position.profit:
            self.state().positions[position.ticket] = replace(position, profit=profit)
        return False

    # Risk ---------------------------------------------------------------------------------
    def picture(
        self,
        live: AccountPicture,
        *,
        day: str,
        day_start_server: int,
        strategy_of: Callable[[int], str],
    ) -> AccountPicture:
        """The live picture with the paper balance, equity, positions and today's deals."""
        state = self.state()
        positions = tuple(
            OpenPosition(
                ticket=p.ticket,
                symbol=p.symbol,
                direction=p.direction,
                volume=p.volume,
                price_open=p.price_open,
                sl=p.sl,
                profit=p.profit,
                magic=p.magic,
                risk_money=_risk_money(p),
                base=p.symbol[:3].upper(),
                quote=p.symbol[3:6].upper(),
                strategy=strategy_of(p.magic),
            )
            for p in state.positions.values()
        )
        today = [d for d in state.deals if d.time >= day_start_server]
        realized = sum(d.profit + d.commission + d.swap + d.fee for d in today)
        equity = self.equity
        money = AccountMoney(day, state.balance, equity, realized, 0.0)
        return replace(
            live,
            balance=state.balance,
            equity=equity,
            margin=0.0,
            margin_free=equity,
            margin_level=0.0,
            positions=positions,
            money=money,
            bot_entries_today=sum(1 for d in today if d.entry == DEAL_ENTRY_IN),
            manual_entries_today=0,
        )


def _risk_money(position: PaperPosition) -> float | None:
    if position.sl <= 0:
        return None
    loss = (position.price_open - position.sl) * position.direction.sign
    return max(0.0, loss * position.volume * position.value_per_price)


class ModeRiskBroker:
    """The risk manager's `RiskBroker` for the current mode: the live account in Semi-auto,
    the paper account (live picture with paper money and positions) in Paper mode."""

    def __init__(
        self,
        live: RiskReads,
        paper: PaperBroker,
        paper_mode: Callable[[], bool],
        strategy_of: Callable[[int], str],
    ) -> None:
        self._live = live
        self._paper = paper
        self._paper_mode = paper_mode
        self._strategy_of = strategy_of

    def picture(
        self,
        *,
        day: str,
        day_start_server: int,
        read_at: float,
    ) -> AccountPicture | None:
        live = self._live.picture(day=day, day_start_server=day_start_server, read_at=read_at)
        if live is None or not self._paper_mode():
            return live
        return self._paper.picture(
            live,
            day=day,
            day_start_server=day_start_server,
            strategy_of=self._strategy_of,
        )

    def loss_per_lot(
        self,
        symbol: str,
        direction: Direction,
        entry: float,
        sl: float,
    ) -> float | None:
        return self._live.loss_per_lot(symbol, direction, entry, sl)

    def margin(
        self,
        symbol: str,
        direction: Direction,
        volume: float,
        price: float,
    ) -> float | None:
        return self._live.margin(symbol, direction, volume, price)


def risk_account(account: str | None, paper_mode: bool) -> str | None:
    """Paper limits (daily loss, drawdown) are kept apart from the live account's."""
    if account is None or not paper_mode:
        return account
    return f"paper:{account}"
