"""The performance monitor (spec E3, D4): runs after each health check, logs budget changes,
saves with the policy; the market watch and the MT5 gateway feed its probes."""

from collections.abc import Sequence

from app.engine.health_monitor import HealthMonitor, HealthSnapshot
from app.engine.perf_monitor import PerfMonitor
from app.mt5.gateway import MT5Gateway
from app.observability.health import HealthCheck, HealthStatus
from app.observability.metrics import MB, Metric, PerfInputs
from tests.fakes.fake_mt5 import FakeMT5
from tests.unit.test_health_monitor import Inputs
from tests.unit.test_market_watch import SYMBOLS, watch

NOW = 1_790_000_000.0


class Measure:
    def __init__(self) -> None:
        self.memory = 200 * MB
        self.fail = False

    def __call__(self) -> PerfInputs:
        if self.fail:
            raise RuntimeError("probes gone")
        return PerfInputs(
            now=NOW,
            cpu_percent=1.0,
            memory_bytes=self.memory,
            mt5_ms=[10.0, 20.0],
            bar_cycles=[(200.0, 3)],
            mt5_queue=0,
            sync_queue=0,
            startup_seconds=2.0,
        )


class Saved:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def __call__(self, metrics: Sequence[Metric], at: float) -> int:
        self.batches.append([metric.name for metric in metrics])
        return len(metrics)


def test_a_run_measures_saves_and_logs_budget_changes() -> None:
    measure, saved, logs = Measure(), Saved(), []
    perf = PerfMonitor(
        measure,
        save=saved,
        log=lambda level, message: logs.append(f"{level} {message}"),
        clock=lambda: NOW,
    )
    assert perf.snapshot.at == 0.0 and perf.snapshot.text == "Performance: not measured yet"
    first = perf.run_once()
    assert first.at == NOW and first.over == [] and len(saved.batches[0]) == 9
    assert first.text == "Performance: within the budgets" and logs == []
    measure.memory = 800 * MB
    perf.run_once()
    assert logs == [
        "WARNING Performance Memory: 800 MB is over the budget (< 500 MB). This app's working "
        "set (the RAM it uses). If it keeps growing, restart the app and send a debug bundle.",
    ]
    assert saved.batches[-1] == ["memory_mb"]
    measure.memory = 300 * MB
    perf.run_once()
    assert logs[-1] == "INFO Performance Memory: 300 MB is within the budget again (< 500 MB)."


def test_a_failing_collector_or_save_is_logged_not_raised() -> None:
    measure, logs = Measure(), []
    measure.fail = True

    def broken(metrics: Sequence[Metric], at: float) -> None:
        raise OSError("disk full")

    perf = PerfMonitor(measure, save=broken, log=lambda level, message: logs.append(message))
    snapshot = perf.run_once()
    assert any("Performance inputs incomplete: RuntimeError" in line for line in logs)
    assert all(metric.status is HealthStatus.UNKNOWN for metric in snapshot.metrics)
    measure.fail = False
    perf.run_once()
    assert any("Performance metrics not saved: OSError" in line for line in logs)


def test_the_health_monitor_runs_the_perf_monitor_after_each_check() -> None:
    perf = PerfMonitor(Measure(), clock=lambda: NOW)
    logs: list[str] = []

    def broken() -> None:
        raise ValueError("boom")

    health = HealthMonitor(
        Inputs(),
        clock=lambda: NOW,
        log=lambda level, message: logs.append(message),
        after=(perf.run_once, broken),
    )
    health.check_now()  # not started: runs inline, with the extras
    assert perf.snapshot.at == NOW and len(perf.snapshot.metrics) == 9
    assert any("After the health check: ValueError: boom" in line for line in logs)


def test_the_health_verdict_for_the_go_live_gate() -> None:
    assert HealthSnapshot().go_live_text is None
    ok = HealthCheck("disk_space", "Disk space", HealthStatus.OK, "Plenty")
    unknown = HealthCheck("supabase", "Supabase reachable", HealthStatus.UNKNOWN, "Off")
    low = HealthCheck("disk_space", "Disk space", HealthStatus.WARNING, "2 GB free")
    assert HealthSnapshot((ok, unknown), NOW).go_live_text == ""
    assert HealthSnapshot((low, unknown), NOW).go_live_text == "Health: 1 issue(s): Disk space"


def test_the_market_watch_reports_cycles_with_new_closed_bars() -> None:
    cycles: list[tuple[float, int]] = []
    with watch(on_bars=lambda ms, n: cycles.append((ms, n))) as (watcher, _fake, clock, _logs):
        watcher.cycle()
        assert len(cycles) == 1 and cycles[0][1] == len(SYMBOLS) and cycles[0][0] >= 0
        clock.now += 30  # the same bar: nothing analysed, nothing reported
        watcher.cycle()
        assert len(cycles) == 1
        clock.now += 300
        watcher.cycle()
        assert len(cycles) == 2


def test_the_gateway_tells_its_queue_size() -> None:
    fake = FakeMT5()
    gateway = MT5Gateway(lambda: fake, idle_seconds=0.05)
    assert gateway.queue_size == 0
    gateway.start()
    try:
        assert gateway.queue_size == 0
    finally:
        gateway.stop()
