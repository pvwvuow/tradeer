import time

import pytest

from app.mt5.checklist import ConnectRequest
from app.mt5.connection import Backoff, ConnectionService, ConnectionState, ConnectionStatus
from app.mt5.errors import MT5Timeout
from app.mt5.gateway import MT5Gateway
from tests.fakes.fake_mt5 import DEFAULT_PATH, FakeAccount, FakeMT5


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def request(password: str = FakeAccount().password) -> ConnectRequest:
    account = FakeAccount()
    return ConnectRequest(DEFAULT_PATH, account.login, password, account.server)


class Harness:
    def __init__(self, fake: FakeMT5, password: str = FakeAccount().password) -> None:
        self.fake = fake
        self.clock = FakeClock()
        self.events: list[tuple[str, str]] = []
        self.statuses: list[ConnectionStatus] = []
        self.gateway = MT5Gateway(lambda: fake, default_timeout=5.0, idle_seconds=0.05)
        self.gateway.start()
        self.service = ConnectionService(
            self.gateway,
            lambda: request(password),
            log=lambda level, message: self.events.append((level, message)),
            backoff=Backoff(initial_seconds=2.0, factor=2.0, maximum_seconds=10.0),
            heartbeat_seconds=5.0,
            clock=self.clock,
            path_exists=lambda path: True,
        )
        self.service.add_listener(self.statuses.append)

    def advance(self, seconds: float) -> None:
        self.clock.now += seconds
        self.service.poll()

    def close(self) -> None:
        self.gateway.stop()


def test_backoff_grows_exponentially_up_to_the_maximum() -> None:
    backoff = Backoff(initial_seconds=2, factor=2, maximum_seconds=30)
    assert [backoff.delay(attempt) for attempt in range(1, 7)] == [2, 4, 8, 16, 30, 30]


def test_connect_reports_the_account_and_status_bar_text() -> None:
    harness = Harness(FakeMT5())
    try:
        report = harness.service.connect()
    finally:
        harness.close()
    assert report.connected
    status = harness.service.status
    assert status.state is ConnectionState.CONNECTED
    assert status.status_bar_text() == "MT5: DemoBroker-Server · 51234567 · DEMO"
    assert [s.state for s in harness.statuses][:2] == [
        ConnectionState.CONNECTING,
        ConnectionState.CONNECTED,
    ]
    assert harness.events[-1][0] == "INFO"
    assert FakeAccount().password not in str(harness.events)


def test_a_failed_first_connection_is_not_retried() -> None:
    harness = Harness(FakeMT5(), password="wrong-password")
    try:
        harness.service.connect()
        assert harness.service.status.state is ConnectionState.FAILED
        calls = len(harness.fake.calls)
        harness.advance(60)
        assert len(harness.fake.calls) == calls
    finally:
        harness.close()
    assert "wrong login number" in harness.service.status.message


def test_a_lost_terminal_is_reconnected_with_backoff_and_open_positions_alert() -> None:
    fake = FakeMT5(open_positions=2)
    harness = Harness(fake)
    try:
        harness.service.connect()
        harness.advance(5)
        assert harness.service.status.open_positions == 2
        fake.terminal_running = False
        fake.initialize_error = (-10003, "IPC initialize failed")
        harness.advance(5)
        status = harness.service.status
        assert status.state is ConnectionState.RECONNECTING
        assert status.attempt == 1 and status.retry_in_seconds == 2.0
        assert "CRITICAL" in [level for level, _ in harness.events]
        harness.advance(1)
        assert harness.service.status.attempt == 1
        harness.advance(1)
        assert harness.service.status.attempt == 2
        assert harness.service.status.retry_in_seconds == 4.0
        fake.initialize_error = None
        harness.advance(4)
        status = harness.service.status
    finally:
        harness.close()
    assert status.state is ConnectionState.CONNECTED
    assert status.message == "Reconnected"
    assert any("Reconnected to MT5 after 2" in message for _, message in harness.events)


def test_broker_disconnects_are_detected_by_the_heartbeat() -> None:
    fake = FakeMT5()
    harness = Harness(fake)
    try:
        harness.service.connect()
        fake.broker_connected = False
        harness.advance(5)
        state = harness.service.status.state
    finally:
        harness.close()
    assert state is ConnectionState.RECONNECTING
    assert "no broker connection" in harness.service.status.message


def test_disconnect_stops_monitoring_and_shuts_mt5_down() -> None:
    harness = Harness(FakeMT5())
    try:
        harness.service.connect()
        harness.service.disconnect()
        calls = len(harness.fake.calls)
        harness.advance(30)
    finally:
        harness.close()
    assert harness.service.status.state is ConnectionState.DISCONNECTED
    assert "shutdown" in harness.fake.calls
    assert len(harness.fake.calls) == calls + 1  # only the final shutdown from close()


def test_a_frozen_terminal_times_out_instead_of_hanging() -> None:
    fake = FakeMT5(hang_seconds=1.0)
    harness = Harness(fake)
    harness.service = ConnectionService(
        harness.gateway,
        lambda: ConnectRequest(DEFAULT_PATH, timeout_ms=100),
        clock=harness.clock,
    )
    try:
        with pytest.raises(MT5Timeout):
            harness.service.gateway.run("init", lambda mt5: mt5.initialize(), timeout=0.1)
    finally:
        harness.close()


def test_the_monitor_thread_starts_and_stops() -> None:
    harness = Harness(FakeMT5())
    try:
        harness.service.start_monitor(interval_seconds=0.01)
        harness.service.start_monitor(interval_seconds=0.01)
        harness.service.stop_monitor()
    finally:
        harness.close()


def test_a_heartbeat_stuck_behind_a_long_request_is_postponed_not_lost() -> None:
    harness = Harness(FakeMT5())
    harness.service = ConnectionService(
        harness.gateway,
        request,
        log=lambda level, message: harness.events.append((level, message)),
        heartbeat_seconds=5.0,
        heartbeat_timeout=0.2,
        clock=harness.clock,
        path_exists=lambda path: True,
    )
    try:
        harness.service.connect()
        slow = harness.gateway.submit("history_import", lambda mt5: time.sleep(1.0))
        time.sleep(0.1)
        harness.advance(6)
        assert harness.service.status.state is ConnectionState.CONNECTED
        assert any("Heartbeat postponed" in message for _, message in harness.events)
        slow.result(timeout=5)
        harness.advance(6)
        assert harness.service.status.state is ConnectionState.CONNECTED
    finally:
        harness.close()
