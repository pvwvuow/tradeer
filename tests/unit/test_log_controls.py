from pathlib import Path

from app.observability.buffer import RecentLogBuffer
from app.observability.categories import LogCategory
from app.observability.controls import LogControls
from app.observability.levels import LevelRegistry, LogLevel


def make_controls() -> tuple[LogControls, list[tuple[str, object, object]]]:
    calls: list[tuple[str, object, object]] = []

    def audit(action: str, before: object, after: object) -> None:
        calls.append((action, before, after))

    controls = LogControls(
        RecentLogBuffer(),
        LevelRegistry(),
        Path("logs"),
        Path("crash_reports"),
        audit=audit,
    )
    return controls, calls


def test_level_changes_are_applied_and_audited_with_before_and_after() -> None:
    controls, calls = make_controls()
    controls.set_level(LogCategory.MT5, LogLevel.DEBUG)
    controls.set_level(LogCategory.MT5, LogLevel.DEBUG)
    assert controls.registry.level(LogCategory.MT5) is LogLevel.DEBUG
    assert calls == [
        (
            "log_level_changed",
            {"category": "mt5", "level": "INFO"},
            {"category": "mt5", "level": "DEBUG"},
        ),
    ]


def test_debug_mode_toggles_and_is_audited() -> None:
    controls, calls = make_controls()
    assert controls.toggle_debug(10) is True
    assert controls.debug_state().active
    assert controls.toggle_debug() is False
    assert not controls.debug_state().active
    controls.disable_debug()
    assert [call[0] for call in calls] == ["debug_mode_enabled", "debug_mode_disabled"]
