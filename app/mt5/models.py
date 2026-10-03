"""Plain snapshots of MT5 objects, so nothing outside the gateway holds package types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.mt5 import api


class AccountKind(StrEnum):
    DEMO = "DEMO"
    CONTEST = "CONTEST"
    REAL = "REAL"
    UNKNOWN = "UNKNOWN"


class MarginMode(StrEnum):
    HEDGING = "hedging"
    NETTING = "netting"
    EXCHANGE = "exchange"
    UNKNOWN = "unknown"


class SymbolTradeMode(StrEnum):
    FULL = "full"
    LONG_ONLY = "long only"
    SHORT_ONLY = "short only"
    CLOSE_ONLY = "close only"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


_ACCOUNT_KINDS = {
    api.ACCOUNT_TRADE_MODE_DEMO: AccountKind.DEMO,
    api.ACCOUNT_TRADE_MODE_CONTEST: AccountKind.CONTEST,
    api.ACCOUNT_TRADE_MODE_REAL: AccountKind.REAL,
}
_MARGIN_MODES = {
    api.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING: MarginMode.HEDGING,
    api.ACCOUNT_MARGIN_MODE_RETAIL_NETTING: MarginMode.NETTING,
    api.ACCOUNT_MARGIN_MODE_EXCHANGE: MarginMode.EXCHANGE,
}
_SYMBOL_TRADE_MODES = {
    api.SYMBOL_TRADE_MODE_FULL: SymbolTradeMode.FULL,
    api.SYMBOL_TRADE_MODE_LONGONLY: SymbolTradeMode.LONG_ONLY,
    api.SYMBOL_TRADE_MODE_SHORTONLY: SymbolTradeMode.SHORT_ONLY,
    api.SYMBOL_TRADE_MODE_CLOSEONLY: SymbolTradeMode.CLOSE_ONLY,
    api.SYMBOL_TRADE_MODE_DISABLED: SymbolTradeMode.DISABLED,
}


def _get(source: Any, name: str, default: Any = None) -> Any:
    return getattr(source, name, default)


def _int(source: Any, name: str) -> int:
    value = _get(source, name, 0)
    return int(value) if value is not None else 0


def _float(source: Any, name: str) -> float:
    value = _get(source, name, 0.0)
    return float(value) if value is not None else 0.0


def _str(source: Any, name: str) -> str:
    value = _get(source, name, "")
    return str(value) if value is not None else ""


def _bool(source: Any, name: str) -> bool:
    return bool(_get(source, name, False))


@dataclass(frozen=True)
class TerminalSnapshot:
    name: str
    company: str
    path: str
    data_path: str
    build: int
    connected: bool
    trade_allowed: bool
    tradeapi_disabled: bool
    ping_ms: float
    maxbars: int

    @classmethod
    def from_mt5(cls, info: Any, version: Any = None) -> TerminalSnapshot:
        build = _int(info, "build")
        if not build and isinstance(version, tuple | list) and len(version) > 1:
            build = int(version[1])
        return cls(
            name=_str(info, "name"),
            company=_str(info, "company"),
            path=_str(info, "path"),
            data_path=_str(info, "data_path"),
            build=build,
            connected=_bool(info, "connected"),
            trade_allowed=_bool(info, "trade_allowed"),
            tradeapi_disabled=_bool(info, "tradeapi_disabled"),
            ping_ms=_float(info, "ping_last") / 1000.0,
            maxbars=_int(info, "maxbars"),
        )


@dataclass(frozen=True)
class AccountSnapshot:
    login: int
    name: str
    server: str
    company: str
    currency: str
    balance: float
    equity: float
    margin_free: float
    leverage: int
    kind: AccountKind
    margin_mode: MarginMode
    trade_allowed: bool
    trade_expert: bool
    stopout_percent: bool
    margin_call: float
    stop_out: float
    margin: float = 0.0
    margin_level: float = 0.0

    @classmethod
    def from_mt5(cls, info: Any) -> AccountSnapshot:
        return cls(
            login=_int(info, "login"),
            name=_str(info, "name"),
            server=_str(info, "server"),
            company=_str(info, "company"),
            currency=_str(info, "currency"),
            balance=_float(info, "balance"),
            equity=_float(info, "equity"),
            margin_free=_float(info, "margin_free"),
            leverage=_int(info, "leverage"),
            kind=_ACCOUNT_KINDS.get(_int(info, "trade_mode"), AccountKind.UNKNOWN),
            margin_mode=_MARGIN_MODES.get(_int(info, "margin_mode"), MarginMode.UNKNOWN),
            trade_allowed=_bool(info, "trade_allowed"),
            trade_expert=_bool(info, "trade_expert"),
            stopout_percent=_int(info, "margin_so_mode") == api.ACCOUNT_STOPOUT_MODE_PERCENT,
            margin_call=_float(info, "margin_so_call"),
            stop_out=_float(info, "margin_so_so"),
            margin=_float(info, "margin"),
            margin_level=_float(info, "margin_level"),
        )

    @property
    def read_only(self) -> bool:
        """True after an investor-password login: the account cannot trade at all."""
        return not self.trade_allowed

    def stop_out_text(self) -> str:
        unit = "%" if self.stopout_percent else f" {self.currency}"
        return f"margin call {self.margin_call:g}{unit}, stop-out {self.stop_out:g}{unit}"

    def summary(self) -> str:
        return (
            f"{self.login} · {self.name} · {self.company} · {self.server} · {self.kind.value} · "
            f"{self.balance:,.2f} {self.currency} · 1:{self.leverage} · {self.margin_mode.value}"
        )


@dataclass(frozen=True)
class SymbolSpec:
    name: str
    description: str
    digits: int
    point: float
    tick_size: float
    tick_value: float
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level: int
    freeze_level: int
    filling_mode: int
    trade_mode: SymbolTradeMode
    currency_margin: str
    currency_profit: str
    visible: bool

    @classmethod
    def from_mt5(cls, info: Any) -> SymbolSpec:
        return cls(
            name=_str(info, "name"),
            description=_str(info, "description"),
            digits=_int(info, "digits"),
            point=_float(info, "point"),
            tick_size=_float(info, "trade_tick_size"),
            tick_value=_float(info, "trade_tick_value"),
            contract_size=_float(info, "trade_contract_size"),
            volume_min=_float(info, "volume_min"),
            volume_max=_float(info, "volume_max"),
            volume_step=_float(info, "volume_step"),
            stops_level=_int(info, "trade_stops_level"),
            freeze_level=_int(info, "trade_freeze_level"),
            filling_mode=_int(info, "filling_mode"),
            trade_mode=_SYMBOL_TRADE_MODES.get(_int(info, "trade_mode"), SymbolTradeMode.UNKNOWN),
            currency_margin=_str(info, "currency_margin"),
            currency_profit=_str(info, "currency_profit"),
            visible=_bool(info, "visible"),
        )


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    server_time: int
    digits: int = 5

    @property
    def valid(self) -> bool:
        return self.bid > 0 and self.ask > 0

    @classmethod
    def from_mt5(cls, symbol: str, tick: Any, digits: int = 5) -> Quote:
        return cls(symbol, _float(tick, "bid"), _float(tick, "ask"), _int(tick, "time"), digits)

    def text(self) -> str:
        return f"{self.symbol} {self.bid:.{self.digits}f} / {self.ask:.{self.digits}f}"


def server_time_text(server_time: int) -> str:
    """MT5 tick and bar times are broker server time written as if they were UTC."""
    moment = datetime.fromtimestamp(server_time, UTC)
    return moment.strftime("%Y-%m-%d %H:%M:%S") + " (server)"
