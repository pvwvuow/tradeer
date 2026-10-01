"""Watchdog (spec E3): workers send heartbeats; a silent worker is reported and restarted.

A worker registers with a timeout and an optional restart function, then calls `beat()`
regularly from its own thread. When a worker stays silent longer than its timeout, `check()`
reports a freeze once (with the stack of the stuck thread), restarts the worker if it can,
and reports the recovery when heartbeats return.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass

Clock = Callable[[], float]

DEFAULT_CHECK_SECONDS = 1.0
DEFAULT_MAX_RESTARTS = 3


@dataclass(frozen=True)
class FreezeEvent:
    worker: str
    silent_seconds: float
    timeout_seconds: float
    restarted: bool
    restart_error: str | None
    stack: str | None


@dataclass(frozen=True)
class RecoveryEvent:
    worker: str
    silent_seconds: float


@dataclass(frozen=True)
class WorkerStatus:
    name: str
    silent_seconds: float
    timeout_seconds: float
    frozen: bool
    restarts: int


@dataclass
class _Worker:
    name: str
    timeout: float
    restart: Callable[[], None] | None
    max_restarts: int
    last_beat: float
    thread_id: int | None = None
    frozen: bool = False
    restarts: int = 0


def thread_stack(thread_id: int | None) -> str | None:
    if thread_id is None:
        return None
    frame = sys._current_frames().get(thread_id)
    if frame is None:
        return None
    return "".join(traceback.format_stack(frame))


class Watchdog:
    def __init__(
        self,
        clock: Clock = time.monotonic,
        on_freeze: Callable[[FreezeEvent], None] | None = None,
        on_recover: Callable[[RecoveryEvent], None] | None = None,
    ) -> None:
        self._clock = clock
        self._on_freeze = on_freeze
        self._on_recover = on_recover
        self._lock = threading.Lock()
        self._workers: dict[str, _Worker] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def register(
        self,
        name: str,
        timeout_seconds: float,
        restart: Callable[[], None] | None = None,
        max_restarts: int = DEFAULT_MAX_RESTARTS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        with self._lock:
            self._workers[name] = _Worker(
                name=name,
                timeout=timeout_seconds,
                restart=restart,
                max_restarts=max_restarts,
                last_beat=self._clock(),
            )

    def unregister(self, name: str) -> None:
        with self._lock:
            self._workers.pop(name, None)

    def beat(self, name: str) -> None:
        recovered: RecoveryEvent | None = None
        with self._lock:
            worker = self._workers.get(name)
            if worker is None:
                return
            now = self._clock()
            if worker.frozen:
                worker.frozen = False
                recovered = RecoveryEvent(name, now - worker.last_beat)
            worker.last_beat = now
            worker.thread_id = threading.get_ident()
        if recovered is not None and self._on_recover is not None:
            with contextlib.suppress(Exception):
                self._on_recover(recovered)

    def check(self) -> list[FreezeEvent]:
        """Find newly frozen workers, restart them when possible, and report each freeze once."""
        frozen: list[tuple[_Worker, float, str | None, bool]] = []
        with self._lock:
            now = self._clock()
            for worker in self._workers.values():
                silent = now - worker.last_beat
                if worker.frozen or silent <= worker.timeout:
                    continue
                worker.frozen = True
                can_restart = worker.restart is not None and worker.restarts < worker.max_restarts
                frozen.append((worker, silent, thread_stack(worker.thread_id), can_restart))
        events: list[FreezeEvent] = []
        for worker, silent, stack, can_restart in frozen:
            restarted, error = self._restart(worker) if can_restart else (False, None)
            event = FreezeEvent(worker.name, silent, worker.timeout, restarted, error, stack)
            events.append(event)
            if self._on_freeze is not None:
                with contextlib.suppress(Exception):
                    self._on_freeze(event)
        return events

    def statuses(self) -> list[WorkerStatus]:
        with self._lock:
            now = self._clock()
            return [
                WorkerStatus(w.name, now - w.last_beat, w.timeout, w.frozen, w.restarts)
                for w in self._workers.values()
            ]

    def start(self, interval_seconds: float = DEFAULT_CHECK_SECONDS) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(interval_seconds,),
            name="watchdog",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _restart(self, worker: _Worker) -> tuple[bool, str | None]:
        # Runs outside the lock: a restart may take time or call beat() itself.
        restart = worker.restart
        if restart is None:
            return False, None
        try:
            restart()
        except Exception as error:
            with self._lock:
                worker.restarts += 1
            return False, f"{type(error).__name__}: {error}"
        with self._lock:
            worker.restarts += 1
            worker.frozen = False
            worker.last_beat = self._clock()
        return True, None

    def _run(self, interval_seconds: float) -> None:
        while not self._stop.wait(interval_seconds):
            with contextlib.suppress(Exception):
                self.check()
