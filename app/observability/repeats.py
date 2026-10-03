"""Collapse a message that repeats many times into one line plus a count (ADR 48).

On a real PC one harmless Qt warning made up 95% of the log. The first copy of a message is
logged; further copies within `window_seconds` are only counted, and the next copy after the
window is logged with the number that was hidden.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

DEFAULT_WINDOW_SECONDS = 60.0
MAX_TRACKED = 256


class RepeatFilter:
    def __init__(
        self,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._seen: dict[str, tuple[float, int]] = {}

    def check(self, message: str) -> tuple[bool, int]:
        """`(log_it, hidden)`: hidden is how many copies were dropped since it was last logged."""
        now = self._clock()
        with self._lock:
            entry = self._seen.get(message)
            if entry is not None and now - entry[0] < self._window:
                self._seen[message] = (entry[0], entry[1] + 1)
                return False, 0
            if entry is None and len(self._seen) >= MAX_TRACKED:
                oldest = min(self._seen, key=lambda key: self._seen[key][0])
                del self._seen[oldest]
            self._seen[message] = (now, 0)
            return True, entry[1] if entry is not None else 0

    def text(self, message: str, hidden: int) -> str:
        if not hidden:
            return message
        return f"{message} (repeated {hidden} more times in {self._window:g} s)"
