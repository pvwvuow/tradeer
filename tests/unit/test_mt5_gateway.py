import threading
import time

import pytest

from app.mt5.api import MT5Api
from app.mt5.errors import MT5Error, MT5Timeout, MT5Unavailable
from app.mt5.gateway import THREAD_NAME, MT5Gateway, RequestRecord
from tests.fakes.fake_mt5 import FakeMT5


def started(fake: FakeMT5, records: list[RequestRecord] | None = None) -> MT5Gateway:
    gateway = MT5Gateway(
        lambda: fake,
        default_timeout=5.0,
        on_request=records.append if records is not None else None,
        idle_seconds=0.05,
    )
    gateway.start()
    return gateway


def test_every_call_runs_on_the_one_gateway_thread() -> None:
    fake = FakeMT5()
    gateway = started(fake)
    threads: list[str] = []

    def work(mt5: MT5Api) -> bool:
        threads.append(threading.current_thread().name)
        return mt5.initialize()

    try:
        callers = [threading.Thread(target=gateway.run, args=("init", work)) for _ in range(5)]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join()
        assert gateway.call("symbols_total") == 4
    finally:
        gateway.stop()
    assert threads == [THREAD_NAME] * 5
    assert fake.calls[-1] == "shutdown"


def test_requests_are_reported_with_masked_arguments() -> None:
    records: list[RequestRecord] = []
    gateway = started(FakeMT5(), records)
    try:
        gateway.call("initialize")
        assert gateway.call("login", 51234567, password="Demo-Pass-123", server="DemoBroker-Server")
        with pytest.raises(ZeroDivisionError):
            gateway.run("broken", lambda mt5: 1 / 0)
    finally:
        gateway.stop()
    login = next(record for record in records if record.name == "login")
    assert login.ok
    assert login.arguments["password"] == "***"
    assert login.arguments["server"] == "DemoBroker-Server"
    assert login.arguments["args"] == [51234567]
    broken = next(record for record in records if record.name == "broken")
    assert not broken.ok
    assert broken.error is not None and broken.error.startswith("ZeroDivisionError")
    assert all(record.duration_ms >= 0 for record in records)


def test_a_hanging_call_times_out_and_later_requests_expire_in_the_queue() -> None:
    fake = FakeMT5(hang_seconds=0.5)
    records: list[RequestRecord] = []
    gateway = started(fake, records)
    try:
        with pytest.raises(MT5Timeout):
            gateway.run("initialize", lambda mt5: mt5.initialize(), timeout=0.1)
        queued = gateway.submit("symbols_total", lambda mt5: mt5.symbols_total(), timeout=0.1)
        with pytest.raises(MT5Timeout):
            queued.result(timeout=3)
        fake.hang_seconds = 0
        assert gateway.run("version", lambda mt5: mt5.version(), timeout=3) is not None
    finally:
        gateway.stop()
    assert any(record.error == "expired in the queue" for record in records)


def test_a_missing_package_fails_every_request_with_a_clear_error() -> None:
    def missing() -> MT5Api:
        raise ImportError("No module named 'MetaTrader5'")

    gateway = MT5Gateway(missing, idle_seconds=0.05)
    gateway.start()
    try:
        with pytest.raises(MT5Unavailable):
            gateway.call("initialize")
    finally:
        gateway.stop()


def test_requests_fail_fast_when_the_gateway_is_not_running() -> None:
    gateway = MT5Gateway(FakeMT5, idle_seconds=0.05)
    with pytest.raises(MT5Error):
        gateway.call("initialize")
    gateway.start()
    gateway.stop()
    assert not gateway.running
    with pytest.raises(MT5Error):
        gateway.call("initialize")


def test_the_gateway_sends_heartbeats_while_idle() -> None:
    beats: list[float] = []
    gateway = MT5Gateway(
        FakeMT5,
        heartbeat=lambda: beats.append(time.monotonic()),
        idle_seconds=0.02,
    )
    gateway.start()
    try:
        deadline = time.monotonic() + 3
        while len(beats) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        gateway.stop()
    assert len(beats) >= 3
