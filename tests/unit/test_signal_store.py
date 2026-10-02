"""Signals and their traces in the database (spec E2): one transaction, stable ids."""

from app.domain.signals import SignalState
from app.storage import outbox
from app.storage.signal_store import SignalRepository, config_id, trace_row_id
from tests.unit.signal_helpers import MORNING, make_record, pending
from tests.unit.storage_helpers import temporary_store


def test_a_signal_is_saved_with_its_trace_and_config() -> None:
    with temporary_store() as store:
        repository = SignalRepository(store)
        record = pending(make_record())
        assert repository.save(record, {"reward_r": 2.0})
        signal = record.signal
        row = store.get("signals", signal.id)
        assert row is not None and row["state"] == "PENDING_APPROVAL"
        assert row["probability_source"] == "none" and row["win_probability"] is None
        assert row["trace_id"] == "trace-1"
        trace = store.get("decision_traces", trace_row_id(signal.id))
        assert trace is not None and trace["final_decision"] == "PENDING_APPROVAL"
        config = store.get("strategy_configs", config_id("trend_pullback", "1.0.0", "hash"))
        assert config is not None and '"reward_r":2.0' in config["params_json"]
        assert repository.exists(signal.id) and not repository.exists("other")
        tables = {item.table for item in outbox.pending(store.db.connection(), 100)}
        assert {"signals", "decision_traces", "strategy_configs"} <= tables


def test_saving_the_same_signal_again_changes_nothing() -> None:
    with temporary_store() as store:
        repository = SignalRepository(store)
        record = pending(make_record())
        assert repository.save(record, {})
        assert not repository.save(record, {})
        assert store.count("signals") == 1 and store.count("decision_traces") == 1


def test_recent_signals_read_back_exactly() -> None:
    with temporary_store() as store:
        repository = SignalRepository(store)
        record = pending(make_record())
        repository.save(record, {})
        older = make_record(id="older", bar_time=MORNING - 3600, created_at=float(MORNING - 2700))
        repository.save(older, {})
        found = repository.recent()
        assert [item.id for item in found] == [record.id, "older"]
        again = found[0]
        assert again.signal == record.signal
        assert again.trace.steps == record.trace.steps
        assert again.trace.final_decision == "PENDING_APPROVAL"
        assert again.signal.history[0].new is SignalState.PENDING_APPROVAL
        assert again.probability.samples == 0 and again.probability.min_samples == 30
        assert repository.recent(account="someone-else") == []


def test_results_come_from_trades_linked_to_signals() -> None:
    with temporary_store() as store:
        repository = SignalRepository(store)
        record = pending(make_record())
        repository.save(record, {})
        store.upsert(
            "trades",
            {
                "id": "trade-1",
                "signal_id": record.signal.id,
                "symbol": "EURUSD",
                "outcome": "loss",
                "close_time": "2026-09-30T11:00:00.000Z",
            },
        )
        found = repository.results("trend_pullback")
        assert [(item.symbol, item.outcome) for item in found] == [("EURUSD", "loss")]
        assert repository.results("london_breakout") == []
