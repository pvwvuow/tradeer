"""Per-category log levels that change at runtime, plus a timed debug mode (spec E3)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import IntEnum

from app.observability.categories import LogCategory

Clock = Callable[[], float]

DEFAULT_DEBUG_MINUTES = 30.0
MAX_DEBUG_MINUTES = 24 * 60.0


class LogLevel(IntEnum):
    """The loguru levels and their numbers."""

    TRACE = 5
    DEBUG = 10
    INFO = 20
    SUCCESS = 25
    WARNING = 30
    ERROR = 40
    CRITICAL = 50

    @classmethod
    def parse(cls, value: str | int) -> LogLevel:
        if isinstance(value, int):
            return cls(value)
        try:
            return cls[value.strip().upper()]
        except KeyError:
            raise ValueError(f"unknown log level: {value!r}") from None


DEFAULT_LEVEL = LogLevel.INFO


def level_number(name: object, default: int = DEFAULT_LEVEL) -> int:
    """The number of a level name such as "WARNING"; `default` for anything unknown."""
    try:
        return int(LogLevel.parse(str(name)))
    except ValueError:
        return default


@dataclass(frozen=True)
class DebugModeState:
    active: bool
    remaining_seconds: float


class LevelRegistry:
    """Thread-safe minimum level per category.

    Debug mode lowers every category to DEBUG for a limited time and then reverts on its own,
    so a forgotten toggle cannot fill the disk.
    """

    def __init__(
        self,
        default: LogLevel = DEFAULT_LEVEL,
        overrides: Mapping[LogCategory, LogLevel] | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._levels = {category: default for category in LogCategory}
        if overrides:
            self._levels.update(overrides)
        self._debug_until: float | None = None

    def level(self, category: LogCategory) -> LogLevel:
        """The configured level, ignoring debug mode."""
        with self._lock:
            return self._levels[category]

    def set_level(self, category: LogCategory, level: LogLevel) -> None:
        with self._lock:
            self._levels[category] = level

    def effective_level(self, category: LogCategory) -> LogLevel:
        with self._lock:
            configured = self._levels[category]
            if self._debug_active():
                return min(configured, LogLevel.DEBUG)
            return configured

    def accepts(self, category: LogCategory, level_no: int) -> bool:
        return level_no >= self.effective_level(category)

    def enable_debug(self, minutes: float = DEFAULT_DEBUG_MINUTES) -> None:
        if not 0 < minutes <= MAX_DEBUG_MINUTES:
            raise ValueError(f"debug mode lasts 0 to {MAX_DEBUG_MINUTES:g} minutes, not {minutes}")
        with self._lock:
            self._debug_until = self._clock() + minutes * 60.0

    def disable_debug(self) -> None:
        with self._lock:
            self._debug_until = None

    def debug_state(self) -> DebugModeState:
        with self._lock:
            if not self._debug_active() or self._debug_until is None:
                return DebugModeState(active=False, remaining_seconds=0.0)
            return DebugModeState(active=True, remaining_seconds=self._debug_until - self._clock())

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return {category.value: level.name for category, level in self._levels.items()}

    def _debug_active(self) -> bool:
        # Caller holds the lock. Expiry is detected lazily, on the next check.
        if self._debug_until is None:
            return False
        if self._clock() >= self._debug_until:
            self._debug_until = None
            return False
        return True
