"""Phase 7 acceptance: sizing is right for every symbol type (spec C6, G3 row 7).

The loss per lot comes from MT5's order_calc_profit (here the FakeMT5, which converts JPY and
EUR like MT5 does); the tick value is never used.
"""

import math
from dataclasses import replace

from app.domain.signals import Direction
from app.domain.sizing import (
    SizingInput,
    SizingResult,
    VolumeRules,
    commission_per_lot,
    round_down,
    size_position,
)
from app.mt5 import api
from app.mt5.risk_reads import loss_per_lot, margin_for, order_action
from app.risk.risk_manager import volume_rules
from tests.unit.risk_helpers import connected, spec_of

LOTS = VolumeRules(0.01, 100.0, 0.01)


def sized(loss: float, capital: float = 10_000.0, **changes: object) -> SizingResult:
    values: dict[str, object] = {
        "capital": capital,
        "capital_basis": "equity",
        "risk_percent": 0.5,
        "loss_per_lot": loss,
        "commission_per_lot": 0.0,
        "volume": LOTS,
        "currency": "USD",
    }
    values.update(changes)
    return size_position(SizingInput(**values))  # type: ignore[arg-type]


def test_eurusd_on_a_usd_account() -> None:
    fake = connected()
    loss = loss_per_lot(fake, "EURUSD.m", Direction.LONG, 1.10000, 1.09900)
    assert loss is not None and math.isclose(loss, 100.0)
    result = sized(loss)
    assert result.ok and result.volume == 0.5
    assert math.isclose(result.risk_money, 50.0)


def test_usdjpy_converts_the_yen_loss_to_dollars() -> None:
    fake = connected()
    loss = loss_per_lot(fake, "USDJPY.m", Direction.SHORT, 151.234, 151.734)
    assert loss is not None and math.isclose(loss, 50_000 / 151.234, rel_tol=1e-4)
    result = sized(loss)
    assert result.ok and result.volume == 0.15


def test_xauusd_ignores_a_wrong_tick_value() -> None:
    """FIBO reported a 0.1 USD tick value for gold where a point is worth 1 USD."""
    fake = connected()
    spec = replace(spec_of(fake, "XAUUSD.m"), tick_value=0.1)
    loss = loss_per_lot(fake, "XAUUSD.m", Direction.LONG, 2385.42, 2375.42)
    assert loss is not None and math.isclose(loss, 1000.0)
    result = sized(loss, volume=volume_rules(spec))
    assert result.ok and result.volume == 0.05
    from_tick_value = 10.0 / spec.tick_size * spec.tick_value
    assert from_tick_value == 100.0  # what the tick value would claim: 10x too small


def test_a_eur_account_converts_the_dollar_loss() -> None:
    fake = connected(currency="EUR")
    loss = loss_per_lot(fake, "EURUSD.m", Direction.LONG, 1.10000, 1.09900)
    assert loss is not None and math.isclose(loss, 100.0 / 1.08345, rel_tol=1e-4)
    result = sized(loss, currency="EUR")
    assert result.ok and result.volume == 0.54


def test_the_minimum_lot_above_the_allowed_risk_is_rejected() -> None:
    result = sized(100.0, capital=100.0)
    assert not result.ok and result.volume == 0.0
    assert "smallest lot 0.01" in result.reason
    assert result.lines[-1].startswith("rejected")


def test_commission_is_part_of_the_loss_and_volume_rounds_down() -> None:
    result = sized(200.0, commission_per_lot=7.0)
    assert result.volume == 0.24  # 50 / 207 = 0.2415
    assert math.isclose(result.risk_money, 0.24 * 207.0)
    assert result.risk_money <= result.allowed_risk
    assert "order_calc_profit" in result.text()


def test_the_maximum_lot_and_coarse_steps_are_respected() -> None:
    assert sized(0.01, volume=VolumeRules(0.01, 5.0, 0.01)).volume == 5.0
    assert sized(30.0, volume=VolumeRules(0.1, 50.0, 0.1)).volume == 1.6
    assert round_down(0.29999999, 0.01, 2) == 0.29
    assert round_down(0.3, 0.1, 1) == 0.3
    assert VolumeRules(0.25, 10, 0.25).decimals == 2


def test_unknown_numbers_reject_instead_of_guessing() -> None:
    assert not sized(math.nan).ok
    assert not sized(-5.0).ok
    assert not sized(100.0, capital=0.0).ok
    assert not sized(100.0, volume=VolumeRules(math.nan, math.nan, math.nan)).ok


def test_mt5_failures_give_none() -> None:
    fake = connected()
    fake.calc_fails = True
    assert loss_per_lot(fake, "EURUSD.m", Direction.LONG, 1.1, 1.099) is None
    assert margin_for(fake, "EURUSD.m", Direction.LONG, 0.5, 1.1) is None
    assert fake.trading_calls == []


def test_margin_comes_from_order_calc_margin() -> None:
    fake = connected()
    margin = margin_for(fake, "EURUSD.m", Direction.LONG, 0.5, 1.1)
    assert margin is not None and math.isclose(margin, 50_000 * 1.1 / 100)
    assert order_action(Direction.SHORT) == api.ORDER_TYPE_SELL


def test_commission_estimate_from_history() -> None:
    assert commission_per_lot(-14.0, 2.0) == 7.0
    assert commission_per_lot(0.0, 0.0) is None
