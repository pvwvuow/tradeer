"""Signals, traces and pipelines for the Phase 6 tests (test code only)."""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.probability import ProbabilityEstimate, baseline
from app.domain.signals import Direction, OrderType, Signal, SignalRecord, SignalState, signal_id
from app.engine.filters import FilterSettings
from app.engine.signal_pipeline import RiskHook, SignalPipeline
from app.observability.decision_trace import REQUIRED_STAGES, DecisionTrace
from app.storage.signal_store import SignalRepository
from app.strategies.base import Strategy
from app.strategies.registry import create_strategy
from tests.unit.strategy_helpers import WEDNESDAY

# Wednesday 2026-09-30 10:00 UTC: London and New York both open.
MORNING = WEDNESDAY + 10 * 3600


def make_signal(**changes: object) -> Signal:
    values: dict[str, object] = {
        "id": signal_id("test", "hash:long", "EURUSD", "M15", MORNING),
        "symbol": "EURUSD",
        "timeframe": "M15",
        "direction": Direction.LONG,
        "order_type": OrderType.MARKET,
        "entry": 1.10000,
        "sl": 1.09900,
        "tp": 1.10200,
        "reason": "test reason",
        "strategy": "trend_pullback",
        "strategy_version": "1.0.0",
        "params_hash": "hash",
        "bar_time": MORNING - 900,
        "created_at": float(MORNING),
        "expires_at": float(MORNING + 1800),
    }
    values.update(changes)
    return Signal(**values)  # type: ignore[arg-type]


def make_record(signal: Signal | None = None, **changes: object) -> SignalRecord:
    found = signal or make_signal(**changes)
    trace = DecisionTrace(found.id, "trace-1")
    for stage in REQUIRED_STAGES:
        trace.add(stage, f"{stage} step", True, value=1.5, detail="ok", at=float(MORNING))
    trace.final_decision = found.state.value
    estimate: ProbabilityEstimate = baseline(0, 0)
    return SignalRecord(found, trace, estimate, None, 0.00002, 0.0008, "")


def pending(record: SignalRecord) -> SignalRecord:
    signal = record.signal.with_state(SignalState.PENDING_APPROVAL, float(MORNING), "test")
    record.trace.final_decision = signal.state.value
    return record.with_signal(signal)


def pipeline(
    names: Sequence[str] = ("london_breakout",),
    *,
    store: SignalRepository | None = None,
    logs: list[tuple[str, str]] | None = None,
    settings: FilterSettings | None = None,
    risk: RiskHook | None = None,
) -> SignalPipeline:
    strategies: list[Strategy] = [create_strategy(name) for name in names]
    found = logs if logs is not None else []
    return SignalPipeline(
        lambda: strategies,
        lambda: settings or FilterSettings(),
        store=store,
        risk=risk,
        log=lambda level, message: found.append((level, message)),
        utc_now=lambda: float(MORNING),
    )
