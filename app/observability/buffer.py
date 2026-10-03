"""In-memory ring buffer of recent log entries for the Logs page and crash reports."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Mapping
from typing import Any

DEFAULT_CAPACITY = 5000

LogEntry = dict[str, Any]


class RecentLogBuffer:
    """Thread-safe. Every entry gets an increasing sequence number so readers can tail it."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._lock = threading.Lock()
        self._items: deque[tuple[int, LogEntry]] = deque(maxlen=capacity)
        self._seq = 0

    def append(self, entry: Mapping[str, Any]) -> int:
        with self._lock:
            self._seq += 1
            self._items.append((self._seq, dict(entry)))
            return self._seq

    def since(self, seq: int) -> list[tuple[int, LogEntry]]:
        """Entries newer than `seq`, oldest first."""
        with self._lock:
            return [item for item in self._items if item[0] > seq]

    def last(self, count: int) -> list[LogEntry]:
        """The newest `count` entries, oldest first."""
        if count <= 0:
            return []
        with self._lock:
            items = list(self._items)[-count:]
        return [entry for _, entry in items]

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
