"""Signals and their life cycle (spec C4, C5): pure data and the state machine, no I/O.

`NEW -> FILTERED_OUT | RISK_REJECTED | PENDING_APPROVAL -> APPROVED | USER_REJECTED | EXPIRED
-> SENT -> FILLED | FAILED -> MANAGED -> CLOSED`. A transition the table does not allow
raises `InvalidTransition`; the pipeline persists and logs every allowed one.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum

from app.domain.probability import ProbabilityEstimate
from app.observability.decision_trace import DecisionTrace
from app.storage.ids import stable_id


class Direction(StrEnum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Direction.LONG else -1


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class SignalState(StrEnum):
    NEW = "NEW"
    FILTERED_OUT = "FILTERED_OUT"
    RISK_REJECTED = "RISK_REJECTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    USER_REJECTED = "USER_REJECTED"
    EXPIRED = "EXPIRED"
    SENT = "SENT"
    FILLED = "FILLED"
    FAILED = "FAILED"
    MANAGED = "MANAGED"
    CLOSED = "CLOSED"

    @property
    def final(self) -> bool:
        return not TRANSITIONS[self]

    @property
    def open_position(self) -> bool:
        """The strategy has (or is about to have) a position for this signal."""
        return self in (SignalState.SENT, SignalState.FILLED, SignalState.MANAGED)


TRANSITIONS: dict[SignalState, frozenset[SignalState]] = {
    SignalState.NEW: frozenset(
        {SignalState.FILTERED_OUT, SignalState.RISK_REJECTED, SignalState.PENDING_APPROVAL},
    ),
    SignalState.PENDING_APPROVAL: frozenset(
        {SignalState.APPROVED, SignalState.USER_REJECTED, SignalState.EXPIRED},
    ),
    SignalState.APPROVED: frozenset({SignalState.SENT}),
    SignalState.SENT: frozenset({SignalState.FILLED, SignalState.FAILED}),
    SignalState.FILLED: frozenset({SignalState.MANAGED}),
    SignalState.MANAGED: frozenset({SignalState.CLOSED}),
    SignalState.FILTERED_OUT: frozenset(),
    SignalState.RISK_REJECTED: frozenset(),
    SignalState.USER_REJECTED: frozenset(),
    SignalState.EXPIRED: frozenset(),
    SignalState.FAILED: frozenset(),
    SignalState.CLOSED: frozenset(),
}


class InvalidTransition(ValueError):
    def __init__(self, old: SignalState, new: SignalState) -> None:
        super().__init__(f"A signal cannot go from {old.value} to {new.value}")
        self.old = old
        self.new = new


@dataclass(frozen=True)
class StateChange:
    old: SignalState
    new: SignalState
    at: float  # UTC seconds
    reason: str

    def text(self) -> str:
        moment = datetime.fromtimestamp(self.at, UTC).strftime("%H:%M:%S UTC")
        return f"{moment}: {self.old.value} -> {self.new.value} ({self.reason})"


def signal_id(strategy: str, params_hash: str, symbol: str, timeframe: str, bar_time: int) -> str:
    """One id per symbol, strategy settings and bar: evaluating the same bar again (a restart,
    a second window) gives the same signal instead of a new one."""
    return stable_id("signal", strategy, params_hash, symbol.upper(), timeframe, int(bar_time))


@dataclass(frozen=True)
class Signal:
    """A trade idea from a strategy (spec C4). Prices are in the symbol's quote currency."""

    id: str
    symbol: str
    timeframe: str
    direction: Direction
    order_type: OrderType
    entry: float
    sl: float
    tp: float
    reason: str
    strategy: str
    strategy_version: str
    params_hash: str
    bar_time: int  # UTC open time of the signal bar
    created_at: float  # UTC close time of the signal bar
    expires_at: float
    digits: int = 5
    config_id: str = ""
    features: Mapping[str, float | str] = field(default_factory=dict)
    state: SignalState = SignalState.NEW
    history: tuple[StateChange, ...] = ()

    @property
    def risk(self) -> float:
        """Distance from the entry to the stop loss, in price."""
        return abs(self.entry - self.sl)

    @property
    def reward(self) -> float:
        return abs(self.tp - self.entry)

    @property
    def rr(self) -> float:
        return self.reward / self.risk if self.risk > 0 else math.nan

    def valid_prices(self) -> bool:
        """SL on the losing side, TP on the winning side, all finite."""
        values = (self.entry, self.sl, self.tp)
        if not all(math.isfinite(value) for value in values) or self.risk <= 0:
            return False
        sign = self.direction.sign
        return (self.entry - self.sl) * sign > 0 and (self.tp - self.entry) * sign > 0

    def price(self, value: float) -> str:
        return f"{value:,.{self.digits}f}"

    def summary(self) -> str:
        side = "Buy" if self.direction is Direction.LONG else "Sell"
        kind = "" if self.order_type is OrderType.MARKET else f" {self.order_type.value}"
        return (
            f"{side}{kind} {self.symbol} at {self.price(self.entry)}, SL {self.price(self.sl)}, "
            f"TP {self.price(self.tp)} (R:R {self.rr:.1f})"
        )

    def with_state(self, new: SignalState, at: float, reason: str) -> Signal:
        """The signal after one transition. Raises `InvalidTransition` if it is not allowed."""
        if new not in TRANSITIONS[self.state]:
            raise InvalidTransition(self.state, new)
        change = StateChange(self.state, new, at, reason)
        return replace(self, state=new, history=(*self.history, change))


@dataclass(frozen=True)
class SignalRecord:
    """A signal with everything the Signals page shows: the numbers and the decision trace."""

    signal: Signal
    trace: DecisionTrace
    probability: ProbabilityEstimate
    expected_value: float | None = None
    spread: float = math.nan
    atr: float = math.nan
    reject_reason: str = ""

    @property
    def id(self) -> str:
        return self.signal.id

    def with_signal(self, signal: Signal) -> SignalRecord:
        return replace(self, signal=signal)
