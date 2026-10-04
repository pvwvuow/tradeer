"""The soak report reads the saved run from the database and the crash folder."""

import os
from pathlib import Path

from app.observability.health import HealthCheck, HealthStatus
from app.observability.metrics import Metric
from app.storage.health_store import HealthRepository
from app.storage.perf_store import PerfRepository
from app.storage.signal_store import iso_time
from app.storage.soak_store import create_soak_report, soak_inputs
from tests.unit.storage_helpers import temporary_store

START = 1_790_000_000.0
STEP = 900.0
HOURS = 25


def metric(name: str, value: float, status: HealthStatus = HealthStatus.OK) -> Metric:
    return Metric(name, name, value, "", "", status)


def log_entry(moment: float, level: str, message: str) -> dict[str, object]:
    return {
        "time": iso_time(moment),
        "level": level,
        "category": "mt5",
        "module": "app.mt5.connection",
        "function": "_lost",
        "line": 1,
        "session_id": "session-1",
        "message": message,
    }


def test_the_saved_run_becomes_a_report(tmp_path: Path) -> None:
    crashes = tmp_path / "crash_reports"
    crashes.mkdir()
    crash = crashes / "crash_20260922-010000-000001.json"
    crash.write_text("{}", encoding="utf-8")
    os.utime(crash, (START + 3600, START + 3600))
    old = crashes / "crash_20260901-010000-000001.json"
    old.write_text("{}", encoding="utf-8")
    os.utime(old, (START - 10 * 86_400, START - 10 * 86_400))
    end = START + HOURS * 3600
    with temporary_store() as store:
        perf = PerfRepository(store)
        perf.record([metric("memory_mb", 250.0)], START - 3 * 86_400)  # an older run
        moment = START
        while moment <= end:
            perf.record(
                [
                    metric("memory_mb", 250.0),
                    metric("cpu_percent", 0.5),
                    metric("mt5_ms_p95", 12.0),
                    metric("bar_ms_p95", 240.0),
                    metric("sync_queue", 0.0),
                ],
                moment,
            )
            moment += STEP
        lost = "MT5 connection lost (no broker connection); reconnecting"
        store.record_log(log_entry(START + 7200, "WARNING", lost))
        store.record_log(log_entry(START + 7300, "ERROR", "Order check failed"))
        store.record_log(log_entry(START - 86_400, "CRITICAL", "An older run"))
        frozen = HealthCheck("workers", "Background workers", HealthStatus.CRITICAL, "ui")
        HealthRepository(store).record([frozen], START + 600)
        found = soak_inputs(store, crashes, end + 60)
        assert found.start == START and found.end == end and found.longest_gap == STEP
        assert {sample.name for sample in found.samples} == {
            "memory_mb",
            "cpu_percent",
            "mt5_ms_p95",
            "bar_ms_p95",
        }
        assert (found.critical_logs, found.error_logs, found.disconnects) == (0, 1, 1)
        assert (found.frozen_workers, found.crash_reports) == (1, 1)
        result = create_soak_report(store, crashes, tmp_path / "reports", end + 60)
    assert result.path.parent == tmp_path / "reports" and result.path.exists()
    assert not result.report.passed
    assert [check.name for check in result.report.checks if not check.passed] == ["Stability"]
    assert "Soak test FAIL: 6 of 7" in result.text
    assert "| Stability | FAIL |" in result.path.read_text(encoding="utf-8")


def test_an_empty_database_gives_an_empty_run(tmp_path: Path) -> None:
    with temporary_store() as store:
        found = soak_inputs(store, tmp_path / "missing", START)
    assert (found.start, found.end, found.samples) == (0.0, 0.0, ())
