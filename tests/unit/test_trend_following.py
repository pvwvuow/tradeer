"""Daily trend following (16 October 2026): rules, timing, and its trailing stop default."""

from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from app.analysis import indicators
from app.analysis.bars import Bars
from app.core.execution_settings import ExecutionConfig
from app.domain.management import ManagementSettings
from app.domain.signals import Direction, OrderType
from app.strategies.base import Evaluation, SetupState
from app.strategies.registry import (
    MAGIC_NUMBERS,
    STRATEGIES,
    on_by_default,
    strategy_for_magic,
)
from app.strategies.trend_following import TrendFollowing, TrendFollowingParams
from tests.unit.strategy_helpers import WEDNESDAY, context

DAY = 86_400
H4 = 14_400
COUNT = 300


def daily(direction: int, *, drift: float = 0.0003, breakout: bool = True) -> Bars:
    """D1 bars of a year-long trend with swings; the last close breaks the 55-day extreme."""
    index = np.arange(COUNT, dtype=np.float64)
    closes = 1.1 + direction * drift * index + 0.004 * np.sin(2 * np.pi * index / 30)
    opens = np.r_[closes[0], closes[:-1]]
    highs = np.maximum(opens, closes) + 0.001
    lows = np.minimum(opens, closes) - 0.001
    if breakout:
        if direction > 0:
            closes[-1] = highs[-56:-1].max() + 0.003
        else:
            closes[-1] = lows[-56:-1].min() - 0.003
        highs[-1] = max(highs[-1], opens[-1], closes[-1]) + 0.001
        lows[-1] = min(lows[-1], opens[-1], closes[-1]) - 0.001
    start = WEDNESDAY - COUNT * DAY
    return Bars.build(
        "EURUSD",
        "D1",
        time=start + DAY * np.arange(COUNT),
        open=opens,
        high=highs,
        low=lows,
        close=closes,
    )


def four_hourly(d1: Bars, close_hour: int = 12) -> Bars:
    """H4 bars after the last D1 bar; the newest one closes at `close_hour` (server = UTC)."""
    last_open = int(d1.time[-1]) + DAY + close_hour * 3600 - H4
    times = last_open - H4 * np.arange(29, -1, -1)
    price = float(d1.close[-1])
    closes = np.full(30, price)
    return Bars.build(
        "EURUSD",
        "H4",
        time=times,
        open=closes,
        high=closes + 0.0005,
        low=closes - 0.0005,
        close=closes,
    )


def run(
    d1: Bars,
    close_hour: int = 12,
    params: TrendFollowingParams | None = None,
) -> Evaluation:
    ctx = context({"H4": four_hourly(d1, close_hour), "D1": d1}, timeframe="H4")
    return TrendFollowing(params).evaluate(ctx)


def test_a_breakout_with_the_year_buys_with_a_wide_atr_stop() -> None:
    d1 = daily(1)
    evaluation = run(d1)
    assert evaluation.state is SetupState.READY
    signal = evaluation.signal
    assert signal is not None
    assert signal.direction is Direction.LONG and signal.order_type is OrderType.MARKET
    assert signal.valid_prices() and abs(signal.rr - 5.0) < 0.01
    atr = indicators.last(indicators.atr(d1.high, d1.low, d1.close, 20))
    assert abs(signal.risk - 3.0 * atr) < 1e-4
    assert signal.strategy == "trend_following" and "55-day high" in signal.reason
    assert all(condition.passed for condition in evaluation.conditions)


def test_the_short_side_is_mirrored() -> None:
    signal = run(daily(-1)).signal
    assert signal is not None
    assert signal.direction is Direction.SHORT and signal.valid_prices()
    assert signal.sl > signal.entry > signal.tp


def test_it_checks_only_once_a_day_at_the_entry_hour() -> None:
    d1 = daily(1)
    for hour in (4, 8, 16, 20):
        evaluation = run(d1, close_hour=hour)
        assert evaluation.signal is None and evaluation.note == "not the daily check"
    late = TrendFollowingParams(entry_hour=16)
    assert run(d1, close_hour=16, params=late).signal is not None


def test_a_breakout_against_the_year_is_not_traded() -> None:
    falling = daily(-1, breakout=False)
    closes = falling.close.copy()
    highs = falling.high.copy()
    closes[-1] = highs[-56:-1].max() + 0.003  # a jump up in a year-long fall
    highs[-1] = closes[-1] + 0.001
    jumped = Bars.build(
        "EURUSD",
        "D1",
        time=falling.time,
        open=falling.open,
        high=highs,
        low=falling.low,
        close=closes,
    )
    evaluation = run(jumped)
    assert evaluation.signal is None and evaluation.state is SetupState.FORMING
    assert evaluation.note == "against the year"


def test_inside_the_channel_there_is_no_signal() -> None:
    evaluation = run(daily(1, breakout=False, drift=0.0))
    assert evaluation.signal is None
    assert evaluation.state in (SetupState.NONE, SetupState.FORMING)


def test_short_history_waits() -> None:
    d1 = daily(1)
    evaluation = run(d1.slice(100, COUNT))
    assert evaluation.state is SetupState.NONE and evaluation.signal is None
    assert "missing" in evaluation.conditions[0].detail


def test_the_entry_hour_is_an_h4_close() -> None:
    with pytest.raises(ValidationError):
        TrendFollowingParams(entry_hour=10)
    with pytest.raises(ValidationError):
        TrendFollowingParams(sl_atr=0.1)


def test_it_is_registered_off_with_its_own_magic() -> None:
    assert STRATEGIES["trend_following"] is TrendFollowing
    assert not on_by_default("trend_following")
    assert MAGIC_NUMBERS["trend_following"] == 26_070_010
    assert strategy_for_magic(26_070_010) == "trend_following"
    assert len(set(MAGIC_NUMBERS.values())) == len(MAGIC_NUMBERS)


def test_its_trailing_stop_is_on_by_default_and_a_saved_choice_wins() -> None:
    config = ExecutionConfig()
    rules = config.management_for("trend_following")
    assert rules.trailing_atr == 7.0 and rules.trailing_start_r == 1.0
    assert config.management_for("trend_pullback") == ManagementSettings()
    saved = ExecutionConfig(management={"trend_following": ManagementSettings()})
    assert saved.management_for("trend_following").trailing_atr == 0.0
