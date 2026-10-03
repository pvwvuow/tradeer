"""Market reads shared by the live and the paper broker: the current bid and ask, the symbol
specification, the account balance and `order_calc_profit`. Read-only; run in the gateway
thread, called from the analysis thread.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol

from app.domain.signals import Direction
from app.mt5.api import MT5Api
from app.mt5.gateway import MT5Gateway
from app.mt5.models import SymbolSpec
from app.mt5.risk_reads import order_action

READ_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    time: int  # broker server time of the tick

    @property
    def valid(self) -> bool:
        return self.bid > 0 and self.ask > 0 and self.ask >= self.bid


class MarketReads(Protocol):
    def quote(self, symbol: str) -> Quote | None: ...

    def spec(self, symbol: str) -> SymbolSpec | None: ...

    def balance(self) -> float | None: ...

    def profit(
        self,
        symbol: str,
        direction: Direction,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None: ...


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def read_quote(mt5: MT5Api, symbol: str) -> Quote | None:
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    bid = _finite(getattr(tick, "bid", None)) or 0.0
    ask = _finite(getattr(tick, "ask", None)) or 0.0
    found = Quote(bid, ask, int(_finite(getattr(tick, "time", 0)) or 0))
    return found if found.valid else None


def read_spec(mt5: MT5Api, symbol: str) -> SymbolSpec | None:
    info = mt5.symbol_info(symbol)
    return SymbolSpec.from_mt5(info) if info is not None else None


def read_balance(mt5: MT5Api) -> float | None:
    info = mt5.account_info()
    return _finite(getattr(info, "balance", None)) if info is not None else None


class GatewayMarket:
    """`MarketReads` through the MT5 gateway thread."""

    def __init__(self, gateway: MT5Gateway, timeout: float = READ_TIMEOUT_SECONDS) -> None:
        self._gateway = gateway
        self._timeout = timeout

    def quote(self, symbol: str) -> Quote | None:
        found: Quote | None = self._gateway.run(
            "symbol_info_tick",
            lambda mt5: read_quote(mt5, symbol),
            timeout=self._timeout,
            arguments={"symbol": symbol},
        )
        return found

    def spec(self, symbol: str) -> SymbolSpec | None:
        found: SymbolSpec | None = self._gateway.run(
            "symbol_info",
            lambda mt5: read_spec(mt5, symbol),
            timeout=self._timeout,
            arguments={"symbol": symbol},
        )
        return found

    def balance(self) -> float | None:
        found: float | None = self._gateway.run("account_info", read_balance, timeout=self._timeout)
        return found

    def profit(
        self,
        symbol: str,
        direction: Direction,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None:
        def work(mt5: MT5Api) -> float | None:
            action = order_action(direction)
            return _finite(mt5.order_calc_profit(action, symbol, volume, price_open, price_close))

        found: float | None = self._gateway.run(
            "order_calc_profit",
            work,
            timeout=self._timeout,
            arguments={"symbol": symbol, "volume": volume},
        )
        return found
