"""The same-geometry base rate of the Signal desk's full check (docs/SIGNAL_DESK.md 2.5)."""

import numpy as np

from app.analysis.bars import Bars
from app.domain.signals import Direction, OrderType
from app.signals.base_rate import (
    MIN_SAMPLES,
    BaseRate,
    Geometry,
    TargetRate,
    base_rate,
    session_of,
)
from app.signals.plan import OrderPlan

WEDNESDAY = 1_791_331_200  # 2026-10-07 00:00 UTC
STEP = 900


def trend(count: int, move: float, size: float = 0.0010, spread: int = 0) -> Bars:
    """M15 bars that move `move` per bar with a range of `size` around the close."""
    close = 1.1000 + move * np.arange(count)
    return Bars.build(
        "EURUSD",
        "M15",
        time=WEDNESDAY + STEP * np.arange(count),
        open=close - move,
        high=close + size / 2,
        low=close - size / 2,
        close=close,
        spread=np.full(count, spread),
    )


def test_a_steady_rise_reaches_a_buys_target_and_a_sells_stop() -> None:
    bars = trend(800, 0.0002)
    buy = base_rate(bars, Geometry(Direction.LONG, 1.0, (1.0, 2.0)), 0.00001)
    sell = base_rate(bars, Geometry(Direction.SHORT, 1.0, (1.0,)), 0.00001)
    first = buy.targets[0]
    assert first.samples >= MIN_SAMPLES and first.losses == 0
    assert first.rate == 1.0 and buy.targets[1].rate == 1.0
    assert sell.targets[0].rate == 0.0
    assert buy.bars == 800 and buy.days == 8


def test_when_both_are_inside_one_bar_the_stop_came_first() -> None:
    bars = trend(400, 0.0, size=0.0010)
    found = base_rate(bars, Geometry(Direction.LONG, 0.1, (0.1,)), 0.00001)
    target = found.targets[0]
    assert target.wins == 0 and target.losses > 0


def test_the_spread_is_paid() -> None:
    flat = trend(400, 0.0, size=0.0010)
    costly = trend(400, 0.0, size=0.0010, spread=500)  # 5 pips: a buy starts deep in the loss
    geometry = Geometry(Direction.LONG, 3.0, (0.4,))
    free = base_rate(flat, geometry, 0.00001).targets[0]
    paid = base_rate(costly, geometry, 0.00001).targets[0]
    assert free.wins > 0
    assert paid.wins < free.wins


def test_too_little_data_has_no_percent() -> None:
    found = base_rate(trend(60, 0.0002), Geometry(Direction.LONG, 1.0, (1.0,)), 0.00001)
    assert found.targets[0].samples < MIN_SAMPLES and found.targets[0].rate is None
    empty = base_rate(trend(5, 0.0002), Geometry(Direction.LONG, 1.0, (1.0,)), 0.00001)
    assert empty.targets == (TargetRate(1.0),) and isinstance(empty, BaseRate)


def test_only_samples_of_the_same_session_count() -> None:
    bars = trend(2000, 0.0001)
    anywhere = base_rate(bars, Geometry(Direction.LONG, 1.0, (1.0,)), 0.00001)
    london = session_of(WEDNESDAY + 9 * 3600)
    inside = base_rate(bars, Geometry(Direction.LONG, 1.0, (1.0,), london), 0.00001)
    assert london
    assert 0 < inside.targets[0].samples < anywhere.targets[0].samples


def test_the_geometry_of_a_plan_is_measured_in_atr() -> None:
    plan = OrderPlan(
        "EURUSD",
        Direction.SHORT,
        OrderType.LIMIT,
        1.1000,
        1.1020,
        (1.0980, 1.0950),
    )
    found = Geometry.of(plan, 0.0010, WEDNESDAY + 9 * 3600)
    assert found is not None
    assert found.direction is Direction.SHORT
    assert round(found.sl_atr, 6) == 2.0
    assert tuple(round(value, 6) for value in found.tp_atr) == (2.0, 5.0)
    assert found.session == session_of(WEDNESDAY + 9 * 3600)
    assert Geometry.of(plan, float("nan"), WEDNESDAY) is None
