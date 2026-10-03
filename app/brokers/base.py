"""The `Broker` interface (spec C8, D3.4): the live MT5 broker and the paper broker give the
execution engine the same operations, so live and paper share every other line of code.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.brokers.requests import OrderPlan
from app.domain.history import Deal
from app.domain.signals import Direction


@dataclass(frozen=True)
class Attempt:
    """One request to the trade server and its answer (table `mt5_requests`)."""

    action: str  # the MT5 function that was called
    request: Mapping[str, Any]
    retcode: int | None
    retcode_text: str
    comment: str
    latency_ms: float
    attempt: int
    result: Mapping[str, Any] = field(default_factory=dict)
    last_error: str = ""


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    retcode: int | None
    text: str
    order: int = 0
    deal: int = 0
    position: int = 0
    price: float = math.nan  # the fill price (or the placed pending price)
    volume: float = 0.0
    requested_price: float = math.nan
    attempts: tuple[Attempt, ...] = ()
    placed: bool = False  # a pending order was placed (no position yet)


@dataclass(frozen=True)
class BrokerPosition:
    ticket: int
    symbol: str
    direction: Direction
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float
    swap: float
    magic: int
    comment: str
    time: int  # broker server time of the open


@dataclass(frozen=True)
class BrokerOrder:
    ticket: int
    symbol: str
    direction: Direction
    pending_type: int  # ORDER_TYPE_*
    volume: float
    price: float
    sl: float
    tp: float
    magic: int
    comment: str
    expiration: int  # broker server time, 0 = none


class Broker(Protocol):
    @property
    def mode(self) -> str: ...  # "live" or "paper"

    def open(self, plan: OrderPlan) -> OrderResult: ...

    def modify(self, position: BrokerPosition, sl: float, tp: float) -> OrderResult: ...

    def close(self, position: BrokerPosition, volume: float | None = None) -> OrderResult: ...

    def cancel(self, order: BrokerOrder) -> OrderResult: ...

    def positions(self, magics: Collection[int]) -> list[BrokerPosition] | None:
        """The bot's open positions, or None when they could not be read."""
        ...

    def orders(self, magics: Collection[int]) -> list[BrokerOrder] | None: ...

    def deals(self, position: int) -> Sequence[Deal] | None: ...
