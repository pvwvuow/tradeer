"""Signals, the state machine (spec C5), probability baseline and decision traces."""

import math

import pytest

from app.domain.probability import baseline, cost_in_r, expected_value_r, wilson_interval
from app.domain.signals import TRANSITIONS, Direction, InvalidTransition, SignalState, signal_id
from app.observability.decision_trace import REQUIRED_STAGES, DecisionTrace, TraceStep
from tests.unit.signal_helpers import MORNING, make_signal


def test_signal_ids_are_stable_per_symbol_strategy_and_bar() -> None:
    first = signal_id("s", "h", "eurusd", "M15", 100)
    assert first == signal_id("s", "h", "EURUSD", "M15", 100)
    assert first != signal_id("s", "h", "EURUSD", "M15", 1000)
    assert first != signal_id("s", "other", "EURUSD", "M15", 100)


def test_prices_risk_and_summary() -> None:
    signal = make_signal()
    assert signal.valid_prices()
    assert math.isclose(signal.risk, 0.001) and math.isclose(signal.rr, 2.0)
    assert signal.summary() == "Buy EURUSD at 1.10000, SL 1.09900, TP 1.10200 (R:R 2.0)"
    assert not make_signal(sl=1.101).valid_prices()
    assert not make_signal(direction=Direction.SHORT).valid_prices()


def test_the_state_machine_allows_only_the_spec_transitions() -> None:
    signal = make_signal()
    assert signal.state is SignalState.NEW
    waiting = signal.with_state(SignalState.PENDING_APPROVAL, MORNING, "all filters passed")
    rejected = waiting.with_state(SignalState.USER_REJECTED, MORNING + 60, "dismissed")
    assert rejected.state.final and len(rejected.history) == 2
    assert "PENDING_APPROVAL -> USER_REJECTED" in rejected.history[-1].text()
    with pytest.raises(InvalidTransition):
        signal.with_state(SignalState.SENT, MORNING, "skips approval")
    with pytest.raises(InvalidTransition):
        rejected.with_state(SignalState.APPROVED, MORNING, "already final")
    path = [
        SignalState.PENDING_APPROVAL,
        SignalState.APPROVED,
        SignalState.SENT,
        SignalState.FILLED,
        SignalState.MANAGED,
        SignalState.CLOSED,
    ]
    current = signal
    for state in path:
        current = current.with_state(state, MORNING, "next")
    assert current.state is SignalState.CLOSED
    finals = {state for state, targets in TRANSITIONS.items() if not targets}
    assert SignalState.FILTERED_OUT in finals and SignalState.EXPIRED in finals
    assert SignalState.FILLED.open_position and not SignalState.CLOSED.open_position


def test_wilson_interval_and_the_baseline() -> None:
    low, high = wilson_interval(50, 100)
    assert math.isclose(low, 0.4038, abs_tol=1e-3) and math.isclose(high, 0.5962, abs_tol=1e-3)
    assert wilson_interval(0, 0) == (0.0, 1.0)
    unknown = baseline(10, 20)
    assert not unknown.known and "20 of 30" in unknown.text()
    known = baseline(18, 30)
    assert known.known and known.source == "baseline"
    assert known.text().startswith("60% \u00b1")


def test_expected_value_in_r() -> None:
    assert math.isclose(expected_value_r(0.5, 2.0), 0.5)
    assert math.isclose(expected_value_r(0.5, 2.0, 0.1), 0.4)
    assert math.isclose(cost_in_r(0.0002, 0.001), 0.2)
    assert math.isnan(cost_in_r(0.0002, 0.0))


def test_a_trace_is_complete_only_with_every_stage_and_a_decision() -> None:
    trace = DecisionTrace("signal", "trace")
    for stage in REQUIRED_STAGES[:-1]:
        trace.add(stage, stage, True)
    assert trace.missing_stages() == ["decision"] and not trace.complete
    trace.add("decision", "PENDING_APPROVAL", True)
    assert not trace.complete  # no final decision yet
    trace.final_decision = "PENDING_APPROVAL"
    assert trace.complete
    trace.add("filter", "spread", False, value=float("nan"), threshold=0.25)
    assert [step.name for step in trace.failed()] == ["spread"]
    assert trace.lines()[-1] == "Decision: PENDING_APPROVAL"


def test_trace_steps_survive_json() -> None:
    step = TraceStep("filter", "spread vs ATR", False, 0.31, 0.25, "too wide", MORNING)
    again = TraceStep.from_dict(step.to_dict())
    assert again == step
    assert step.text() == "\u2717 filter: spread vs ATR = 0.31 (limit 0.25). too wide"
    assert TraceStep("x", "y", None, float("inf")).to_dict()["value"] is None
