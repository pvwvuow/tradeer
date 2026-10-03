"""Model features (spec C10): known at signal time, aligned with the trade, versioned."""

import math
from dataclasses import replace

import pytest

from app.backtest.engine import BacktestSetup, run_backtest
from app.core.clock import BrokerClock
from app.domain.signals import Direction
from app.ml import features as feature_set
from app.strategies.context import MarketContext, build_context
from app.strategies.registry import create_strategy
from tests.unit.backtest_helpers import DAY, noisy_history
from tests.unit.signal_helpers import make_signal
from tests.unit.strategy_helpers import WEDNESDAY, analysis_at, london_days


def _context() -> MarketContext:
    m15 = london_days()
    analysis, now = analysis_at(m15, len(m15))
    clock = BrokerClock.assumed()
    return build_context(analysis, "M15", clock=clock, now=now, point=1e-5, spread=2e-5)


def test_every_feature_is_computed_and_aligned_with_the_trade() -> None:
    ctx = _context()
    price = float(ctx.entry.close[-1])
    moment = float(ctx.close_time)
    buy = make_signal(entry=price, sl=price - 0.001, tp=price + 0.002, created_at=moment)
    sell = replace(buy, direction=Direction.SHORT, sl=price + 0.001, tp=price - 0.002)
    long_values = feature_set.compute(buy, ctx)
    short_values = feature_set.compute(sell, ctx)
    assert set(long_values) == set(feature_set.FEATURE_NAMES)
    known = [name for name, value in long_values.items() if math.isfinite(value)]
    assert len(known) >= len(feature_set.FEATURE_NAMES) - 2
    assert long_values["direction"] == 1.0 and short_values["direction"] == -1.0
    for name in ("ema20_dist_atr", "ema50_dist_atr", "rsi_aligned", "ema20_slope_atr"):
        assert long_values[name] == pytest.approx(-short_values[name])
    assert long_values["adx"] == short_values["adx"]
    assert long_values["strategy_trend_pullback"] == 1.0
    assert long_values["strategy_london_breakout"] == 0.0
    assert 0.0 <= long_values["body_ratio"] <= 1.0
    assert long_values["news_minutes"] == feature_set.NEWS_CAP_MINUTES


def test_features_survive_the_signal_store_format() -> None:
    ctx = _context()
    price = float(ctx.entry.close[-1])
    signal = make_signal(entry=price, sl=price - 0.001, tp=price + 0.002)
    values = feature_set.compute(signal, ctx)
    stored = feature_set.stored(values)
    assert all(key.startswith(feature_set.PREFIX) for key in stored)
    again = feature_set.from_signal({**stored, "rr": 2.0, "session": "London"})
    for name, value in values.items():
        if math.isfinite(value):
            assert again[name] == pytest.approx(value, abs=1e-5)
        else:
            assert math.isnan(again[name])
    assert feature_set.has_features(stored) and not feature_set.has_features({"rr": 2.0})


def test_the_schema_hash_follows_names_order_and_version() -> None:
    names = feature_set.FEATURE_NAMES
    assert feature_set.schema_hash() == feature_set.schema_hash(names)
    assert feature_set.schema_hash(names[::-1]) != feature_set.schema_hash()
    assert feature_set.schema_hash(names, version=99) != feature_set.schema_hash()
    assert len(set(names)) == len(names)


def test_backtest_signals_carry_the_model_features() -> None:
    history = noisy_history(26, 1)
    strategies = (create_strategy("trend_pullback"), create_strategy("london_breakout"))
    setup = BacktestSetup(strategies=strategies, start=WEDNESDAY - 3 * DAY, end=WEDNESDAY)
    result = run_backtest(history, setup)
    assert result.signals
    for record in result.signals:
        assert feature_set.has_features(record.signal.features)
        steps = [step.name for step in record.trace.steps if step.stage == "features"]
        assert "ml.direction" in steps
