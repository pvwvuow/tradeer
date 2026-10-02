"""FakeMT5: a scripted stand-in for the `MetaTrader5` package, for automated tests only.

It mimics the shapes the real package returns (named records, numpy rate arrays, `None` plus
`last_error()` on failure) and records every call, so tests can prove that read-only tools
never trade. It is never imported by the app and never shipped.
"""

from __future__ import annotations

import time
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
    deals: list[SimpleNamespace] = field(default_factory=list)
    orders: list[SimpleNamespace] = field(default_factory=list)
    rates_count: int = 5000
    # Symbol -> seconds its bars trail the clock, as right after a connect on a real account.
    stale_history: dict[str, int] = field(default_factory=dict)
    # Refuse bigger copy_rates requests, as a real terminal refused 100,000 bars.
    rates_request_limit: int | None = None
    initialize_error: tuple[int, str] | None = None
    hang_seconds: float = 0.0
    now: Any = time.time
    calls: list[str] = field(default_factory=list)
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
            equity=account.balance,
            margin_free=account.balance,
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
        return SimpleNamespace(
            time=self._server_now(),
            bid=found.bid,
            ask=round(found.bid + spread, found.digits),
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

    def history_deals_get(self, *args: Any, **kwargs: Any) -> tuple[SimpleNamespace, ...] | None:
        self.calls.append("history_deals_get")
        if not self._ready():
            return None
        return tuple(deal for deal in self.deals if _in_range(deal.time, args))

    def history_orders_get(self, *args: Any, **kwargs: Any) -> tuple[SimpleNamespace, ...] | None:
        self.calls.append("history_orders_get")
        if not self._ready():
            return None
        return tuple(order for order in self.orders if _in_range(order.time_setup, args))

    def positions_total(self) -> int:
        self.calls.append("positions_total")
        return self.open_positions if self._ready() else 0

    # Anything that trades must never be called by read-only code ------------------------
    def order_send(self, request: Any) -> None:
        self.calls.append("order_send")
        raise AssertionError("order_send must not be called by read-only code")

    def order_check(self, request: Any) -> None:
        self.calls.append("order_check")
        raise AssertionError("order_check must not be called by read-only code")

    # Helpers ----------------------------------------------------------------------------
    @property
    def trading_calls(self) -> list[str]:
        return [name for name in self.calls if name.startswith("order_")]

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
            trade_stops_level=10,
            trade_freeze_level=0,
            filling_mode=3,
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
