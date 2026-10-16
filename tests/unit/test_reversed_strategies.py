"""Reversed strategies: the opposite trade of each built-in one (16 October 2026)."""

from pathlib import Path

from app.backtest.service import BacktestRequest
from app.core.strategy_settings import load_strategy_settings
from app.domain.orders import short_id
from app.domain.signals import Direction, OrderType
from app.strategies.london_breakout import LondonBreakout
from app.strategies.registry import (
    MAGIC_NUMBERS,
    STRATEGIES,
    create_strategy,
    on_by_default,
    strategy_for_magic,
)
from app.strategies.reversed import REVERSED, LondonBreakoutReversed, is_reversed
from tests.unit.strategy_helpers import context, frames, london_days


def test_the_london_pair_becomes_two_limits_with_sl_and_tp_swapped() -> None:
    ctx = context(frames(london_days()))
    original = LondonBreakout().evaluate(ctx)
    flipped = LondonBreakoutReversed().evaluate(ctx)
    assert flipped.strategy == "london_breakout_reversed" and len(flipped.signals) == 2
    for before in original.signals:
        after = next(s for s in flipped.signals if s.entry == before.entry)
        assert after.direction is not before.direction
        assert before.order_type is OrderType.STOP and after.order_type is OrderType.LIMIT
        assert (after.sl, after.tp) == (before.tp, before.sl)
        assert after.valid_prices() and after.expires_at == before.expires_at
        assert after.strategy == "london_breakout_reversed" and after.id != before.id
        assert after.reason.startswith("REVERSED london_breakout")
        assert after.features["reversed_of"] == "london_breakout"
        assert short_id(after.id).startswith("tw-rev") and len(short_id(after.id)) <= 31
    groups = {s.features["oco_group"] for s in flipped.signals}
    assert len(groups) == 1 and ":london_breakout_reversed:" in str(next(iter(groups)))
    sell = next(s for s in flipped.signals if s.direction is Direction.SHORT)
    assert sell.entry > next(s for s in flipped.signals if s.direction is Direction.LONG).entry


def test_every_reversed_strategy_is_registered_off_with_its_own_magic() -> None:
    assert len(REVERSED) == 5
    for kind in REVERSED:
        name = kind.name
        assert is_reversed(name) and STRATEGIES[name] is kind
        assert not on_by_default(name) and not kind.example
        inner = kind.inner_kind
        assert MAGIC_NUMBERS[name] == MAGIC_NUMBERS[inner.name] + 100
        assert strategy_for_magic(MAGIC_NUMBERS[name]) == name
        assert kind.params_model is inner.params_model
        built = create_strategy(name)
        assert built.params_hash != inner().params_hash
    assert len(set(MAGIC_NUMBERS.values())) == len(MAGIC_NUMBERS)


def test_reversed_strategies_start_off_and_the_backtest_leaves_them_out(tmp_path: Path) -> None:
    settings = load_strategy_settings(tmp_path)
    assert not any(is_reversed(name) for name in settings.enabled())
    request = BacktestRequest.model_validate({"start": "2026-01-01", "end": "2026-02-01"})
    assert request.strategies and not any(is_reversed(name) for name in request.strategies)
    chosen = BacktestRequest.model_validate(
        {"start": "2026-01-01", "end": "2026-02-01", "strategies": ["trend_pullback_reversed"]},
    )
    assert chosen.strategies == ["trend_pullback_reversed"]
