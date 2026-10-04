"""Performance metrics in the synced table and the decision traces for the debug bundle."""

from app.observability.health import HealthStatus
from app.observability.metrics import Metric
from app.storage.ids import new_id
from app.storage.perf_store import PerfRepository, recent_traces
from tests.unit.storage_helpers import temporary_store

NOW = 1_790_000_000.0


def test_measured_metrics_are_saved_and_read_back() -> None:
    metrics = [
        Metric("memory_mb", "Memory", 320.0, "MB", "< 500 MB", HealthStatus.OK, "RAM"),
        Metric("cpu_percent", "CPU", None, "%", "< 3 %", HealthStatus.UNKNOWN),
        Metric("mt5_ms_p95", "MT5 call p95", 1400.0, "ms", "< 1,000 ms", HealthStatus.WARNING),
    ]
    with temporary_store() as store:
        repository = PerfRepository(store, lambda: None)
        assert repository.record(metrics, NOW) == 2  # the unmeasured CPU is skipped
        assert repository.record(metrics[:1], NOW + 60) == 1
        everything = repository.recent()
        memory = repository.recent(name="memory_mb")
        assert store.count("performance_metrics") == 3
        assert store.outbox_counts().pending >= 3
    assert [item.name for item in memory] == ["memory_mb", "memory_mb"]
    assert memory[0].time == NOW + 60 and memory[0].unit == "MB" and memory[0].value == 320.0
    slow = next(item for item in everything if item.name == "mt5_ms_p95")
    assert slow.status == "warning" and slow.value == 1400.0


def test_the_newest_decision_traces_come_with_their_steps() -> None:
    with temporary_store() as store:
        for number in range(3):
            store.upsert(
                "decision_traces",
                {
                    "id": new_id(),
                    "signal_id": new_id(),
                    "trace_id": f"trace-{number}",
                    "steps_json": [{"stage": "decision", "name": "final", "passed": True}],
                    "final_decision": "taken" if number else "rejected",
                },
            )
        traces = recent_traces(store, 2)
    assert len(traces) == 2
    assert {item["trace_id"] for item in traces} <= {"trace-0", "trace-1", "trace-2"}
    assert traces[0]["steps"] == [{"stage": "decision", "name": "final", "passed": True}]
    assert traces[0]["final_decision"] in ("taken", "rejected")
