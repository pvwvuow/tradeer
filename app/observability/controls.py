"""What the Logs page may change at runtime. Every change is audit-logged (spec E3)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.observability.buffer import RecentLogBuffer
from app.observability.categories import LogCategory
from app.observability.levels import (
    DEFAULT_DEBUG_MINUTES,
    DebugModeState,
    LevelRegistry,
    LogLevel,
)

AuditCallback = Callable[[str, object, object], None]


class LogControls:
    def __init__(
        self,
        buffer: RecentLogBuffer,
        registry: LevelRegistry,
        log_dir: Path,
        crash_dir: Path,
        audit: AuditCallback | None = None,
    ) -> None:
        self.buffer = buffer
        self.registry = registry
        self.log_dir = log_dir
        self.crash_dir = crash_dir
        self._audit = audit

    def set_level(self, category: LogCategory, level: LogLevel) -> None:
        before = self.registry.level(category)
        if before is level:
            return
        self.registry.set_level(category, level)
        self._record(
            "log_level_changed",
            {"category": category.value, "level": before.name},
            {"category": category.value, "level": level.name},
        )

    def debug_state(self) -> DebugModeState:
        return self.registry.debug_state()

    def enable_debug(self, minutes: float = DEFAULT_DEBUG_MINUTES) -> None:
        self.registry.enable_debug(minutes)
        self._record("debug_mode_enabled", {"debug_mode": False}, {"minutes": minutes})

    def disable_debug(self) -> None:
        if not self.registry.debug_state().active:
            return
        self.registry.disable_debug()
        self._record("debug_mode_disabled", {"debug_mode": True}, {"debug_mode": False})

    def toggle_debug(self, minutes: float = DEFAULT_DEBUG_MINUTES) -> bool:
        """Switch debug mode and return whether it is now on."""
        if self.debug_state().active:
            self.disable_debug()
            return False
        self.enable_debug(minutes)
        return True

    def _record(self, action: str, before: object, after: object) -> None:
        if self._audit is not None:
            self._audit(action, before, after)
