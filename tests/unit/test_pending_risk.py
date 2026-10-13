"""Pending orders in the risk picture, and the legs of one signal as one trade (0.50.4)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.signals import Direction, OrderType, Signal
from app.mt5 import api
from app.mt5.risk_reads import read_picture
from app.risk.limits import (
    AccountPicture,
    Candidate,
    LimitCheck,
    OpenPosition,
    candidate_group,
    check_trade,
    open_risk_money,
    trade_group,
    usage,
)
from app.risk.limits_state import AccountMoney, LimitsState
from app.risk.settings import RiskSettings
from tests.unit.risk_helpers import connected
from tests.unit.signal_helpers import MORNING

SYMBOL = "EURUSD.m"
CHANNEL = "channel_a"
SETTINGS = RiskSettings(max_open_trades=3, max_open_per_symbol=1, max_open_per_strategy=2)


def entry(
    ticket: int,
    *,
    strategy: str = CHANNEL,
    direction: Direction = Direction.LONG,
    sl: float = 1.07,
    risk: float | None = 20.0,
    pending: bool = False,
) -> OpenPosition:
    return OpenPosition(
        ticket=ticket,
        symbol=SYMBOL,
        direction=direction,
        volume=0.1,
        price_open=1.08,
        sl=sl,
        profit=0.0,
        magic=1,
        risk_money=risk,
        base="EUR",
        quote="USD",
        strategy=strategy,
        pending=pending,
    )


def picture_of(*entries: OpenPosition) -> AccountPicture:
    return AccountPicture(
        currency="USD",
        balance=10_000.0,
        equity=10_000.0,
        margin=0.0,
        margin_free=10_000.0,
        margin_level=0.0,
        positions=tuple(item for item in entries if not item.pending),
        money=AccountMoney("2026-10-13", 10_000.0, 10_000.0, 0.0, 0.0),
        bot_entries_today=0,
        manual_entries_today=0,
        read_at=0.0,
        orders=tuple(item for item in entries if item.pending),
    )


def candidate(
    *,
    group: str = "",
    pending: bool = False,
    strategy: str = CHANNEL,
    direction: Direction = Direction.LONG,
    risk: float = 20.0,
) -> Candidate:
    return Candidate(
        symbol=SYMBOL,
        strategy=strategy,
        direction=direction,
        risk_money=risk,
        base="EUR",
        quote="USD",
        stop_points=100.0,
        stops_level=0,
        margin_required=10.0,
        group=group,
        pending=pending,
    )


def named(name: str, checks: list[LimitCheck]) -> LimitCheck:
    return next(check for check in checks if check.name == name)


def signal_of(order_type: OrderType, features: dict[str, float | str]) -> Signal:
    return Signal(
        id="signal-1",
        symbol="EURUSD",
        timeframe="M15",
        direction=Direction.LONG,
        order_type=order_type,
        entry=1.08,
        sl=1.07,
        tp=1.10,
        reason="test",
        strategy=CHANNEL,
        strategy_version="1",
        params_hash="hash",
        bar_time=0,
        created_at=0.0,
        expires_at=0.0,
        features=features,
    )


def test_read_picture_includes_pending_orders() -> None:
    fake = connected()
    fake.pending_orders.append(
        SimpleNamespace(
            ticket=11,
            type=api.ORDER_TYPE_SELL_STOP,
            magic=7,
            volume_current=0.1,
            volume_initial=0.1,
            price_open=1.08,
            sl=1.083,
            symbol=SYMBOL,
            comment="",
        ),
    )
    picture = read_picture(
        fake,
        day="2026-10-13",
        day_start_server=0,
        read_at=float(MORNING),
        strategy_of=lambda magic: "london_breakout" if magic == 7 else "",
        cache={},
    )
    assert picture is not None
    assert picture.positions == ()
    (order,) = picture.orders
    assert order.pending
    assert order.direction is Direction.SHORT
    assert order.strategy == "london_breakout"
    assert order.risk_money == pytest.approx(30.0)
    assert order.group == "london_breakout|EURUSD.m|pending"
    assert fake.trading_calls == []


def test_a_pending_order_counts_toward_the_symbol() -> None:
    picture = picture_of(entry(1, strategy="london_breakout", pending=True))
    checks = check_trade(candidate(), picture, LimitsState(), SETTINGS)
    assert not named("open trades on the symbol", checks).passed
    assert named("total open risk", checks).value == pytest.approx(0.4)


def test_the_next_leg_joins_its_open_trade() -> None:
    group = trade_group(CHANNEL, SYMBOL, Direction.LONG, 1.07, pending=False)
    picture = picture_of(entry(1))
    checks = check_trade(candidate(group=group), picture, LimitsState(), SETTINGS)
    on_symbol = named("open trades on the symbol", checks)
    assert on_symbol.passed
    assert on_symbol.value == 1
    assert "joins an open trade" in on_symbol.detail
    assert named("total open risk", checks).value == pytest.approx(0.4)


def test_another_trade_on_the_symbol_is_still_blocked() -> None:
    picture = picture_of(entry(1))
    checks = check_trade(candidate(), picture, LimitsState(), SETTINGS)
    assert not named("open trades on the symbol", checks).passed


def test_legs_count_as_one_open_trade() -> None:
    picture = picture_of(entry(1), entry(2), entry(3, strategy=""))
    found = usage(picture, LimitsState(), RiskSettings(count_manual_trades=True))
    assert found.open_trades == 2
    assert found.open_risk_percent == pytest.approx(0.6)


def test_positions_without_a_stop_loss_never_merge() -> None:
    picture = picture_of(entry(1, sl=0.0, risk=None), entry(2, sl=0.0, risk=None))
    assert usage(picture, LimitsState(), SETTINGS).open_trades == 2


def test_only_the_bigger_side_of_a_pending_pair_counts() -> None:
    long = entry(1, pending=True, risk=20.0)
    short = entry(2, direction=Direction.SHORT, sl=1.09, pending=True, risk=30.0)
    held = [long, short, entry(3, risk=10.0)]
    assert open_risk_money(held) == pytest.approx(40.0)
    group = trade_group(CHANNEL, SYMBOL, Direction.LONG, 1.07, pending=True)
    second = candidate(group=group, pending=True, risk=25.0)
    assert open_risk_money(held, second) == pytest.approx(55.0)


def test_candidate_groups() -> None:
    leg = signal_of(OrderType.MARKET, {"leg_group": "g1", "risk_share": 0.5})
    assert candidate_group(leg, SYMBOL) == f"{CHANNEL}|{SYMBOL}|long|1.07"
    assert candidate_group(signal_of(OrderType.MARKET, {}), SYMBOL) == ""
    limit = candidate_group(signal_of(OrderType.LIMIT, {}), SYMBOL)
    assert limit == f"{CHANNEL}|{SYMBOL}|pending"
