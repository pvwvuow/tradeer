from datetime import date, timedelta

from app.analysis.levels import (
    Level,
    build_levels,
    cluster_swings,
    period_levels,
    round_numbers,
    round_step,
)
from app.analysis.structure import Swing, SwingKind
from tests.unit.analysis_helpers import ohlc_bars


def swing(price: float) -> Swing:
    return Swing(SwingKind.HIGH, 0, 0, price, 3, "")


def test_close_swings_form_one_level() -> None:
    levels = cluster_swings([swing(1.1000), swing(1.1004), swing(1.1050)], tolerance=0.0005)
    assert [(round(level.price, 4), level.touches) for level in levels] == [
        (1.1002, 2),
        (1.105, 1),
    ]
    assert levels[0].kind == "swing cluster"


def test_round_numbers_scale_with_the_daily_range() -> None:
    assert round(round_step(1.0834, 0.0070), 10) == 0.005
    assert round_step(2385.0, 30.0) == 20.0
    assert round_step(151.2, 1.2) == 1.0
    prices = [round(level.price, 4) for level in round_numbers(1.0834, 0.005)]
    assert prices == [1.08, 1.075, 1.085, 1.09]


def test_previous_day_and_week_levels() -> None:
    days = [date(2026, 9, 21) + timedelta(days=offset) for offset in range(10)]
    rows = [
        (1.10, 1.10 + 0.01 * index, 1.09 - 0.001 * index, 1.10 + 0.001 * index)
        for index in range(10)
    ]
    daily = ohlc_bars(rows, "D1")
    found = {level.kind: level.price for level in period_levels(daily, days, date(2026, 10, 1))}
    assert round(found["previous day high"], 4) == 1.19  # 2026-09-30, index 9
    # Previous ISO week: 2026-09-21 .. 2026-09-27 (indexes 0 to 6).
    assert round(found["previous week high"], 4) == 1.16
    assert round(found["previous week low"], 4) == 1.084
    assert round(found["previous week close"], 4) == 1.106


def test_levels_carry_their_distance_in_atr() -> None:
    levels = build_levels(
        1.1012,
        0.0010,
        [swing(1.1018), swing(1.1018)],
        [Level(1.1002, "previous day low")],
        0.0700,
    )
    support, resistance = levels.nearest_support, levels.nearest_resistance
    assert support is not None and support.kind == "previous day low"
    assert round(support.distance_atr, 6) == -1.0
    assert resistance is not None and resistance.kind == "swing cluster"
    assert resistance.side == "resistance"
    assert "1.10180 (0.6 ATR)" in resistance.text(5)
    assert any(level.kind == "round number" for level in levels.levels)
    assert build_levels(1.1, 0.0, [], [], 0.0).levels == ()
