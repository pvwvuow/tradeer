"""Problems found in the first real debug bundle (0.18.0, 4 Oct), reproduced first (spec I6).

Cloud sync was off, so 4,394 rows waited on the PC and the health check and the metric both
warned about it; and pressing Connect while connected sent a "connection lost" alert.
"""

from app.mt5.connection import ConnectionState, ConnectionStatus
from app.notify.events import connection_notices
from app.observability.health import GIB, HealthInputs, HealthStatus, evaluate
from app.observability.metrics import PerfInputs, evaluate_metrics

NOW = 1_790_000_000.0


def test_waiting_rows_are_no_problem_while_cloud_sync_is_off() -> None:
    off = evaluate(HealthInputs(now=NOW, sync_state="disabled", pending=4_394, disk_free_bytes=GIB))
    check = next(item for item in off if item.name == "sync_queue")
    assert check.status is HealthStatus.UNKNOWN and not check.problem
    assert check.text == "Cloud sync is off: 4,394 row(s) are kept on this PC."
    assert check.value == 4_394
    on = evaluate(HealthInputs(now=NOW, sync_state="up_to_date", pending=4_394))
    assert next(item for item in on if item.name == "sync_queue").status is HealthStatus.WARNING


def test_the_sync_metric_waits_while_cloud_sync_is_off() -> None:
    off = evaluate_metrics(PerfInputs(now=NOW, sync_queue=4_408, sync_enabled=False))
    found = next(metric for metric in off if metric.name == "sync_queue")
    assert found.status is HealthStatus.UNKNOWN and found.value == 4_408
    assert "Cloud sync is off" in found.text and not found.over
    on = evaluate_metrics(PerfInputs(now=NOW, sync_queue=4_408))
    assert next(metric for metric in on if metric.name == "sync_queue").over


def test_pressing_connect_again_is_not_a_lost_connection() -> None:
    up = ConnectionStatus(ConnectionState.CONNECTED)
    again = ConnectionStatus(ConnectionState.CONNECTING, "Connecting to MT5")
    assert connection_notices(up, again) == []
    lost = ConnectionStatus(ConnectionState.RECONNECTING, "Connection lost: no broker connection")
    assert [notice.key for notice in connection_notices(up, lost)] == ["disconnect"]
