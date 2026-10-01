import threading
import time

import pytest

from app.observability.watchdog import FreezeEvent, RecoveryEvent, Watchdog


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_silent_worker_is_reported_once_with_its_stack_and_then_recovers() -> None:
    clock = FakeClock()
    freezes: list[FreezeEvent] = []
    recoveries: list[RecoveryEvent] = []
    watchdog = Watchdog(clock=clock, on_freeze=freezes.append, on_recover=recoveries.append)
    watchdog.register("ui", timeout_seconds=10)
    watchdog.beat("ui")
    clock.now = 10
    assert watchdog.check() == []
    clock.now = 12
    events = watchdog.check()
    assert [event.worker for event in events] == ["ui"]
    assert events[0].silent_seconds == 12
    assert not events[0].restarted
    assert events[0].stack is not None and "test_watchdog" in events[0].stack
    clock.now = 30
    assert watchdog.check() == []
    assert len(freezes) == 1
    watchdog.beat("ui")
    assert recoveries == [RecoveryEvent("ui", 30)]
    assert not watchdog.statuses()[0].frozen


def test_frozen_workers_are_restarted_up_to_the_limit() -> None:
    clock = FakeClock()
    restarts: list[str] = []
    watchdog = Watchdog(clock=clock)
    watchdog.register("mt5", 5, restart=lambda: restarts.append("mt5"), max_restarts=2)
    results = []
    for _ in range(3):
        clock.now += 6
        results.append(watchdog.check()[0].restarted)
        if results[-1] is False:
            break
    assert results == [True, True, False]
    assert restarts == ["mt5", "mt5"]
    assert watchdog.statuses()[0].restarts == 2


def test_a_failing_restart_is_reported() -> None:
    clock = FakeClock()

    def broken() -> None:
        raise RuntimeError("cannot restart")

    watchdog = Watchdog(clock=clock)
    watchdog.register("sync", 1, restart=broken)
    clock.now = 2
    event = watchdog.check()[0]
    assert not event.restarted
    assert event.restart_error == "RuntimeError: cannot restart"


def test_unknown_or_removed_workers_are_ignored() -> None:
    watchdog = Watchdog(clock=FakeClock())
    watchdog.beat("nobody")
    watchdog.register("x", 1)
    watchdog.unregister("x")
    assert watchdog.statuses() == []
    with pytest.raises(ValueError):
        watchdog.register("bad", 0)


def test_the_background_thread_checks_on_its_own() -> None:
    frozen = threading.Event()
    watchdog = Watchdog(on_freeze=lambda event: frozen.set())
    watchdog.register("worker", timeout_seconds=0.05)
    watchdog.start(interval_seconds=0.01)
    try:
        assert frozen.wait(timeout=5)
    finally:
        watchdog.stop()
    started = time.monotonic()
    watchdog.stop()
    assert time.monotonic() - started < 1
