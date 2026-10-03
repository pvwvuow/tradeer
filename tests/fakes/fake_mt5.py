"""FakeMT5: a scripted stand-in for the `MetaTrader5` package, for automated tests only.

It mimics the shapes the real package returns (named records, numpy rate arrays, `None` plus
`last_error()` on failure) and records every call, so tests can prove that read-only tools
never trade. `order_check`/`order_send` simulate a trade server (positions, pending orders,
deals, scripted return codes, SL/TP hits when a test moves the price with `set_bid`). It is
never imported by the app and never shipped.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import numpy as np

from app.mt5 import api

DEFAULT_PATH = r"C:\Program Files\Demo Broker MetaTrader 5\terminal64.exe"
RATE_DTYPE = np.dtype(
    [
        ("time", "<i8"),
        ("open", "<f8"),
        ("high", "<f8"),
        ("low", "<f8"),
        ("close", "<f8"),
        ("tick_volume", "<u8"),
        ("spread", "<i4"),
        ("real_volume", "<u8"),
    ],
)


@dataclass
class FakeAccount:
    login: int = 51234567
    password: str = "Demo-Pass-123"
    investor_password: str = "Investor-Pass-456"
    server: str = "DemoBroker-Server"
    name: str = "Test Trader"
    company: str = "Demo Broker Ltd"
    currency: str = "USD"
    balance: float = 10_000.0
    leverage: int = 100
    trade_mode: int = api.ACCOUNT_TRADE_MODE_DEMO
    margin_mode: int = api.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
    trade_expert: bool = True
    margin: float = 0.0


@dataclass
class FakeSymbol:
    name: str
    bid: float
    digits: int = 5
    spread_points: int = 7
    contract_size: float = 100_000.0
    trade_mode: int = api.SYMBOL_TRADE_MODE_FULL
    visible: bool = True
    filling_flags: int = 3  # SYMBOL_FILLING_FOK | SYMBOL_FILLING_IOC
    stops_level: int = 10
    # Relative price moves per server time; None = the gentle default wave below.
    path: Callable[[np.ndarray[Any, Any]], np.ndarray[Any, Any]] | None = None


def default_symbols() -> list[FakeSymbol]:
    return [
        FakeSymbol("EURUSD.m", 1.08345),
        FakeSymbol("GBPUSD.m", 1.27012),
        FakeSymbol("XAUUSD.m", 2385.42, digits=2, spread_points=25, contract_size=100.0),
        FakeSymbol("USDJPY.m", 151.234, digits=3),
    ]


@dataclass
class FakeMT5:
    terminal_path: str = DEFAULT_PATH
    accounts: list[FakeAccount] = field(default_factory=lambda: [FakeAccount()])
    symbols: list[FakeSymbol] = field(default_factory=default_symbols)
    terminal_running: bool = True
    broker_connected: bool = True
    algo_trading: bool = True
    tradeapi_disabled: bool = False
    build: int = 4755
    maxbars: int = 100_000
    ping_us: int = 35_000
    server_offset_hours: float = 3.0
    open_positions: int = 0
    positions: list[SimpleNamespace] = field(default_factory=list)
    # Fail order_calc_profit / order_calc_margin (return None), as MT5 does for a bad symbol.
    calc_fails: bool = False
    deals: list[SimpleNamespace] = field(default_factory=list)
    orders: list[SimpleNamespace] = field(default_factory=list)
    rates_count: int = 5000
    # Symbol -> seconds its bars trail the clock, as right after a connect on a real account.
    stale_history: dict[str, int] = field(default_factory=dict)
    # Refuse bigger copy_rates requests, as a real terminal refused 100,000 bars.
    rates_request_limit: int | None = None
    # The oldest bar the terminal has (server time), as "Max bars in chart" limits history.
    history_start: int | None = None
    # Ticks on the synthetic bar path (the bid is the newest bar's close), for replay tests.
    ticks_follow_bars: bool = False
    initialize_error: tuple[int, str] | None = None
    hang_seconds: float = 0.0
    now: Any = time.time
    calls: list[str] = field(default_factory=list)
    # Trading simulation: return codes to answer the next sends with (nothing happens), sends
    # that execute but answer TIMEOUT (a lost reply), commission per lot and side.
    send_script: list[int] = field(default_factory=list)
    lost_replies: int = 0
    check_retcode: int = 0
    commission_per_lot: float = 0.0
    pending_orders: list[SimpleNamespace] = field(default_factory=list)
    _next_ticket: int = 70_000_000
    _simulated: set[int] = field(default_factory=set)
    _error: tuple[int, str] = (api.RES_S_OK, "Success")
    _initialized: bool = False
    _account: FakeAccount | None = None
    _investor: bool = False

    # Connection -------------------------------------------------------------------------
    def initialize(self, path: str | None = None, **kwargs: Any) -> bool:
        self.calls.append("initialize")
        if self.hang_seconds:
            time.sleep(self.hang_seconds)
        if self.initialize_error is not None:
            return self._fail(*self.initialize_error)
        if path is not None and path.casefold() != self.terminal_path.casefold():
            return self._fail(api.RES_E_INTERNAL_FAIL_INIT, "IPC initialize failed, path")
        self.terminal_running = True
        self._initialized = True
        if "login" in kwargs:
            return self.login(kwargs["login"], kwargs.get("password", ""), kwargs.get("server", ""))
        self._ok()
        return True

    def login(
        self,
        login: int,
        password: str = "",
        server: str = "",
        timeout: int = 60000,
    ) -> bool:
        self.calls.append("login")
        if not self._initialized:
            return self._fail(api.RES_E_INTERNAL_FAIL_INIT, "IPC initialize failed")
        for account in self.accounts:
            if account.login != login or (server and server != account.server):
                continue
            if password in (account.password, account.investor_password):
                self._account = account
                self._investor = password == account.investor_password
                self._ok()
                return True
        return self._fail(api.RES_E_AUTH_FAILED, "Terminal: Authorization failed")

    def shutdown(self) -> None:
        self.calls.append("shutdown")
        self._initialized = False

    def last_error(self) -> tuple[int, str]:
        return self._error

    def version(self) -> tuple[int, int, str] | None:
        self.calls.append("version")
        return (500, self.build, "21 Feb 2025") if self._ready() else None

    # Information ------------------------------------------------------------------------
    def terminal_info(self) -> SimpleNamespace | None:
        self.calls.append("terminal_info")
        if not self._ready():
            return None
        return SimpleNamespace(
            name="Demo Broker MetaTrader 5",
            company="Demo Broker Ltd",
            path=self.terminal_path.rsplit("\\", 1)[0],
            data_path=r"C:\Users\trader\AppData\Roaming\MetaQuotes\Terminal\ABC123",
            build=self.build,
            connected=self.broker_connected,
            trade_allowed=self.algo_trading,
            tradeapi_disabled=self.tradeapi_disabled,
            ping_last=self.ping_us,
            maxbars=self.maxbars,
        )

    def account_info(self) -> SimpleNamespace | None:
        self.calls.append("account_info")
        account = self._account
        if not self._ready() or account is None:
            return None
        return SimpleNamespace(
            login=account.login,
            name=account.name,
            server=account.server,
            company=account.company,
            currency=account.currency,
            balance=account.balance,
            equity=account.balance + self._floating(),
            margin_free=account.balance + self._floating(),
            leverage=account.leverage,
            trade_mode=account.trade_mode,
            margin_mode=account.margin_mode,
            trade_allowed=not self._investor,
            trade_expert=account.trade_expert,
            margin_so_mode=api.ACCOUNT_STOPOUT_MODE_PERCENT,
            margin_so_call=100.0,
            margin_so_so=50.0,
            margin=account.margin,
            margin_level=account.balance / account.margin * 100.0 if account.margin else 0.0,
        )

    def symbols_total(self) -> int:
        self.calls.append("symbols_total")
        return len(self.symbols) if self._ready() else 0

    def symbols_get(self, *args: Any, **kwargs: Any) -> tuple[SimpleNamespace, ...] | None:
        self.calls.append("symbols_get")
        if not self._ready():
            return None
        return tuple(self._symbol_record(symbol) for symbol in self.symbols)

    def symbol_info(self, symbol: str) -> SimpleNamespace | None:
        self.calls.append("symbol_info")
        found = self._find(symbol)
        return self._symbol_record(found) if found is not None and self._ready() else None

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        self.calls.append("symbol_select")
        found = self._find(symbol)
        if found is None:
            return self._fail(api.RES_E_NOT_FOUND, "Symbol not found")
        found.visible = enable
        return True

    def symbol_info_tick(self, symbol: str) -> SimpleNamespace | None:
        self.calls.append("symbol_info_tick")
        found = self._find(symbol)
        if found is None or not self._ready() or not self.broker_connected:
            return None
        spread = found.spread_points / 10**found.digits
        bid = found.bid
        if self.ticks_follow_bars:
            bid = round(float(synthetic_price(found, np.int64(self._server_now()))), found.digits)
        return SimpleNamespace(
            time=self._server_now(),
            bid=bid,
            ask=round(bid + spread, found.digits),
            last=0.0,
            volume=0,
            time_msc=self._server_now() * 1000,
        )

    def copy_rates_from_pos(
        self,
        symbol: str,
        timeframe: int,
        start_pos: int,
        count: int,
    ) -> np.ndarray[Any, Any] | None:
        self.calls.append("copy_rates_from_pos")
        found = self._find(symbol)
        if found is None or not self._ready():
            return self._fail_none(api.RES_E_NOT_FOUND, "Symbol not found")
        if self.rates_request_limit is not None and count > self.rates_request_limit:
            return self._fail_none(api.RES_E_INVALID_PARAMS, "Terminal: Invalid params")
        bars = max(0, min(count, self.rates_count - start_pos))
        seconds = max(60, (timeframe if timeframe < 16000 else (timeframe - 16384) * 60) * 60)
        newest = self._server_now() - self.stale_history.get(symbol, 0)
        last_open = newest // seconds * seconds - seconds * start_pos
        opens = last_open - seconds * np.arange(bars - 1, -1, -1, dtype=np.int64)
        return synthetic_rates(found, opens, seconds)

    def copy_rates_range(
        self,
        symbol: str,
        timeframe: int,
        date_from: Any,
        date_to: Any,
    ) -> np.ndarray[Any, Any] | None:
        """Closed bars opening from `date_from` to `date_to` (server time, as MT5 reads it)."""
        self.calls.append("copy_rates_range")
        found = self._find(symbol)
        if found is None or not self._ready():
            return self._fail_none(api.RES_E_NOT_FOUND, "Symbol not found")
        seconds = max(60, (timeframe if timeframe < 16000 else (timeframe - 16384) * 60) * 60)
        first = max(_epoch(date_from), self.history_start or 0)
        last = min(_epoch(date_to), self._server_now() - seconds)  # no forming bar
        start = -(-first // seconds) * seconds
        opens = np.arange(start, last + 1, seconds, dtype=np.int64)
        if self.rates_request_limit is not None and len(opens) > self.rates_request_limit:
            return self._fail_none(api.RES_E_INVALID_PARAMS, "Terminal: Invalid params")
        return synthetic_rates(found, opens, seconds)

    def history_deals_get(self, *args: Any, **kwargs: Any) -> tuple[SimpleNamespace, ...] | None:
        self.calls.append("history_deals_get")
        if not self._ready():
            return None
        found = [deal for deal in self.deals if _in_range(deal.time, args)]
        if "ticket" in kwargs:
            found = [deal for deal in found if deal.ticket == kwargs["ticket"]]
        if "position" in kwargs:
            found = [deal for deal in found if deal.position_id == kwargs["position"]]
        return tuple(found)

    def history_orders_get(self, *args: Any, **kwargs: Any) -> tuple[SimpleNamespace, ...] | None:
        self.calls.append("history_orders_get")
        if not self._ready():
            return None
        return tuple(order for order in self.orders if _in_range(order.time_setup, args))

    def positions_total(self) -> int:
        self.calls.append("positions_total")
        if not self._ready():
            return 0
        return len(self.positions) if self.positions else self.open_positions

    def positions_get(self, *args: Any, **kwargs: Any) -> tuple[SimpleNamespace, ...] | None:
        self.calls.append("positions_get")
        if not self._ready():
            return None
        self._revalue()
        found = list(self.positions)
        if "symbol" in kwargs:
            found = [p for p in found if p.symbol == kwargs["symbol"]]
        if "ticket" in kwargs:
            found = [p for p in found if p.ticket == kwargs["ticket"]]
        return tuple(found)

    def orders_get(self, *args: Any, **kwargs: Any) -> tuple[SimpleNamespace, ...] | None:
        self.calls.append("orders_get")
        if not self._ready():
            return None
        found = list(self.pending_orders)
        if "symbol" in kwargs:
            found = [o for o in found if o.symbol == kwargs["symbol"]]
        if "ticket" in kwargs:
            found = [o for o in found if o.ticket == kwargs["ticket"]]
        return tuple(found)

    # Calculations (never trade) ----------------------------------------------------------
    def order_calc_profit(
        self,
        action: int,
        symbol: str,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None:
        """Profit in the account currency, converted with the fake's own quotes like MT5."""
        self.calls.append("order_calc_profit")
        found = self._find(symbol)
        if found is None or not self._ready() or self.calc_fails:
            return self._fail_none(api.RES_E_INVALID_PARAMS, "Invalid params")
        sign = 1.0 if action == api.ORDER_TYPE_BUY else -1.0
        profit = (price_close - price_open) * sign * found.contract_size * volume
        return round(profit * self._to_account(symbol[3:6]), 2)

    def order_calc_margin(
        self,
        action: int,
        symbol: str,
        volume: float,
        price_open: float,
    ) -> float | None:
        self.calls.append("order_calc_margin")
        found = self._find(symbol)
        account = self._account
        if found is None or account is None or not self._ready() or self.calc_fails:
            return self._fail_none(api.RES_E_INVALID_PARAMS, "Invalid params")
        base = symbol[:3]
        notional = found.contract_size * volume
        if base == account.currency:
            return round(notional / account.leverage, 2)
        value = notional * price_open * self._to_account(symbol[3:6])
        return round(value / account.leverage, 2)

    def _to_account(self, currency: str) -> float:
        """Rate from `currency` to the account currency, through USD with the fake's bids."""
        account = self._account.currency if self._account is not None else "USD"
        return self._to_usd(currency) / self._to_usd(account)

    def _to_usd(self, currency: str) -> float:
        if currency == "USD":
            return 1.0
        for symbol in self.symbols:
            if symbol.name.startswith(currency + "USD"):
                return symbol.bid
            if symbol.name.startswith("USD" + currency):
                return 1.0 / symbol.bid
        raise AssertionError(f"FakeMT5 has no USD rate for {currency}")

    # Trading simulation (read-only code must never call these: see `trading_calls`) ------
    def order_check(self, request: dict[str, Any]) -> SimpleNamespace | None:
        self.calls.append("order_check")
        if not self._ready():
            return self._fail_none(api.RES_E_FAIL, "Terminal not ready")
        code = self.check_retcode or self._validate(request)
        account = self._account
        balance = account.balance if account is not None else 0.0
        return SimpleNamespace(
            retcode=0 if code in (0, api.TRADE_RETCODE_DONE) else code,
            balance=balance,
            equity=balance + self._floating(),
            profit=0.0,
            margin=0.0,
            margin_free=balance,
            margin_level=0.0,
            comment="Done" if not code else "Rejected",
            request=SimpleNamespace(**request),
        )

    def order_send(self, request: dict[str, Any]) -> SimpleNamespace | None:
        self.calls.append("order_send")
        if not self._ready():
            return self._fail_none(api.RES_E_FAIL, "Terminal not ready")
        if self.send_script:
            return self._result(self.send_script.pop(0), request)
        code = self._validate(request)
        if code:
            return self._result(code, request)
        action = int(request["action"])
        if action == api.TRADE_ACTION_DEAL and request.get("position"):
            result = self._close(request)
        elif action == api.TRADE_ACTION_DEAL:
            result = self._open(request)
        elif action == api.TRADE_ACTION_PENDING:
            ticket = self._ticket()
            self.pending_orders.append(
                SimpleNamespace(
                    ticket=ticket,
                    time_setup=self._server_now(),
                    type=int(request["type"]),
                    state=1,
                    magic=int(request.get("magic", 0)),
                    position_id=0,
                    volume_initial=float(request["volume"]),
                    volume_current=float(request["volume"]),
                    price_open=float(request["price"]),
                    sl=float(request.get("sl", 0.0)),
                    tp=float(request.get("tp", 0.0)),
                    time_expiration=int(request.get("expiration", 0)),
                    symbol=request["symbol"],
                    comment=str(request.get("comment", "")),
                ),
            )
            result = self._result(api.TRADE_RETCODE_PLACED, request, order=ticket)
        elif action == api.TRADE_ACTION_SLTP:
            position = self._position(int(request["position"]))
            if position is None:
                return self._result(10036, request)
            if (position.sl, position.tp) == (request["sl"], request["tp"]):
                return self._result(10025, request)
            position.sl, position.tp = float(request["sl"]), float(request["tp"])
            result = self._result(api.TRADE_RETCODE_DONE, request)
        elif action == api.TRADE_ACTION_REMOVE:
            order = next((o for o in self.pending_orders if o.ticket == request["order"]), None)
            if order is None:
                return self._result(10013, request)
            self.pending_orders.remove(order)
            result = self._result(api.TRADE_RETCODE_DONE, request, order=order.ticket)
        else:
            return self._result(10013, request)
        if self.lost_replies and action == api.TRADE_ACTION_DEAL:
            self.lost_replies -= 1
            return self._result(10012, request)
        return result

    def set_bid(self, symbol: str, bid: float) -> None:
        """Move the price: pending orders that are crossed fill, SL/TP hits close positions."""
        found = self._find(symbol)
        if found is None:
            raise AssertionError(f"FakeMT5 has no symbol {symbol}")
        found.bid = bid
        bid, ask = self._prices(found)
        for order in list(self.pending_orders):
            if order.symbol != symbol:
                continue
            if order.time_expiration and self._server_now() >= order.time_expiration:
                self.pending_orders.remove(order)
                continue
            long = order.type in (api.ORDER_TYPE_BUY_LIMIT, api.ORDER_TYPE_BUY_STOP)
            current = ask if long else bid
            stop = order.type in (api.ORDER_TYPE_BUY_STOP, api.ORDER_TYPE_SELL_STOP)
            rising = current >= order.price_open
            crossed = rising == long if stop else rising != long
            if crossed:
                self.pending_orders.remove(order)
                self._new_position(order, long, order.volume_current, current, order.ticket)
        for position in list(self.positions):
            if position.ticket not in self._simulated or position.symbol != symbol:
                continue
            long = position.type == api.POSITION_TYPE_BUY
            price = bid if long else ask
            sign = 1 if long else -1
            if position.sl and (price - position.sl) * sign <= 0:
                self._exit(position, position.volume, position.sl, api.DEAL_REASON_SL)
            elif position.tp and (price - position.tp) * sign >= 0:
                self._exit(position, position.volume, position.tp, api.DEAL_REASON_TP)

    def _prices(self, symbol: FakeSymbol) -> tuple[float, float]:
        spread = symbol.spread_points / 10**symbol.digits
        return symbol.bid, round(symbol.bid + spread, symbol.digits)

    def _ticket(self) -> int:
        self._next_ticket += 1
        return self._next_ticket

    def _position(self, ticket: int) -> SimpleNamespace | None:
        return next((p for p in self.positions if p.ticket == ticket), None)

    def _validate(self, request: dict[str, Any]) -> int:
        """0 when the request is valid, else the trade server's return code."""
        action = int(request.get("action", 0))
        if action in (api.TRADE_ACTION_REMOVE, api.TRADE_ACTION_SLTP):
            if action == api.TRADE_ACTION_SLTP:
                position = self._position(int(request.get("position", 0)))
                if position is None:
                    return 10036
                found = self._find(position.symbol)
                long = position.type == api.POSITION_TYPE_BUY
                return self._stops(found, long, float(request["sl"]), float(request["tp"]))
            return 0
        if not self.algo_trading:
            return 10027
        found = self._find(str(request.get("symbol", "")))
        if found is None:
            return 10013
        if found.trade_mode == api.SYMBOL_TRADE_MODE_DISABLED:
            return 10017
        volume = float(request.get("volume", 0.0))
        if volume < 0.01 or abs(round(volume / 0.01) * 0.01 - volume) > 1e-9:
            return 10014
        filling = int(request.get("type_filling", 0))
        allowed = {api.ORDER_FILLING_FOK: 1, api.ORDER_FILLING_IOC: 2}
        if filling in allowed and not found.filling_flags & allowed[filling]:
            return 10030
        if filling == api.ORDER_FILLING_RETURN and found.filling_flags:
            return 10030
        bid, ask = self._prices(found)
        kind = int(request.get("type", 0))
        long = kind in (api.ORDER_TYPE_BUY, api.ORDER_TYPE_BUY_LIMIT, api.ORDER_TYPE_BUY_STOP)
        if action == api.TRADE_ACTION_DEAL:
            current = ask if long else bid
            point = 10**-found.digits
            deviation = int(request.get("deviation", 0))
            if abs(float(request.get("price", current)) - current) > (deviation + 0.5) * point:
                return 10004
            if request.get("position"):
                return 0 if self._position(int(request["position"])) else 10036
            sl, tp = float(request.get("sl", 0)), float(request.get("tp", 0))
            return self._stops(found, long, sl, tp)
        price = float(request.get("price", 0.0))
        current = ask if long else bid
        stop = kind in (api.ORDER_TYPE_BUY_STOP, api.ORDER_TYPE_SELL_STOP)
        right_side = (price > current) == long if stop else (price < current) == long
        if not right_side:
            return 10015
        sl, tp = float(request.get("sl", 0)), float(request.get("tp", 0))
        return self._stops(found, long, sl, tp, price)

    def _stops(
        self,
        found: FakeSymbol | None,
        long: bool,
        sl: float,
        tp: float,
        price: float | None = None,
    ) -> int:
        if found is None:
            return 10013
        bid, ask = self._prices(found)
        reference = price if price is not None else (bid if long else ask)
        sign = 1 if long else -1
        minimum = found.stops_level * 10**-found.digits
        if sl and (reference - sl) * sign < minimum:
            return 10016
        if tp and (tp - reference) * sign < minimum:
            return 10016
        return 0

    def _open(self, request: dict[str, Any]) -> SimpleNamespace:
        found = self._find(str(request["symbol"]))
        assert found is not None
        bid, ask = self._prices(found)
        long = int(request["type"]) == api.ORDER_TYPE_BUY
        price = ask if long else bid
        ticket = self._ticket()
        source = SimpleNamespace(
            magic=int(request.get("magic", 0)),
            sl=float(request.get("sl", 0.0)),
            tp=float(request.get("tp", 0.0)),
            symbol=request["symbol"],
            comment=str(request.get("comment", "")),
        )
        deal = self._new_position(source, long, float(request["volume"]), price, ticket)
        return self._result(
            api.TRADE_RETCODE_DONE,
            request,
            order=ticket,
            deal=deal,
            price=price,
            volume=float(request["volume"]),
        )

    def _new_position(
        self,
        source: SimpleNamespace,
        long: bool,
        volume: float,
        price: float,
        ticket: int,
    ) -> int:
        self.positions.append(
            SimpleNamespace(
                ticket=ticket,
                symbol=source.symbol,
                type=api.POSITION_TYPE_BUY if long else api.POSITION_TYPE_SELL,
                volume=volume,
                price_open=price,
                price_current=price,
                sl=source.sl,
                tp=source.tp,
                profit=0.0,
                swap=0.0,
                magic=source.magic,
                identifier=ticket,
                comment=source.comment,
                time=self._server_now(),
            ),
        )
        self._simulated.add(ticket)
        return self._deal(ticket, source, long, api.DEAL_ENTRY_IN, volume, price, 0.0, 3)

    def _deal(
        self,
        position: int,
        source: SimpleNamespace,
        buy: bool,
        entry: int,
        volume: float,
        price: float,
        profit: float,
        reason: int,
    ) -> int:
        ticket = self._ticket()
        deal = make_trade_deal(
            ticket,
            position,
            entry=entry,
            deal_type=api.DEAL_TYPE_BUY if buy else api.DEAL_TYPE_SELL,
            time_s=self._server_now(),
            volume=volume,
            price=price,
            profit=round(profit, 2),
            commission=-round(self.commission_per_lot * volume, 2),
            magic=source.magic,
            reason=reason,
            symbol=source.symbol,
        )
        deal.order = position
        deal.comment = source.comment
        self.deals.append(deal)
        if self._account is not None:
            self._account.balance += deal.profit + deal.commission
        return ticket

    def _close(self, request: dict[str, Any]) -> SimpleNamespace:
        position = self._position(int(request["position"]))
        assert position is not None
        found = self._find(position.symbol)
        assert found is not None
        bid, ask = self._prices(found)
        long = position.type == api.POSITION_TYPE_BUY
        price = bid if long else ask
        volume = min(float(request["volume"]), position.volume)
        deal = self._exit(position, volume, price, api.DEAL_REASON_EXPERT)
        return self._result(
            api.TRADE_RETCODE_DONE,
            request,
            order=self._ticket(),
            deal=deal,
            price=price,
            volume=volume,
        )

    def _exit(self, position: SimpleNamespace, volume: float, price: float, reason: int) -> int:
        long = position.type == api.POSITION_TYPE_BUY
        sign = 1 if long else -1
        found = self._find(position.symbol)
        contract = found.contract_size if found is not None else 100_000.0
        money = (price - position.price_open) * sign * contract * volume
        money *= self._to_account(position.symbol[3:6])
        out = api.DEAL_ENTRY_OUT
        deal = self._deal(position.ticket, position, not long, out, volume, price, money, reason)
        position.volume = round(position.volume - volume, 8)
        if position.volume <= 1e-9:
            self.positions.remove(position)
            self._simulated.discard(position.ticket)
        return deal

    def _revalue(self) -> None:
        for position in self.positions:
            if position.ticket not in self._simulated:
                continue
            found = self._find(position.symbol)
            if found is None:
                continue
            bid, ask = self._prices(found)
            long = position.type == api.POSITION_TYPE_BUY
            price = bid if long else ask
            position.price_current = price
            sign = 1 if long else -1
            money = (price - position.price_open) * sign * found.contract_size * position.volume
            position.profit = round(money * self._to_account(position.symbol[3:6]), 2)

    def _floating(self) -> float:
        self._revalue()
        return sum(p.profit for p in self.positions if p.ticket in self._simulated)

    def _result(
        self,
        code: int,
        request: dict[str, Any],
        *,
        order: int = 0,
        deal: int = 0,
        price: float = 0.0,
        volume: float = 0.0,
    ) -> SimpleNamespace:
        found = self._find(str(request.get("symbol", "")))
        bid, ask = self._prices(found) if found is not None else (0.0, 0.0)
        return SimpleNamespace(
            retcode=code,
            deal=deal,
            order=order,
            volume=volume,
            price=price,
            bid=bid,
            ask=ask,
            comment="Request executed" if code == api.TRADE_RETCODE_DONE else f"code {code}",
            request_id=len(self.calls),
            retcode_external=0,
            request=SimpleNamespace(**request),
        )

    # Helpers ----------------------------------------------------------------------------
    @property
    def trading_calls(self) -> list[str]:
        return [name for name in self.calls if name in ("order_send", "order_check")]

    def _ready(self) -> bool:
        return self._initialized and self.terminal_running

    def _server_now(self) -> int:
        return int(self.now() + self.server_offset_hours * 3600)

    def _find(self, name: str) -> FakeSymbol | None:
        return next((symbol for symbol in self.symbols if symbol.name == name), None)

    def _symbol_record(self, symbol: FakeSymbol) -> SimpleNamespace:
        point = 10**-symbol.digits
        return SimpleNamespace(
            name=symbol.name,
            description=f"{symbol.name} test symbol",
            digits=symbol.digits,
            point=point,
            trade_tick_size=point,
            trade_tick_value=1.0,
            trade_contract_size=symbol.contract_size,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_stops_level=symbol.stops_level,
            trade_freeze_level=0,
            filling_mode=symbol.filling_flags,
            trade_mode=symbol.trade_mode,
            currency_margin=symbol.name[:3],
            currency_profit=symbol.name[3:6],
            visible=symbol.visible,
        )

    def _ok(self) -> None:
        self._error = (api.RES_S_OK, "Success")

    def _fail(self, code: int, message: str) -> bool:
        self._error = (code, message)
        return False

    def _fail_none(self, code: int, message: str) -> None:
        self._error = (code, message)
        return None


def synthetic_price(symbol: FakeSymbol, server_times: np.ndarray[Any, Any]) -> Any:
    """A deterministic price path: the same bar time always gives the same price."""
    moments = np.asarray(server_times, dtype=np.float64)
    if symbol.path is not None:
        return symbol.bid * (1.0 + symbol.path(moments))
    seed = sum(ord(char) for char in symbol.name)
    wave = (
        0.004 * np.sin(2 * np.pi * moments / (86_400 * 5.3) + seed)
        + 0.0015 * np.sin(2 * np.pi * moments / (3600 * 7.1) + seed / 3)
        + 0.0004 * np.sin(moments / 977.0 + seed)
    )
    return symbol.bid * (1.0 + wave)


def synthetic_rates(
    symbol: FakeSymbol,
    opens: np.ndarray[Any, Any],
    seconds: int,
) -> np.ndarray[Any, Any]:
    """Bars of `seconds` length opening at `opens` (server time), on the synthetic path."""
    rates = np.zeros(len(opens), dtype=RATE_DTYPE)
    if not len(opens):
        return rates
    samples = np.stack([synthetic_price(symbol, opens + seconds * k / 4) for k in range(5)])
    rates["time"] = opens
    rates["open"] = np.round(samples[0], symbol.digits)
    rates["close"] = np.round(samples[-1], symbol.digits)
    spread = 10.0**-symbol.digits * 3
    rates["high"] = np.round(samples.max(axis=0) + spread, symbol.digits)
    rates["low"] = np.round(samples.min(axis=0) - spread, symbol.digits)
    rates["tick_volume"] = 100 + (opens // seconds) % 50
    rates["spread"] = symbol.spread_points
    return rates


def _epoch(value: Any) -> int:
    return int(value.timestamp()) if isinstance(value, datetime) else int(value)


def _in_range(moment: int, args: tuple[Any, ...]) -> bool:
    """`history_*_get(date_from, date_to)` with datetimes or seconds; no dates means all."""
    if len(args) < 2:
        return True
    return _epoch(args[0]) <= moment <= _epoch(args[1])


def make_trade_deal(
    ticket: int,
    position_id: int,
    *,
    entry: int,
    deal_type: int,
    time_s: int,
    volume: float = 0.1,
    price: float = 1.08,
    profit: float = 0.0,
    commission: float = 0.0,
    swap: float = 0.0,
    fee: float = 0.0,
    magic: int = 0,
    reason: int = api.DEAL_REASON_CLIENT,
    symbol: str = "EURUSD.m",
) -> SimpleNamespace:
    """One deal as `history_deals_get` returns it (times are broker server time)."""
    return SimpleNamespace(
        ticket=ticket,
        order=ticket + 500_000,
        time=time_s,
        time_msc=time_s * 1000,
        type=deal_type,
        entry=entry,
        magic=magic,
        position_id=position_id,
        reason=reason,
        volume=volume,
        price=price,
        commission=commission,
        swap=swap,
        profit=profit,
        fee=fee,
        symbol=symbol,
        comment="",
        external_id="",
    )


def make_closed_trade(
    position_id: int,
    *,
    opened: int,
    closed: int,
    profit: float,
    direction: int = api.DEAL_TYPE_BUY,
    volume: float = 0.1,
    magic: int = 0,
    reason: int = api.DEAL_REASON_TP,
) -> list[SimpleNamespace]:
    """The entry and exit deal of one fully closed position."""
    closing = api.DEAL_TYPE_SELL if direction == api.DEAL_TYPE_BUY else api.DEAL_TYPE_BUY
    entry = make_trade_deal(
        position_id * 10,
        position_id,
        entry=api.DEAL_ENTRY_IN,
        deal_type=direction,
        time_s=opened,
        volume=volume,
        price=1.08,
        commission=-0.35,
        magic=magic,
    )
    exit_deal = make_trade_deal(
        position_id * 10 + 1,
        position_id,
        entry=api.DEAL_ENTRY_OUT,
        deal_type=closing,
        time_s=closed,
        volume=volume,
        price=1.081,
        profit=profit,
        commission=-0.35,
        swap=-0.12,
        magic=magic,
        reason=reason,
    )
    return [entry, exit_deal]


def make_order(ticket: int, position_id: int, time_s: int) -> SimpleNamespace:
    return SimpleNamespace(
        ticket=ticket,
        time_setup=time_s,
        time_done=time_s,
        type=0,
        state=4,
        magic=0,
        position_id=position_id,
        volume_initial=0.1,
        volume_current=0.0,
        price_open=1.08,
        sl=0.0,
        tp=0.0,
        price_current=1.08,
        symbol="EURUSD.m",
        comment="",
    )


def make_deal(ticket: int, symbol: str = "EURUSD.m", profit: float = 12.5) -> SimpleNamespace:
    return SimpleNamespace(
        ticket=ticket,
        order=ticket + 1000,
        time=1_727_000_000 + ticket * 60,
        type=0,
        entry=1,
        magic=0,
        position_id=ticket + 2000,
        volume=0.1,
        price=1.08,
        commission=-0.7,
        swap=0.0,
        profit=profit,
        symbol=symbol,
        comment="",
    )


def make_position(
    ticket: int,
    symbol: str = "EURUSD.m",
    *,
    long: bool = True,
    volume: float = 0.1,
    price_open: float = 1.08,
    sl: float = 0.0,
    profit: float = 0.0,
    magic: int = 0,
) -> SimpleNamespace:
    """One open position as `positions_get` returns it."""
    return SimpleNamespace(
        ticket=ticket,
        symbol=symbol,
        type=api.POSITION_TYPE_BUY if long else api.POSITION_TYPE_SELL,
        volume=volume,
        price_open=price_open,
        sl=sl,
        tp=0.0,
        profit=profit,
        magic=magic,
        identifier=ticket,
    )
