"""A PC that slept is not a frozen app (PC log of 16 October 2026)."""

from __future__ import annotations

from app.observability.watchdog import FreezeEvent, SuspendEvent, Watchdog, slept


class Clocks:
    def __init__(self) -> None:
        self.wall = 1000.0
        self.cpu = 5.0

    def now(self) -> float:
        return self.wall

    def used(self) -> float:
        return self.cpu


def make(clocks: Clocks) -> tuple[Watchdog, list[FreezeEvent], list[SuspendEvent]]:
    freezes: list[FreezeEvent] = []
    pauses: list[SuspendEvent] = []
    dog = Watchdog(
        clock=clocks.now,
        on_freeze=freezes.append,
        on_suspend=pauses.append,
        cpu_clock=clocks.used,
    )
    return dog, freezes, pauses


def test_a_long_wait_with_no_cpu_is_sleep() -> None:
    assert slept(2285.0, 0.4, 1.0)
    assert not slept(30.0, 0.0, 1.0)  # a short hiccup
    assert not slept(2285.0, 2000.0, 1.0)  # the CPU was busy: a real freeze


def test_waking_from_sleep_reports_one_pause_and_no_freeze() -> None:
    clocks = Clocks()
    dog, freezes, pauses = make(clocks)
    dog.register("ui", 30.0)
    dog.register("cloud-sync", 60.0)
    last, cpu = clocks.now(), clocks.used()
    clocks.wall += 2285.0
    clocks.cpu += 0.3
    dog.tick(1.0, last, cpu)
    assert freezes == []
    assert len(pauses) == 1
    assert round(pauses[0].paused_seconds) == 2284
    assert not any(status.frozen for status in dog.statuses())


def test_a_busy_freeze_is_still_reported() -> None:
    clocks = Clocks()
    dog, freezes, pauses = make(clocks)
    dog.register("ui", 30.0)
    last, cpu = clocks.now(), clocks.used()
    clocks.wall += 120.0
    clocks.cpu += 110.0
    dog.tick(1.0, last, cpu)
    assert pauses == []
    assert [event.worker for event in freezes] == ["ui"]


def test_one_silent_worker_while_the_loop_runs_on_time_is_a_freeze() -> None:
    clocks = Clocks()
    dog, freezes, pauses = make(clocks)
    dog.register("analysis", 10.0)
    dog.register("ui", 10.0)
    last, cpu = clocks.now(), clocks.used()
    for _ in range(12):
        clocks.wall += 1.0
        dog.beat("ui")
        last, cpu = dog.tick(1.0, last, cpu)
    assert pauses == []
    assert [event.worker for event in freezes] == ["analysis"]
