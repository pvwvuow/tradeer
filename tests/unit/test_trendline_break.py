"""Trendline break on M5 (23 October 2026): the line, the two kinds of entry, the filters."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.analysis.bars import Bars
from app.calendar.models import CalendarEvent, Impact
from app.core.execution_settings import ExecutionConfig
from app.domain.management import ManagementSettings
from app.domain.signals import Direction, OrderType
from app.strategies.base import Evaluation, SetupState
from app.strategies.context import MarketContext
from app.strategies.registry import (
    MAGIC_NUMBERS,
    STRATEGIES,
    on_by_default,
    strategy_for_magic,
)
from app.strategies.trendline_break import TrendlineBreak, TrendlineBreakParams
from tests.unit.strategy_helpers import context
from tests.unit.trendline_scenes import (
    FIRST,
    FIRST_END,
    PULLBACK,
    PULLBACK_END,
    m5_bars,
    mirror,
)


def ctx_for(bars: Bars) -> MarketContext:
    return context({"M5": bars}, timeframe="M5", digits=2)


def run(
    bars: Bars,
    params: TrendlineBreakParams | None = None,
    **changes: object,
) -> Evaluation:
    return TrendlineBreak(params).evaluate(replace(ctx_for(bars), **changes))  # type: ignore[arg-type]


def test_the_first_break_of_a_falling_line_buys_with_the_stop_behind_the_low() -> None:
    evaluation = run(m5_bars(FIRST, FIRST_END))
    assert evaluation.state is SetupState.READY
    signal = evaluation.signal
    assert signal is not None
    assert signal.direction is Direction.LONG and signal.order_type is OrderType.MARKET
    assert signal.valid_prices() and abs(signal.rr - 5.0) < 0.01
    assert signal.features["kind"] == "first"
    assert signal.sl < 98.0  # below the last low before the line (98), by the buffer
    assert signal.strategy == "trendline_break" and "trendline" in signal.reason


def test_a_break_of_a_correction_inside_an_uptrend_is_a_pullback_entry() -> None:
    signal = run(m5_bars(PULLBACK, PULLBACK_END)).signal
    assert signal is not None
    assert signal.direction is Direction.LONG and signal.features["kind"] == "pullback"
    assert signal.sl < 150.0  # behind the low of the correction


def test_the_short_side_is_mirrored() -> None:
    first = run(m5_bars(mirror(FIRST), FIRST_END)).signal
    assert first is not None and first.direction is Direction.SHORT
    assert first.valid_prices() and first.sl > first.entry > first.tp
    pullback = run(m5_bars(mirror(PULLBACK), PULLBACK_END)).signal
    assert pullback is not None and pullback.features["kind"] == "pullback"


def test_one_bar_earlier_there_is_no_break_yet() -> None:
    evaluation = run(m5_bars(FIRST, FIRST_END - 1))
    assert evaluation.signal is None
    assert evaluation.state in (SetupState.NONE, SetupState.FORMING)


def test_only_the_bar_that_breaks_the_line_gives_a_signal() -> None:
    for count in range(FIRST_END + 1, FIRST_END + 8):
        assert run(m5_bars(FIRST, count)).signal is None


def test_each_kind_can_be_turned_off() -> None:
    no_first = TrendlineBreakParams(allow_first=False)
    assert run(m5_bars(FIRST, FIRST_END), no_first).note == "first entries are off"
    no_pullback = TrendlineBreakParams(allow_pullback=False)
    evaluation = run(m5_bars(PULLBACK, PULLBACK_END), no_pullback)
    assert evaluation.signal is None and evaluation.note == "pullback entries are off"


def test_a_wide_spread_is_not_traded() -> None:
    bars = m5_bars(PULLBACK, PULLBACK_END)
    assert run(bars, spread=0.02).signal is not None
    evaluation = run(bars, spread=5.0)
    assert evaluation.signal is None and evaluation.note == "spread too wide"


def test_high_impact_news_close_by_blocks_and_far_news_does_not() -> None:
    bars = m5_bars(PULLBACK, PULLBACK_END)
    now = int(bars.time[-1]) + 300
    soon = CalendarEvent(now + 10 * 60, "USD", Impact.HIGH, "CPI m/m")
    far = CalendarEvent(now + 3 * 3600, "USD", Impact.HIGH, "FOMC")
    other = CalendarEvent(now + 10 * 60, "JPY", Impact.HIGH, "BoJ")
    medium = CalendarEvent(now + 10 * 60, "USD", Impact.MEDIUM, "Claims")
    blocked = run(bars, events=(soon,))
    assert blocked.signal is None and blocked.note == "range or news"
    for event in (far, other, medium):
        assert run(bars, events=(event,)).signal is not None


def test_a_range_is_not_traded() -> None:
    flat = m5_bars([(0, 100), (300, 100.5)], 260)
    evaluation = run(flat)
    assert evaluation.signal is None and evaluation.note == "range or news"


def test_short_history_waits() -> None:
    evaluation = run(m5_bars(FIRST, 120))
    assert evaluation.state is SetupState.NONE and evaluation.signal is None
    assert "missing" in evaluation.conditions[0].detail


def test_the_parameters_are_checked() -> None:
    with pytest.raises(ValidationError):
        TrendlineBreakParams(swing_strength=1)
    with pytest.raises(ValidationError):
        TrendlineBreakParams(reward_r=1)


def test_it_is_registered_off_with_its_own_magic() -> None:
    assert STRATEGIES["trendline_break"] is TrendlineBreak
    assert not on_by_default("trendline_break")
    assert MAGIC_NUMBERS["trendline_break"] == 26_070_011
    assert strategy_for_magic(26_070_011) == "trendline_break"
    assert len(set(MAGIC_NUMBERS.values())) == len(MAGIC_NUMBERS)


def test_half_out_at_2r_and_break_even_are_the_default_and_a_saved_choice_wins() -> None:
    rules = ExecutionConfig().management_for("trendline_break")
    assert rules.partial_close_at_r == 2.0 and rules.partial_close_percent == 50.0
    assert rules.break_even_at_r == 2.0 and rules.trailing_atr == 0.0
    saved = ExecutionConfig(management={"trendline_break": ManagementSettings()})
    assert saved.management_for("trendline_break").partial_close_at_r == 0.0
