"""Performance metrics (spec E3, D4): percentiles, budgets, the summary and what is saved."""

import math

from app.observability.health import HealthStatus
from app.observability.metrics import (
    MB,
    LatencyWindow,
    Metric,
    PerfInputs,
    PerfProbes,
    PerfRecorder,
    evaluate_metrics,
    percentile,
    perf_summary,
)

NOW = 1_790_000_000.0
NAMES = [
    "cpu_percent",
    "memory_mb",
    "bar_ms_p50",
    "bar_ms_p95",
    "mt5_ms_p50",
    "mt5_ms_p95",
    "mt5_queue",
    "sync_queue",
    "startup_seconds",
]


def healthy(**changes: object) -> PerfInputs:
    values: dict[str, object] = {
        "now": NOW,
        "cpu_percent": 1.0,
        "memory_bytes": 200 * MB,
        "mt5_ms": [5.0, 8.0, 12.0, 40.0],
        "bar_cycles": [(300.0, 10), (450.0, 10)],
        "mt5_queue": 0,
        "sync_queue": 12,
        "startup_seconds": 3.2,
    }
    values.update(changes)
    return PerfInputs(**values)  # type: ignore[arg-type]


def by_name(metrics: list[Metric]) -> dict[str, Metric]:
    return {metric.name: metric for metric in metrics}


def test_percentiles_interpolate() -> None:
    assert percentile([], 0.5) is None
    assert percentile([math.nan], 0.5) is None
    assert percentile([7.0], 0.95) == 7.0
    assert percentile([4.0, 1.0, 3.0, 2.0], 0.5) == 2.5
    found = percentile([1.0, 2.0, 3.0, 4.0], 0.95)
    assert found is not None and abs(found - 3.85) < 1e-9


def test_the_latency_window_is_bounded_and_ignores_nonsense() -> None:
    window = LatencyWindow(size=3)
    for value in (1.0, -5.0, math.inf, 2.0, 3.0, 4.0):
        window.add(value)
    assert window.values() == [2.0, 3.0, 4.0]
    probes = PerfProbes()
    probes.mt5_call(12.5)
    probes.bar_cycle(250.0, 0)
    assert probes.mt5.values() == [12.5] and probes.bars.samples() == [(250.0, 1)]


def test_everything_within_the_budgets() -> None:
    metrics = evaluate_metrics(healthy())
    assert [metric.name for metric in metrics] == NAMES
    assert all(metric.status is HealthStatus.OK for metric in metrics), metrics
    assert perf_summary(metrics) == "Performance: within the budgets"
    found = by_name(metrics)
    assert found["memory_mb"].value_text() == "200 MB"
    assert found["startup_seconds"].value_text() == "3.2 s"
    assert found["bar_ms_p95"].budget == "< 1 s per 10 symbols"


def test_over_a_budget_is_a_warning() -> None:
    metrics = evaluate_metrics(
        healthy(
            cpu_percent=4.5,
            memory_bytes=650 * MB,
            mt5_ms=[20.0] * 18 + [1500.0, 2500.0],
            mt5_queue=25,
            sync_queue=5_000,
            startup_seconds=7.0,
        ),
    )
    found = by_name(metrics)
    for name in ("cpu_percent", "memory_mb", "mt5_ms_p95", "mt5_queue", "sync_queue"):
        assert found[name].status is HealthStatus.WARNING, name
    assert found["startup_seconds"].over and found["mt5_ms_p50"].status is HealthStatus.OK
    assert "backtest" in found["cpu_percent"].text
    assert all(metric.status is not HealthStatus.CRITICAL for metric in metrics)
    summary = perf_summary(metrics)
    assert summary.startswith("Performance: 6 over budget: CPU, Memory, MT5 call p95")
    assert summary.endswith("and 3 more")


def test_the_bar_budget_grows_with_the_symbols() -> None:
    slow = by_name(evaluate_metrics(healthy(bar_cycles=[(1500.0, 10)] * 5)))
    assert slow["bar_ms_p95"].over and slow["bar_ms_p50"].over
    many = by_name(evaluate_metrics(healthy(bar_cycles=[(1500.0, 20)] * 5)))
    assert not many["bar_ms_p95"].over and many["bar_ms_p95"].value == 1500.0
    assert "up to 20 symbol(s)" in many["bar_ms_p95"].text


def test_nothing_measured_yet() -> None:
    metrics = evaluate_metrics(PerfInputs(now=NOW))
    assert all(metric.status is HealthStatus.UNKNOWN for metric in metrics)
    assert perf_summary(metrics) == "Performance: not measured yet"
    assert by_name(metrics)["cpu_percent"].value_text() == "n/a"


def test_the_recorder_saves_changes_and_a_snapshot_every_15_minutes() -> None:
    recorder = PerfRecorder()
    first = evaluate_metrics(healthy())
    assert [metric.name for metric in recorder.select(first, NOW)] == NAMES
    assert recorder.select(first, NOW + 60) == []
    slower = evaluate_metrics(healthy(memory_bytes=900 * MB))
    assert [metric.name for metric in recorder.select(slower, NOW + 120)] == ["memory_mb"]
    assert len(recorder.select(slower, NOW + 15 * 60)) == len(NAMES)
    changes = recorder.changes(first, slower)
    assert [(old.name if old else None, new.name) for old, new in changes] == [
        ("memory_mb", "memory_mb"),
    ]
    unknown = evaluate_metrics(PerfInputs(now=NOW))
    assert PerfRecorder().select(unknown, NOW) == []  # nothing measured, nothing saved
