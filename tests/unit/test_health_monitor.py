"""The health monitor (spec E3): runs the rules, logs changes, saves with the policy."""

import threading
from collections.abc import Sequence
from pathlib import Path

from app.engine.health_monitor import HealthMonitor, folder_size
from app.observability.health import HealthCheck, HealthInputs, HealthStatus

NOW = 1_790_000_000.0


class Inputs:
    def __init__(self) -> None:
        self.state = "connected"
        self.fail = False

    def __call__(self) -> HealthInputs:
        if self.fail:
            raise RuntimeError("market watch gone")
        return HealthInputs(
            now=NOW,
            mt5_state=self.state,
            algo_trading=True,
            ping_ms=30.0,
            quote_times=(NOW - 2,),
            clock_measured=True,
            sync_state="up_to_date",
            disk_free_bytes=1e12,
            log_bytes=1e6,
        )


class Saved:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def __call__(self, checks: Sequence[HealthCheck], at: float) -> int:
        self.batches.append([check.name for check in checks])
        return len(checks)


def monitor(inputs: Inputs, saved: Saved, logs: list[str]) -> HealthMonitor:
    return HealthMonitor(
        inputs,
        save=saved,
        log=lambda level, message: logs.append(f"{level} {message}"),
        clock=lambda: NOW,
    )


def test_a_run_evaluates_saves_and_publishes() -> None:
    inputs, saved, logs = Inputs(), Saved(), []
    health = monitor(inputs, saved, logs)
    assert health.snapshot.at == 0.0 and not health.snapshot.green
    seen: list[HealthStatus] = []
    health.add_listener(lambda snapshot: seen.append(snapshot.status))
    snapshot = health.run_once()
    assert snapshot.at == NOW and len(snapshot.checks) == 11
    assert snapshot.status is HealthStatus.OK and snapshot.green
    assert snapshot.text == "Health: all checks OK"
    assert len(saved.batches[0]) == 11 and seen == [HealthStatus.OK]
    assert logs == []  # first run, nothing wrong: nothing to say
    health.run_once()
    assert len(saved.batches) == 1  # nothing changed, no snapshot due


def test_changes_are_logged_with_their_level() -> None:
    inputs, saved, logs = Inputs(), Saved(), []
    health = monitor(inputs, saved, logs)
    health.run_once()
    inputs.state = "reconnecting"
    health.run_once()
    assert any(line.startswith("ERROR Health MT5 connected: critical") for line in logs)
    assert saved.batches[-1][0] == "mt5_connected"
    inputs.state = "connected"
    health.run_once()
    assert any(line.startswith("INFO Health MT5 connected: ok") for line in logs)


def test_a_failing_collector_is_a_warning_not_a_crash() -> None:
    inputs, saved, logs = Inputs(), Saved(), []
    inputs.fail = True
    snapshot = monitor(inputs, saved, logs).run_once()
    assert any("Health inputs incomplete: RuntimeError" in line for line in logs)
    assert snapshot.checks and snapshot.status is HealthStatus.WARNING


def test_a_failing_save_is_logged() -> None:
    logs: list[str] = []

    def broken(checks: Sequence[HealthCheck], at: float) -> None:
        raise OSError("disk full")

    HealthMonitor(Inputs(), save=broken, log=lambda lv, m: logs.append(m)).run_once()
    assert any("Health checks not saved: OSError" in line for line in logs)


def test_the_thread_runs_and_beats_and_check_now_wakes_it() -> None:
    beats = threading.Event()
    runs: list[float] = []
    health = HealthMonitor(
        Inputs(),
        interval_seconds=30.0,
        first_delay_seconds=0.0,
        heartbeat=beats.set,
    )
    health.add_listener(lambda snapshot: runs.append(snapshot.at))
    health.check_now()  # not started: runs inline
    assert len(runs) == 1
    health.start()
    try:
        assert beats.wait(5.0)
        before = len(runs)
        health.check_now()
        deadline = threading.Event()
        for _ in range(50):
            if len(runs) > before:
                break
            deadline.wait(0.1)
        assert len(runs) > before
    finally:
        health.stop()


def test_folder_size(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.log").write_bytes(b"1234")
    (tmp_path / "y.log").write_bytes(b"12")
    assert folder_size(tmp_path) == 6.0
    assert folder_size(tmp_path / "missing") == 0.0
