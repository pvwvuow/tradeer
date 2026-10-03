"""The strategy plugin interface (spec C4).

A strategy is pure: the same `MarketContext` always gives the same `Evaluation`. Besides the
optional signal, the evaluation lists every rule it checked (value, threshold, pass or fail)
and the setup state ("forming" or "ready") for the opportunity scanner, so the decision
trace can show why a strategy did or did not fire.
"""

from __future__ import annotations

import hashlib
import json
import math
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel

from app.analysis.bars import TF_SECONDS
from app.domain.signals import Direction, OrderType, Signal, signal_id
from app.observability.decision_trace import TraceStep
from app.strategies.context import MarketContext

EXAMPLE_NOTE = "Example strategy with exact rules. It is not proven to be profitable."


class SetupState(StrEnum):
    NONE = "none"
    FORMING = "forming"
    READY = "ready"

    @property
    def rank(self) -> int:
        return {SetupState.NONE: 0, SetupState.FORMING: 1, SetupState.READY: 2}[self]


Number = float | int | str | None


@dataclass(frozen=True)
class Condition:
    name: str
    passed: bool
    value: Number = None
    threshold: Number = None
    detail: str = ""

    def step(self, at: float) -> TraceStep:
        value = self.value
        if isinstance(value, float) and not math.isfinite(value):
            value = None
        return TraceStep("strategy", self.name, self.passed, value, self.threshold, self.detail, at)


@dataclass(frozen=True)
class Evaluation:
    strategy: str
    symbol: str
    timeframe: str
    bar_time: int
    state: SetupState
    conditions: tuple[Condition, ...]
    signals: tuple[Signal, ...] = ()
    note: str = ""

    @property
    def signal(self) -> Signal | None:
        return self.signals[0] if self.signals else None

    @property
    def passed(self) -> int:
        return sum(1 for condition in self.conditions if condition.passed)

    def steps(self, at: float) -> list[TraceStep]:
        return [condition.step(at) for condition in self.conditions]


def params_hash(name: str, version: str, params: BaseModel) -> str:
    raw = json.dumps(
        {"strategy": name, "version": version, "params": params.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class Strategy(ABC):
    name: ClassVar[str]
    version: ClassVar[str]  # bump when the logic changes
    title: ClassVar[str]
    description: ClassVar[str]
    params_model: ClassVar[type[BaseModel]]
    entry_timeframe: ClassVar[str]
    required_history: ClassVar[Mapping[str, int]]
    sessions: ClassVar[tuple[str, ...]] = ()  # sessions in which new entries are allowed
    example: ClassVar[bool] = True

    def __init__(self, params: BaseModel | None = None) -> None:
        self.params = params if params is not None else self.params_model()
        self.params_hash = params_hash(self.name, self.version, self.params)

    @abstractmethod
    def evaluate(self, ctx: MarketContext) -> Evaluation:
        """Every rule on the newest closed entry bar, and the signal(s) if all of them pass."""

    def generate_signal(self, ctx: MarketContext) -> Signal | None:
        return self.evaluate(ctx).signal

    # Helpers for subclasses ------------------------------------------------------------
    def history_condition(self, ctx: MarketContext) -> Condition:
        short = [
            f"{timeframe} {ctx.history(timeframe)}/{needed}"
            for timeframe, needed in self.required_history.items()
            if ctx.history(timeframe) < needed
        ]
        return Condition(
            "enough closed bars",
            not short,
            detail="missing: " + ", ".join(short) if short else "all timeframes loaded",
        )

    def result(
        self,
        ctx: MarketContext,
        state: SetupState,
        conditions: Sequence[Condition],
        signals: Sequence[Signal] = (),
        note: str = "",
    ) -> Evaluation:
        return Evaluation(
            self.name,
            ctx.symbol,
            self.entry_timeframe,
            ctx.bar_time,
            state,
            tuple(conditions),
            tuple(signals),
            note,
        )

    def make_signal(
        self,
        ctx: MarketContext,
        direction: Direction,
        order_type: OrderType,
        *,
        entry: float,
        sl: float,
        tp: float,
        reason: str,
        expires_at: float,
        features: Mapping[str, float | str] | None = None,
    ) -> Signal:
        key = f"{self.params_hash}:{direction.value}"
        return Signal(
            id=signal_id(self.name, key, ctx.symbol, self.entry_timeframe, ctx.bar_time),
            symbol=ctx.symbol,
            timeframe=self.entry_timeframe,
            direction=direction,
            order_type=order_type,
            entry=round(entry, ctx.digits),
            sl=round(sl, ctx.digits),
            tp=round(tp, ctx.digits),
            reason=reason,
            strategy=self.name,
            strategy_version=self.version,
            params_hash=self.params_hash,
            bar_time=ctx.bar_time,
            created_at=float(ctx.close_time),
            expires_at=expires_at,
            digits=ctx.digits,
            features=dict(features or {}),
        )

    def bars_later(self, ctx: MarketContext, count: int) -> float:
        """UTC time `count` entry bars after the signal bar closed."""
        return float(ctx.close_time + count * TF_SECONDS[self.entry_timeframe])


@dataclass(frozen=True)
class StrategyInfo:
    name: str
    version: str
    title: str
    description: str
    entry_timeframe: str
    example: bool
    defaults: Mapping[str, object] = field(default_factory=dict)
