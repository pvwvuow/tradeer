"""Position management rules (spec C7): break-even, trailing, partial close, time exit."""

from app.domain.management import ManagedPosition, ManagementSettings, plan, track
from app.domain.signals import Direction


def position(**changes: object) -> ManagedPosition:
    values: dict[str, object] = {
        "ticket": 1,
        "symbol": "EURUSD",
        "direction": Direction.LONG,
        "entry": 1.10000,
        "sl": 1.09900,
        "tp": 1.10300,
        "initial_sl": 1.09900,
        "volume": 0.5,
        "opened_at": 0.0,
        "bar_seconds": 900,
        "atr": 0.0008,
        "digits": 5,
        "point": 1e-5,
        "tick_size": 1e-5,
        "stops_level": 10,
        "freeze_level": 0,
        "volume_min": 0.01,
        "volume_step": 0.01,
    }
    values.update(changes)
    return ManagedPosition(**values)  # type: ignore[arg-type]


def test_everything_is_off_by_default() -> None:
    assert plan(position(), ManagementSettings(), 1.10250, 1.10257, 10_000.0) == []


def test_mfe_and_mae_are_tracked_in_r_at_the_exit_price() -> None:
    found = track(position(), 1.10150, 1.10157)
    found = track(found, 1.09950, 1.09957)
    assert round(found.best_r, 2) == 1.5 and round(found.worst_r, 2) == -0.5


def test_break_even_only_tightens_and_respects_the_stops_level() -> None:
    rules = ManagementSettings(break_even_at_r=1.0, break_even_offset_points=20)
    [action] = plan(position(), rules, 1.10110, 1.10117, 60.0)
    assert action.kind == "modify_sl" and action.sl == 1.10020
    assert plan(position(sl=1.10050), rules, 1.10110, 1.10117, 60.0) == []
    short = position(direction=Direction.SHORT, sl=1.10100, tp=1.09700, initial_sl=1.10100)
    [moved] = plan(short, rules, 1.09880, 1.09887, 60.0)
    assert moved.sl == 1.09980


def test_trailing_partial_close_and_time_exit() -> None:
    trail = ManagementSettings(trailing_atr=1.0, trailing_start_r=1.0)
    moving = track(position(), 1.10200, 1.10207)
    [action] = plan(moving, trail, 1.10200, 1.10207, 60.0)
    assert action.kind == "modify_sl" and action.sl == 1.10120
    part = ManagementSettings(partial_close_at_r=1.0, partial_close_percent=50)
    [half] = plan(position(), part, 1.10110, 1.10117, 60.0)
    assert half.kind == "partial_close" and half.volume == 0.25
    assert plan(position(volume=0.01), part, 1.10110, 1.10117, 60.0) == []
    timed = ManagementSettings(time_exit_bars=4)
    [close] = plan(position(), timed, 1.1, 1.1, 4 * 900.0)
    assert close.kind == "close" and close.volume == 0.5


def test_nothing_changes_inside_the_freeze_level() -> None:
    rules = ManagementSettings(break_even_at_r=0.1)
    frozen = position(sl=1.10005, freeze_level=20, initial_sl=1.09900, entry=1.10000)
    assert plan(frozen, rules, 1.10012, 1.10019, 60.0) == []
