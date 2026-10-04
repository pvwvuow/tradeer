"""The performance monitor (spec E3, D4): metrics every minute, a warning over a budget.

It has no thread of its own: the health monitor runs it after every health check, so the
Health page's "Check now" refreshes both. `collect` reads the probes and this process's CPU
and memory; a metric going over its budget is logged as a WARNING in the `perf` category
(INFO when it is back within), and the values are saved to `performance_metrics` with
`PerfRecorder`'s policy (changes at once, everything every 15 minutes).
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.observability.health import HealthStatus
from app.observability.metrics import (
    Metric,
    PerfInputs,
    PerfRecorder,
    evaluate_metrics,
    perf_summary,
)

Save = Callable[[Sequence[Metric], float], object]


def _quiet(level: str, message: str) -> None:
    return None


@dataclass(frozen=True)
class PerfSnapshot:
    metrics: Sequence[Metric] = field(default_factory=tuple)
    at: float = 0.0  # UTC seconds of the last run, 0 = not run yet

    @property
    def text(self) -> str:
        return perf_summary(self.metrics)

    @property
    def over(self) -> list[Metric]:
        return [metric for metric in self.metrics if metric.over]


class PerfMonitor:
    def __init__(
        self,
        collect: Callable[[], PerfInputs],
        *,
        save: Save | None = None,
        log: Callable[[str, str], None] = _quiet,
        recorder: PerfRecorder | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._collect = collect
        self._save = save
        self._log = log
        self._recorder = recorder or PerfRecorder()
        self._clock = clock
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._snapshot = PerfSnapshot()

    @property
    def snapshot(self) -> PerfSnapshot:
        with self._lock:
            return self._snapshot

    def run_once(self) -> PerfSnapshot:
        with self._run_lock:
            now = self._clock()
            try:
                inputs = self._collect()
            except Exception as error:
                inputs = PerfInputs(now=now)
                found = f"{type(error).__name__}: {error}"
                self._log("WARNING", f"Performance inputs incomplete: {found}")
            metrics = evaluate_metrics(inputs)
            self._report(self.snapshot.metrics, metrics)
            self._store(metrics, now)
            fresh = PerfSnapshot(tuple(metrics), now)
            with self._lock:
                self._snapshot = fresh
        return fresh

    def _report(self, before: Sequence[Metric], after: Sequence[Metric]) -> None:
        for old, new in self._recorder.changes(before, after):
            if new.over:
                level = "WARNING"
                text = f"{new.value_text()} is over the budget ({new.budget}). {new.text}"
            elif old is not None and old.over and new.status is HealthStatus.OK:
                level = "INFO"
                text = f"{new.value_text()} is within the budget again ({new.budget})."
            else:
                continue
            with contextlib.suppress(Exception):
                self._log(level, f"Performance {new.title}: {text}")

    def _store(self, metrics: Sequence[Metric], now: float) -> None:
        if self._save is None:
            return
        chosen = self._recorder.select(metrics, now)
        if not chosen:
            return
        try:
            self._save(chosen, now)
        except Exception as error:
            self._log("WARNING", f"Performance metrics not saved: {type(error).__name__}: {error}")
