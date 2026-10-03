import pytest

from app.observability.categories import LogCategory
from app.observability.levels import (
    DEFAULT_LEVEL,
    MAX_DEBUG_MINUTES,
    LevelRegistry,
    LogLevel,
    level_number,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_parse_levels_by_name_and_number() -> None:
    assert LogLevel.parse("warning") is LogLevel.WARNING
    assert LogLevel.parse(" DEBUG ") is LogLevel.DEBUG
    assert LogLevel.parse(40) is LogLevel.ERROR
    with pytest.raises(ValueError):
        LogLevel.parse("loud")
    assert level_number("CRITICAL") == 50
    assert level_number("custom") == DEFAULT_LEVEL


def test_each_category_has_its_own_level() -> None:
    registry = LevelRegistry(overrides={LogCategory.PERF: LogLevel.WARNING})
    assert registry.level(LogCategory.APP) is LogLevel.INFO
    assert registry.level(LogCategory.PERF) is LogLevel.WARNING
    registry.set_level(LogCategory.MT5, LogLevel.TRACE)
    assert registry.accepts(LogCategory.MT5, LogLevel.TRACE)
    assert not registry.accepts(LogCategory.APP, LogLevel.DEBUG)
    assert not registry.accepts(LogCategory.PERF, LogLevel.INFO)
    assert registry.snapshot()["perf"] == "WARNING"


def test_debug_mode_lowers_every_category_then_reverts_on_its_own() -> None:
    clock = FakeClock()
    registry = LevelRegistry(overrides={LogCategory.MT5: LogLevel.TRACE}, clock=clock)
    registry.enable_debug(minutes=30)
    assert registry.effective_level(LogCategory.APP) is LogLevel.DEBUG
    assert registry.effective_level(LogCategory.MT5) is LogLevel.TRACE
    assert registry.level(LogCategory.APP) is LogLevel.INFO
    state = registry.debug_state()
    assert state.active
    assert state.remaining_seconds == 1800
    clock.now += 1800
    assert not registry.debug_state().active
    assert registry.effective_level(LogCategory.APP) is LogLevel.INFO


def test_debug_mode_can_be_switched_off_and_is_bounded() -> None:
    registry = LevelRegistry()
    registry.enable_debug(minutes=5)
    registry.disable_debug()
    assert not registry.debug_state().active
    with pytest.raises(ValueError):
        registry.enable_debug(minutes=0)
    with pytest.raises(ValueError):
        registry.enable_debug(minutes=MAX_DEBUG_MINUTES + 1)
