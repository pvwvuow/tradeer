"""The two example strategies (spec C4): exact rules, pure, every rule recorded."""

import pytest
from pydantic import ValidationError

from app.core.clock import BrokerClock
from app.domain.signals import Direction, OrderType
from app.strategies.base import SetupState, params_hash
from app.strategies.context import build_context, closed_by
from app.strategies.london_breakout import LondonBreakout, LondonBreakoutParams
from app.strategies.registry import STRATEGIES, create_strategy, strategy_info
from app.strategies.trend_pullback import TrendPullback, TrendPullbackParams
from tests.unit.strategy_helpers import (
    WEDNESDAY,
    analysis_at,
    context,
    frames,
    london_days,
    trend_market,
)


def first_signal(direction: int) -> tuple[object, object]:
    m15 = trend_market(direction)
    strategy = TrendPullback()
    for stop in range(len(m15), len(m15) - 200, -1):
        evaluation = strategy.evaluate(context(frames(m15.slice(0, stop))))
        if evaluation.signal is not None:
            return evaluation, m15.slice(0, stop)
    raise AssertionError("no trend pullback signal in the last 200 bars")


def test_trend_pullback_fires_with_the_trend_and_explains_every_rule() -> None:
    for direction, side in ((1, Direction.LONG), (-1, Direction.SHORT)):
        evaluation, _ = first_signal(direction)
        signal = evaluation.signal
        assert evaluation.state is SetupState.READY
        assert signal.direction is side and signal.order_type is OrderType.MARKET
        assert signal.valid_prices() and abs(signal.rr - 2.0) < 0.01
        assert all(condition.passed for condition in evaluation.conditions)
        assert signal.strategy == "trend_pullback" and signal.reason
        assert signal.expires_at > signal.created_at


def test_strategies_are_pure() -> None:
    evaluation, m15 = first_signal(1)
    again = TrendPullback().evaluate(context(frames(m15)))
    assert again == evaluation


def test_too_little_history_waits() -> None:
    short = trend_market(1, count=200)
    evaluation = TrendPullback().evaluate(context(frames(short)))
    assert evaluation.state is SetupState.NONE and evaluation.signal is None
    assert "missing" in evaluation.conditions[0].detail


def test_london_breakout_places_an_oco_stop_pair_at_the_open() -> None:
    evaluation = LondonBreakout().evaluate(context(frames(london_days())))
    assert evaluation.state is SetupState.READY and len(evaluation.signals) == 2
    buy, sell = sorted(evaluation.signals, key=lambda item: item.direction.value)
    assert buy.direction is Direction.LONG and sell.direction is Direction.SHORT
    assert buy.order_type is OrderType.STOP and sell.order_type is OrderType.STOP
    assert buy.features["oco_group"] == sell.features["oco_group"]
    assert buy.id != sell.id and buy.entry > sell.entry
    assert buy.expires_at == WEDNESDAY + 10 * 3600  # 11:00 London summer time
    assert all(abs(item.rr - 1.5) < 0.01 for item in evaluation.signals)


def test_london_breakout_skips_a_range_that_is_too_wide() -> None:
    wide = london_days(asia_amp=0.006)
    evaluation = LondonBreakout().evaluate(context(frames(wide)))
    assert evaluation.signal is None
    assert any(not condition.passed for condition in evaluation.conditions)


def test_params_are_validated_and_hashed() -> None:
    with pytest.raises(ValidationError):
        LondonBreakoutParams(range_start="09:00")
    with pytest.raises(ValidationError):
        TrendPullbackParams(reward_r=0)
    default = params_hash("trend_pullback", "1.0.0", TrendPullbackParams())
    assert default == TrendPullback().params_hash
    assert default != TrendPullback(TrendPullbackParams(reward_r=3)).params_hash


def test_the_registry_builds_every_strategy() -> None:
    assert sorted(STRATEGIES) == [
        "channel_breakout",
        "ema_momentum",
        "london_breakout",
        "range_reversion",
        "trend_pullback",
    ]
    info = strategy_info("london_breakout")
    assert info.example and info.entry_timeframe == "M15" and info.defaults["reward_r"] == 1.5
    built = create_strategy("trend_pullback", {"reward_r": 2.5})
    assert built.params.model_dump()["reward_r"] == 2.5
    with pytest.raises(KeyError):
        create_strategy("martingale")


def test_the_context_only_holds_closed_higher_timeframe_bars() -> None:
    m15 = london_days()
    analysis, now = analysis_at(m15, len(m15) - 2)
    ctx = build_context(analysis, "M15", clock=BrokerClock.assumed(), now=now, point=1e-5)
    close = ctx.close_time
    for name, bars in ctx.bars.items():
        if name != "M15" and len(bars):
            assert int(bars.time[-1]) + bars.seconds <= close
    assert "M5" not in ctx.bars
    assert len(closed_by(ctx.bars["H1"], 0)) == 0
