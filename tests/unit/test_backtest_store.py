"""Backtest runs are saved with their costs and results (spec E2 `backtest_runs`)."""

from app.storage.backtest_store import BacktestRepository
from tests.unit.storage_helpers import temporary_store


def test_a_run_is_saved_queued_for_sync_and_read_back() -> None:
    with temporary_store() as store:
        runs = BacktestRepository(store)
        first = runs.save(
            "acc",
            config_id="cfg",
            period="2026-01-01..2026-06-30",
            costs={"spread": 1},
            metrics={"symbol": "EURUSD", "strategies": ["trend_pullback"], "trades": 3},
            monte_carlo={"risk_of_ruin": 0.01},
        )
        second = runs.save("acc", config_id="cfg", period="p2", costs={}, metrics={})
        found = runs.recent()
        assert [run.id for run in found] == [second, first]
        saved = found[1]
        assert saved.metrics["trades"] == 3 and saved.costs == {"spread": 1}
        assert saved.walk_forward is None and saved.monte_carlo == {"risk_of_ruin": 0.01}
        assert saved.title == "EURUSD trend_pullback 2026-01-01..2026-06-30"
        assert store.outbox_counts().pending >= 2
