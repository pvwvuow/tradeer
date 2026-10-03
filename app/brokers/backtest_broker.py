"""The backtest broker (spec C8): the paper broker driven by historical bars.

Everything an order does between two cycles is decided from one bar of the replay (bid
prices; the ask is the bid plus the bar's spread):

- a pending order fills when the bar crosses its price; a gap past it fills at the bar's
  open (a stop at the worse price, a limit at the better one); an order whose expiration is
  reached before the bar opens is dropped;
- a stop loss or take profit is hit when the bar's range reaches it; when **both** are inside
  one bar the stop loss wins (spec C8: SL and TP in the same candle count as a loss); a gap
  past either fills at the open; in the bar an order fills only its stop loss is checked;
- stop and stop-loss fills get the slippage, the commission is half on entry and half on exit,
  and the swap is charged per lot and night (three nights on the triple-swap weekday).

Market orders, closes and SL moves go through the paper broker unchanged, at the quote the
replay sets (the next bar's open for an entry), so the execution engine runs exactly as in
Paper mode.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from app.brokers.base import BrokerPosition
from app.brokers.market import MarketReads
from app.brokers.paper_broker import (
    DEAL_REASON_SL,
    DEAL_REASON_TP,
    PaperBroker,
    PaperOrder,
    PaperPosition,
)
from app.core.execution_settings import ExecutionSettings
from app.domain.history import DEAL_ENTRY_IN, Deal
from app.domain.signals import Direction, OrderType

DAY = 86_400


@dataclass(frozen=True)
class ReplayBar:
    """One bar of the replay: bid prices, the spread in price, broker server times."""

    symbol: str
    server_open: int
    server_close: int
    open: float
    high: float
    low: float
    close: float
    spread: float

    def ask(self, price: float) -> float:
        return price + self.spread


class BacktestBroker(PaperBroker):
    def __init__(
        self,
        market: MarketReads,
        settings: Callable[[], ExecutionSettings],
        *,
        swap_per_lot: Callable[[Direction], float] | None = None,
        triple_weekday: int = 2,
    ) -> None:
        super().__init__(market, settings, account=lambda: "backtest")
        self._swap_per_lot = swap_per_lot or (lambda direction: 0.0)
        self._triple_weekday = triple_weekday
        self._swaps: dict[int, float] = {}
        self._swap_day: dict[int, int] = {}  # ticket -> server day last charged

    # Swap ---------------------------------------------------------------------------------
    def positions(self, magics: Collection[int]) -> list[BrokerPosition] | None:
        found = super().positions(magics) or []
        return [replace(p, swap=round(self._swaps.get(p.ticket, 0.0), 2)) for p in found]

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
        deal = super()._deal(
            position,
            entry,
            volume,
            price,
            profit,
            commission,
            reason,
            server_time,
        )
        swap = self._swaps.pop(position.ticket, 0.0) if entry != DEAL_ENTRY_IN else 0.0
        if not swap:
            return deal
        state = self.state()
        charged = replace(deal, swap=round(swap, 2))
        state.deals[-1] = charged
        state.balance += round(swap, 2)
        return charged

    def _charge_swap(self, bar: ReplayBar) -> None:
        """Every rollover (server midnight) a position crossed since the last bar. Weekend
        nights are not charged: the triple swap of `triple_weekday` covers them, as in MT5."""
        day = bar.server_open // DAY
        for position in self.state().positions.values():
            if position.symbol != bar.symbol:
                continue
            last = self._swap_day.get(position.ticket, position.time // DAY)
            self._swap_day[position.ticket] = max(last, day)
            rate = self._swap_per_lot(position.direction)
            if not rate or day <= last:
                continue
            nights = sum(self._nights(crossed) for crossed in range(last + 1, day + 1))
            if nights:
                total = self._swaps.get(position.ticket, 0.0)
                self._swaps[position.ticket] = total + rate * position.volume * nights

    def _nights(self, day: int) -> int:
        """Swap nights charged when the server date becomes `day` (days since 1970)."""
        previous = datetime.fromtimestamp((day - 1) * DAY, UTC).weekday()
        if previous >= 5:  # Saturday and Sunday nights
            return 0
        return 3 if previous == self._triple_weekday else 1

    # Bars ---------------------------------------------------------------------------------
    def step_bar(self, bar: ReplayBar) -> list[str]:
        """Pending fills, expiries, SL and TP inside `bar`; floating profit at its close."""
        self._charge_swap(bar)
        state = self.state()
        events: list[str] = []
        filled: set[int] = set()
        for order in [o for o in state.orders.values() if o.symbol == bar.symbol]:
            ticket = self._step_order_bar(order, bar, events)
            if ticket:
                filled.add(ticket)
        for position in [p for p in state.positions.values() if p.symbol == bar.symbol]:
            self._step_position_bar(position, bar, events, just_filled=position.ticket in filled)
        return events

    def _slip(self, symbol: str) -> float:
        return self._settings().paper_slippage_points * self._point(symbol)

    def _step_order_bar(self, order: PaperOrder, bar: ReplayBar, events: list[str]) -> int:
        orders = self.state().orders
        if order.expiration and bar.server_open >= order.expiration:
            orders.pop(order.ticket, None)
            events.append(f"Backtest order {order.ticket} {order.symbol} expired")
            return 0
        long = order.direction is Direction.LONG
        # A buy fills at the ask, a sell at the bid.
        opening = bar.ask(bar.open) if long else bar.open
        high = bar.ask(bar.high) if long else bar.high
        low = bar.ask(bar.low) if long else bar.low
        if order.order_type is OrderType.STOP:
            hit = high >= order.price if long else low <= order.price
            worse = max(opening, order.price) if long else min(opening, order.price)
            fill = worse + order.direction.sign * self._slip(order.symbol)
        else:
            hit = low <= order.price if long else high >= order.price
            fill = min(opening, order.price) if long else max(opening, order.price)
        if not hit:
            return 0
        orders.pop(order.ticket, None)
        position = self._fill(
            order.symbol,
            order.direction,
            order.volume,
            fill,
            order,
            bar.server_open,
            ticket=order.ticket,
        )
        if position is None:
            events.append(f"Backtest order {order.ticket} could not be valued; it was dropped")
            return 0
        events.append(f"Backtest order {order.ticket} {order.symbol} filled at {fill:g}")
        return order.ticket

    def _step_position_bar(
        self,
        position: PaperPosition,
        bar: ReplayBar,
        events: list[str],
        *,
        just_filled: bool,
    ) -> None:
        long = position.direction is Direction.LONG
        sign = position.direction.sign
        # A buy closes at the bid, a sell at the ask.
        opening = bar.open if long else bar.ask(bar.open)
        worst = bar.low if long else bar.ask(bar.high)
        best = bar.high if long else bar.ask(bar.low)
        closing = bar.close if long else bar.ask(bar.close)
        slip = self._slip(position.symbol)
        sl, tp = position.sl, position.tp
        if sl > 0 and not just_filled and (opening - sl) * sign <= 0:
            gap = opening - sign * slip
            self._exit_at(position, gap, DEAL_REASON_SL, bar, events, "gap past SL")
            return
        if tp > 0 and not just_filled and (opening - tp) * sign >= 0:
            self._exit_at(position, opening, DEAL_REASON_TP, bar, events, "gap past TP")
            return
        if sl > 0 and (worst - sl) * sign <= 0:
            self._exit_at(position, sl - sign * slip, DEAL_REASON_SL, bar, events, "stop loss")
            return
        if tp > 0 and not just_filled and (best - tp) * sign >= 0:
            self._exit_at(position, tp, DEAL_REASON_TP, bar, events, "take profit")
            return
        floating = (closing - position.price_open) * sign * position.volume
        profit = round(floating * position.value_per_price, 2)
        if profit != position.profit or not math.isfinite(position.profit):
            self.state().positions[position.ticket] = replace(position, profit=profit)

    def _exit_at(
        self,
        position: PaperPosition,
        price: float,
        reason: int,
        bar: ReplayBar,
        events: list[str],
        why: str,
    ) -> None:
        self._exit(position, position.volume, price, reason, bar.server_open)
        events.append(f"Backtest position {position.ticket} closed by {why} at {price:g}")
