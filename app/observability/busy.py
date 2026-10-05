"""Heavy work in this process that the idle CPU budget must not count (spec D4).

The budget is "idle CPU under 3 % of the PC", the average of the last five minutes. A
backtest replays months of bars in this process (the Backtest page's worker thread), so the
app is far over it while one runs and for the rest of those five minutes. `busy("backtest")`
marks such work and `recent(seconds)` names the work that ran within the last `seconds`
("" when the app was idle): the performance monitor then shows the CPU as busy, not as a
problem. Model training runs in a child process and never counts here.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager


class BusyTracker:
    """The jobs running now and when each kind of job last ended (thread-safe)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._running: dict[str, int] = {}
        self._ended: dict[str, float] = {}

    @contextmanager
    def busy(self, name: str) -> Iterator[None]:
        with self._lock:
            self._running[name] = self._running.get(name, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                left = self._running.get(name, 1) - 1
                if left > 0:
                    self._running[name] = left
                else:
                    self._running.pop(name, None)
                self._ended[name] = self._clock()

    def recent(self, seconds: float) -> str:
        """The work running now or ended within the last `seconds`, "" when idle."""
        now = self._clock()
        with self._lock:
            names = set(self._running)
            names.update(name for name, end in self._ended.items() if now - end <= seconds)
        return " and ".join(sorted(names))


TRACKER = BusyTracker()
busy = TRACKER.busy
recent = TRACKER.recent
