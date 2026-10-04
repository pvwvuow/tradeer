"""The health rules (spec E3): plain values in, one check per rule out."""

from app.observability.health import (
    GIB,
    HealthCheck,
    HealthInputs,
    HealthRecorder,
    HealthStatus,
    evaluate,
    overall,
    summary,
)
from app.observability.watchdog import WorkerStatus

NOW = 1_790_000_000.0
OK, WARNING, CRITICAL, UNKNOWN = (
    HealthStatus.OK,
    HealthStatus.WARNING,
    HealthStatus.CRITICAL,
    HealthStatus.UNKNOWN,
)


def healthy(**changes: object) -> HealthInputs:
    values: dict[str, object] = {
        "now": NOW,
        "mt5_state": "connected",
        "algo_trading": True,
        "ping_ms": 40.0,
        "quote_times": (NOW - 5, NOW - 300),
        "clock_measured": True,
        "clock_text": "UTC+2/+3 (US summer time)",
        "sync_state": "up_to_date",
        "pending": 3,
        "disk_free_bytes": 80 * GIB,
        "log_bytes": 0.1 * GIB,
        "workers": (WorkerStatus("ui", 0.4, 10.0, False, 0),),
    }
    values.update(changes)
    return HealthInputs(**values)  # type: ignore[arg-type]


def by_name(inputs: HealthInputs) -> dict[str, HealthCheck]:
    return {check.name: check for check in evaluate(inputs)}


def test_a_healthy_app_is_all_green() -> None:
    checks = evaluate(healthy())
    assert [check.name for check in checks] == [
        "mt5_connected",
        "algo_trading",
        "quotes_fresh",
        "broker_offset",
        "supabase",
        "sync_queue",
        "disk_space",
        "log_size",
        "latency",
        "clock_drift",
        "workers",
    ]
    assert {check.status for check in checks} == {OK}
    assert overall(checks) is OK and summary(checks) == "Health: all checks OK"
    assert summary([]) == "Health: not checked yet"


def test_without_mt5_the_mt5_checks_wait() -> None:
    found = by_name(healthy(mt5_state="disconnected"))
    assert found["mt5_connected"].status is WARNING and found["mt5_connected"].fix
    for name in ("algo_trading", "quotes_fresh", "latency", "clock_drift"):
        assert found[name].status is UNKNOWN, name
    assert by_name(healthy(mt5_state="reconnecting"))["mt5_connected"].status is CRITICAL


def test_algo_trading_off_or_api_disabled_is_a_warning() -> None:
    off = by_name(healthy(algo_trading=False))["algo_trading"]
    assert off.status is WARNING and "Algo Trading" in off.fix
    api = by_name(healthy(trade_api_disabled=True))["algo_trading"]
    assert api.status is WARNING and "API" in api.text


def test_quote_age_and_the_weekend() -> None:
    assert by_name(healthy(quote_times=(NOW - 400,)))["quotes_fresh"].status is WARNING
    stale = by_name(healthy(quote_times=(NOW - 2000,)))["quotes_fresh"]
    assert stale.status is CRITICAL and stale.value == 2000
    closed = by_name(healthy(quote_times=(NOW - 90_000,), market_closed=True))["quotes_fresh"]
    assert closed.status is OK and "weekend" in closed.text
    assert by_name(healthy(quote_times=()))["quotes_fresh"].status is UNKNOWN


def test_broker_offset_jumps_and_assumed_clock() -> None:
    jumped = by_name(healthy(clock_changes=(NOW - 3600, NOW - 3 * 86_400)))["broker_offset"]
    assert jumped.status is WARNING and jumped.value == 1
    old = by_name(healthy(clock_changes=(NOW - 3 * 86_400,)))["broker_offset"]
    assert old.status is OK
    assert by_name(healthy(clock_measured=False))["broker_offset"].status is UNKNOWN


def test_a_tick_from_the_future_means_the_pc_clock_is_behind() -> None:
    assert by_name(healthy(quote_times=(NOW + 10,)))["clock_drift"].status is OK
    behind = by_name(healthy(quote_times=(NOW + 45,)))["clock_drift"]
    assert behind.status is WARNING and "45 s behind" in behind.text and behind.fix
    assert by_name(healthy(quote_times=(NOW + 300,)))["clock_drift"].status is CRITICAL
    assert by_name(healthy(clock_measured=False))["clock_drift"].status is UNKNOWN


def test_ping_thresholds() -> None:
    assert by_name(healthy(ping_ms=400.0))["latency"].status is WARNING
    assert by_name(healthy(ping_ms=1500.0))["latency"].status is CRITICAL
    assert by_name(healthy(ping_ms=None))["latency"].status is UNKNOWN


def test_cloud_sync_and_the_queue() -> None:
    assert by_name(healthy(sync_state="disabled"))["supabase"].status is UNKNOWN
    offline = by_name(healthy(sync_state="offline", sync_message="No connection"))["supabase"]
    assert offline.status is WARNING and offline.text == "No connection"
    assert by_name(healthy(pending=5_000))["sync_queue"].status is WARNING
    assert by_name(healthy(pending=50_000))["sync_queue"].status is CRITICAL
    refused = by_name(healthy(failed=2))["sync_queue"]
    assert refused.status is WARNING and "Retry failed" in refused.fix


def test_disk_logs_and_workers() -> None:
    assert by_name(healthy(disk_free_bytes=3 * GIB))["disk_space"].status is WARNING
    assert by_name(healthy(disk_free_bytes=0.5 * GIB))["disk_space"].status is CRITICAL
    assert by_name(healthy(disk_free_bytes=None))["disk_space"].status is UNKNOWN
    assert by_name(healthy(log_bytes=2 * GIB))["log_size"].status is WARNING
    frozen = (WorkerStatus("cloud-sync", 700.0, 600.0, True, 1),)
    stuck = by_name(healthy(workers=frozen))["workers"]
    assert stuck.status is CRITICAL and "cloud-sync" in stuck.text
    assert by_name(healthy(workers=()))["workers"].status is UNKNOWN


def test_overall_and_summary_name_the_problems() -> None:
    checks = evaluate(healthy(algo_trading=False, disk_free_bytes=0.5 * GIB))
    assert overall(checks) is CRITICAL
    assert summary(checks) == "Health: 2 issue(s): Algo Trading on, Disk space"


def test_value_text() -> None:
    assert HealthCheck("a", "A", OK, "", 3.0, "rows").value_text() == "3 rows"
    assert HealthCheck("a", "A", OK, "", 0.25, "GB").value_text() == "0.2 GB"
    assert HealthCheck("a", "A", OK, "", 1234.5, "ms").value_text() == "1,234 ms"
    assert HealthCheck("a", "A", OK, "").value_text() == ""


def test_the_recorder_saves_changes_and_a_snapshot_every_15_minutes() -> None:
    recorder = HealthRecorder()
    good = evaluate(healthy())
    assert len(recorder.select(good, NOW)) == len(good)
    assert recorder.select(good, NOW + 60) == []
    bad = evaluate(healthy(algo_trading=False))
    assert [check.name for check in recorder.select(bad, NOW + 120)] == ["algo_trading"]
    assert len(recorder.select(bad, NOW + 901)) == len(bad)
    changes = recorder.changes(good, bad)
    assert [(old.status if old else None, new.status) for old, new in changes] == [(OK, WARNING)]
    assert len(recorder.changes([], good)) == len(good)
