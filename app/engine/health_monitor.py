"""The health monitor (spec E3): runs the health rules every minute in its own thread.

`collect` gathers a `HealthInputs` snapshot from the running parts (it only reads their
snapshots, never calls MT5), `evaluate` applies the rules. Status changes are logged
(WARNING for a warning, ERROR for a problem, INFO when it is OK again) and saved with
`HealthRecorder`'s policy. The Health page reads `snapshot` and can ask for a check now.
`after` runs once after every check in the same thread (the performance monitor).
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.observability.health import (
    HealthCheck,
    HealthInputs,
    HealthRecorder,
    HealthStatus,
    evaluate,
    overall,
    summary,
)

CHECK_SECONDS = 60.0
FIRST_DELAY_SECONDS = 30.0  # let the auto-connect finish before the first check
Save = Callable[[Sequence[HealthCheck], float], object]
Listener = Callable[["HealthSnapshot"], None]


def _quiet(level: str, message: str) -> None:
    return None


@dataclass(frozen=True)
class HealthSnapshot:
    checks: Sequence[HealthCheck] = field(default_factory=tuple)
    at: float = 0.0  # UTC seconds of the last run, 0 = not run yet

    @property
    def status(self) -> HealthStatus:
        return overall(self.checks)

    @property
    def text(self) -> str:
        return summary(self.checks)

    @property
    def green(self) -> bool:
        """No warning and no problem (unknown checks do not count)."""
        return bool(self.checks) and not any(check.problem for check in self.checks)

    @property
    def go_live_text(self) -> str | None:
        """For the Go-Live gate: None before the first check, "" when green, else the issues."""
        if not self.checks:
            return None
        return "" if self.green else self.text


def folder_size(folder: Path) -> float | None:
    """Bytes of every file below `folder`, or None when it cannot be read."""
    total = 0
    try:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                with contextlib.suppress(OSError):
                    total += os.path.getsize(os.path.join(root, name))
    except OSError:
        return None
    return float(total)


class HealthMonitor:
    def __init__(
        self,
        collect: Callable[[], HealthInputs],
        *,
        save: Save | None = None,
        log: Callable[[str, str], None] = _quiet,
        interval_seconds: float = CHECK_SECONDS,
        heartbeat: Callable[[], None] | None = None,
        recorder: HealthRecorder | None = None,
        clock: Callable[[], float] = time.time,
        first_delay_seconds: float = FIRST_DELAY_SECONDS,
        after: Sequence[Callable[[], object]] = (),
    ) -> None:
        self._collect = collect
        self._first_delay = first_delay_seconds
        self._save = save
        self._log = log
        self._interval = interval_seconds
        self._heartbeat = heartbeat
        self._recorder = recorder or HealthRecorder()
        self._clock = clock
        self._after = tuple(after)
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._snapshot = HealthSnapshot()
        self._listeners: list[Listener] = []
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def snapshot(self) -> HealthSnapshot:
        with self._lock:
            return self._snapshot

    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def run_once(self) -> HealthSnapshot:
        with self._run_lock:
            now = self._clock()
            try:
                inputs = self._collect()
            except Exception as error:
                inputs = HealthInputs(now=now)
                self._log("WARNING", f"Health inputs incomplete: {type(error).__name__}: {error}")
            checks = evaluate(inputs)
            before = self.snapshot.checks
            self._report(before, checks)
            self._store(checks, now)
            fresh = HealthSnapshot(tuple(checks), now)
            with self._lock:
                self._snapshot = fresh
                listeners = list(self._listeners)
        for listener in listeners:
            with contextlib.suppress(Exception):
                listener(fresh)
        for extra in self._after:
            try:
                extra()
            except Exception as error:
                self._log("WARNING", f"After the health check: {type(error).__name__}: {error}")
        return fresh

    def check_now(self) -> None:
        """Run the checks at once (the Health page's button); runs inline if not started."""
        if self._thread is None:
            self.run_once()
        else:
            self._wake.set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="health", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        self._wake.wait(self._first_delay)
        self._wake.clear()
        while not self._stop.is_set():
            with contextlib.suppress(Exception):
                self.run_once()
            if self._heartbeat is not None:
                with contextlib.suppress(Exception):
                    self._heartbeat()
            self._wake.wait(self._interval)
            self._wake.clear()

    def _report(self, before: Sequence[HealthCheck], after: Sequence[HealthCheck]) -> None:
        for old, new in self._recorder.changes(before, after):
            was_problem = old is not None and old.problem
            if new.status is HealthStatus.CRITICAL:
                level = "ERROR"
            elif new.status is HealthStatus.WARNING:
                level = "WARNING"
            elif was_problem:
                level = "INFO"
            else:
                continue
            fix = f" Fix: {new.fix}" if new.fix and new.problem else ""
            with contextlib.suppress(Exception):
                self._log(level, f"Health {new.title}: {new.status.value}. {new.text}{fix}")

    def _store(self, checks: Sequence[HealthCheck], now: float) -> None:
        if self._save is None:
            return
        chosen = self._recorder.select(checks, now)
        if not chosen:
            return
        try:
            self._save(chosen, now)
        except Exception as error:
            self._log("WARNING", f"Health checks not saved: {type(error).__name__}: {error}")
