"""The lab strategies (7 October 2026: run every strategy at once on the demo account and
compare them): exact rules, pure, every rule recorded, a signal on a market made for it."""

from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from app.analysis.bars import Bars
from app.domain.signals import Direction, OrderType
from app.strategies.base import Evaluation, SetupState, Strategy
from app.strategies.channel_breakout import ChannelBreakout, ChannelBreakoutParams
from app.strategies.ema_momentum import EmaMomentum, EmaMomentumParams
from app.strategies.range_reversion import RangeReversion, RangeReversionParams, bands
from app.strategies.registry import MAGIC_NUMBERS, STRATEGIES, create_strategy
from tests.unit.strategy_helpers import WEDNESDAY, bars, context, frames, trend_market, wave

COUNT = 6000


def steep_trend(direction: int) -> Bars:
    """A trend whose pushes close above the highs of the bars before them."""
    closes = wave(COUNT, drift=direction * 0.00008, amp=0.0015, period=32)
    return bars(closes, start=WEDNESDAY - COUNT * 900)


def range_market() -> Bars:
    """No trend: a slow swing with a faster one on top, so closes leave the band and return."""
    index = np.arange(COUNT, dtype=np.float64)
    closes = 1.1 + 0.0010 * np.sin(2 * np.pi * index / 48) + 0.0004 * np.sin(2 * np.pi * index / 7)
    return bars(closes, start=WEDNESDAY - COUNT * 900)


def signals(strategy: Strategy, m15: Bars, bars_back: int = 400) -> list[Evaluation]:
    found: list[Evaluation] = []
    for stop in range(len(m15), len(m15) - bars_back, -1):
        evaluation = strategy.evaluate(context(frames(m15.slice(0, stop))))
        if evaluation.signal is not None:
            found.append(evaluation)
    return found


def check(evaluation: Evaluation, name: str, side: Direction) -> None:
    signal = evaluation.signal
    assert signal is not None
    assert evaluation.state is SetupState.READY
    assert all(condition.passed for condition in evaluation.conditions)
    assert signal.direction is side and signal.order_type is OrderType.MARKET
    assert signal.valid_prices() and signal.strategy == name and signal.reason
    assert signal.expires_at > signal.created_at


def test_the_channel_breakout_buys_and_sells_a_fresh_break_with_the_trend() -> None:
    for direction, side in ((1, Direction.LONG), (-1, Direction.SHORT)):
        found = signals(ChannelBreakout(), steep_trend(direction))
        assert found
        for evaluation in found:
            check(evaluation, "channel_breakout", side)
            assert evaluation.signal is not None and abs(evaluation.signal.rr - 2.0) < 0.02
        times = [evaluation.signal.bar_time for evaluation in found if evaluation.signal]
        assert min(np.diff(sorted(times))) > 900  # a fresh break, not every bar after it


def test_the_ema_momentum_trades_the_cross_with_the_trend() -> None:
    for direction, side in ((1, Direction.LONG), (-1, Direction.SHORT)):
        found = signals(EmaMomentum(), trend_market(direction))
        assert found
        for evaluation in found:
            check(evaluation, "ema_momentum", side)
            assert evaluation.signal is not None and abs(evaluation.signal.rr - 1.5) < 0.02


def test_the_range_reversion_fades_a_stretch_back_to_the_middle() -> None:
    found = signals(RangeReversion(), range_market())
    sides = {evaluation.signal.direction for evaluation in found if evaluation.signal}
    assert sides == {Direction.LONG, Direction.SHORT}
    for evaluation in found:
        signal = evaluation.signal
        assert signal is not None
        check(evaluation, "range_reversion", signal.direction)
        assert signal.rr >= 1.0 - 0.02


def test_the_range_reversion_stays_out_of_a_trend() -> None:
    evaluation = RangeReversion().evaluate(context(frames(steep_trend(1))))
    assert evaluation.signal is None
    assert evaluation.note == "H1 is trending"


def test_the_band_is_the_mean_and_deviations_of_the_window() -> None:
    close = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    middle, half = bands(close, 3, 2.0)
    assert np.isnan(middle[1]) and np.isnan(half[1])
    assert middle[-1] == pytest.approx(4.0)
    assert half[-1] == pytest.approx(2.0 * np.std([3.0, 4.0, 5.0]))


def test_the_lab_strategies_are_pure_and_wait_for_history() -> None:
    m15 = steep_trend(1)
    for strategy in (ChannelBreakout(), EmaMomentum(), RangeReversion()):
        ctx = context(frames(m15))
        assert strategy.evaluate(ctx) == strategy.evaluate(ctx)
        short = strategy.evaluate(context(frames(m15.slice(0, 100))))
        assert short.state is SetupState.NONE and short.signal is None
        assert "missing" in short.conditions[0].detail


def test_every_strategy_has_its_own_magic_number_and_validated_params() -> None:
    assert set(MAGIC_NUMBERS) == set(STRATEGIES)
    assert len(set(MAGIC_NUMBERS.values())) == len(MAGIC_NUMBERS)
    assert MAGIC_NUMBERS["trend_pullback"] == 26_070_001  # never change an old number
    assert MAGIC_NUMBERS["london_breakout"] == 26_070_002
    assert 26_070_098 not in MAGIC_NUMBERS.values()  # the demo test's
    for params in (ChannelBreakoutParams, EmaMomentumParams, RangeReversionParams):
        with pytest.raises(ValidationError):
            params(expiry_bars=0)
    built = create_strategy("ema_momentum", {"reward_r": 2.0})
    assert built.params.model_dump()["reward_r"] == 2.0
