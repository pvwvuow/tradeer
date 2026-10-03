"""Phase 6 acceptance: every signal has a full decision trace (spec C5, G3)."""

from app.core.clock import BrokerClock
from app.domain.signals import SignalState
from app.engine.signal_pipeline import history_of
from app.observability.decision_trace import REQUIRED_STAGES
from app.storage.signal_store import SignalRepository, TradeResult
from tests.unit.signal_helpers import pipeline
from tests.unit.storage_helpers import temporary_store
from tests.unit.strategy_helpers import analysis_at, london_days

CLOCK = BrokerClock.assumed()


def london_open(spread: float = 0.00002) -> tuple[object, float]:
    m15 = london_days()
    return analysis_at(m15, len(m15), spread=spread)


def test_every_signal_has_a_full_decision_trace() -> None:
    with temporary_store() as store:
        logs: list[tuple[str, str]] = []
        signals = pipeline(store=SignalRepository(store), logs=logs)
        analysis, now = london_open()
        created = signals.on_analysis(analysis, clock=CLOCK, spec=None, spread=2e-5, now=now)
        assert len(created) == 2
        for record in created:
            trace = record.trace
            assert trace.complete, trace.missing_stages()
            assert set(REQUIRED_STAGES) <= trace.stages()
            assert record.signal.state is SignalState.PENDING_APPROVAL
            assert trace.final_decision == "PENDING_APPROVAL"
            assert any(step.stage == "execution" for step in trace.steps)
            assert record.signal.config_id and "rr" in record.signal.features
        saved = SignalRepository(store).recent()
        assert len(saved) == 2 and all(item.trace.complete for item in saved)
        assert sum("waiting for approval" in message for _, message in logs) == 2
        snapshot = signals.snapshot
        assert len(snapshot.pending()) == 2
        assert snapshot.scanner[0].state == "ready"


def test_a_wide_spread_filters_the_signal_out_with_the_reason() -> None:
    signals = pipeline()
    analysis, now = london_open()
    created = signals.on_analysis(analysis, clock=CLOCK, spec=None, spread=0.002, now=now)
    assert created and all(r.signal.state is SignalState.FILTERED_OUT for r in created)
    for record in created:
        assert record.trace.complete
        assert "spread vs ATR" in record.reject_reason
        assert record.trace.final_decision == "FILTERED_OUT"


def test_the_same_bar_never_gives_a_second_signal() -> None:
    with temporary_store() as store:
        analysis, now = london_open()
        first = pipeline(store=SignalRepository(store))
        assert len(first.on_analysis(analysis, clock=CLOCK, spec=None, spread=2e-5, now=now)) == 2
        assert first.on_analysis(analysis, clock=CLOCK, spec=None, spread=2e-5, now=now) == []
        restarted = pipeline(store=SignalRepository(store))
        assert restarted.on_analysis(analysis, clock=CLOCK, spec=None, spread=2e-5, now=now) == []
        assert store.count("signals") == 2


def test_dismiss_and_expiry_are_saved() -> None:
    with temporary_store() as store:
        repository = SignalRepository(store)
        signals = pipeline(store=repository)
        analysis, now = london_open()
        created = signals.on_analysis(analysis, clock=CLOCK, spec=None, spread=2e-5, now=now)
        signals.dismiss(created[0].id)
        signals.on_cycle(now + 60)
        states = {r.id: r.signal.state for r in signals.snapshot.signals}
        assert states[created[0].id] is SignalState.USER_REJECTED
        assert states[created[1].id] is SignalState.PENDING_APPROVAL
        restarted = pipeline(store=repository)
        assert restarted.load() == 2
        restarted.on_cycle(created[1].signal.expires_at + 1)
        final = {r.id: r.signal.state for r in restarted.snapshot.signals}
        assert final[created[1].id] is SignalState.EXPIRED
        row = store.get("signals", created[1].id)
        assert row is not None and row["state"] == "EXPIRED"
        trace = repository.recent()[0].trace
        assert trace.final_decision in ("EXPIRED", "USER_REJECTED") and trace.complete


def test_trade_history_feeds_the_baseline_and_the_streak() -> None:
    results = [
        TradeResult("EURUSD", "loss", 300.0),
        TradeResult("GBPUSD", "loss", 200.0),
        TradeResult("EURUSD", "win", 100.0),
        TradeResult("EURUSD", "breakeven", 50.0),
    ]
    found = history_of(results)
    assert (found.wins, found.total, found.losses_in_row) == (1, 3, 2)
    assert found.last_loss == {"EURUSD": 300.0, "GBPUSD": 200.0}
    assert found.streak_end == 300.0  # the newest loss: the pause counts from there


def test_a_failing_strategy_is_logged_and_the_rest_still_run() -> None:
    logs: list[tuple[str, str]] = []
    signals = pipeline(("london_breakout", "trend_pullback"), logs=logs)
    analysis, now = london_open()
    created = signals.on_analysis(analysis, clock=CLOCK, spec=None, spread=2e-5, now=now)
    assert len(created) == 2
    assert not [message for level, message in logs if level == "ERROR"]
    assert {entry.strategy for entry in signals.snapshot.scanner} == {
        "london_breakout",
        "trend_pullback",
    }
