"""Shadow results of channel signals (docs/SIGNAL_DESK.md 3.5, phase 21d2): every parsed
signal is followed from the message time plus the latency, with the spread paid and the
stop loss first when both are inside one bar."""

from dataclasses import replace

import numpy as np

from app.analysis.bars import Bars
from app.domain.signals import Direction, OrderType
from app.signals.parse import parse
from app.signals.shadow import HORIZON_SECONDS, ShadowLeg, ShadowState, follow, legs_of

START = 1_791_331_200.0  # 2026-10-07 00:00 UTC
POINT = 0.00001


def path(closes: list[float], size: float = 0.0004, spread: int = 0) -> Bars:
    """M1 bars through these closes, each opening at the last close."""
    close = np.array(closes)
    opens = np.concatenate(([close[0]], close[:-1]))
    return Bars.build(
        "EURUSD",
        "M1",
        time=START + 60 * np.arange(len(close)),
        open=opens,
        high=np.maximum(opens, close) + size / 2,
        low=np.minimum(opens, close) - size / 2,
        close=close,
        spread=np.full(len(close), spread),
    )


def market(direction: Direction, sl: float, tp: float | None, start: float = START) -> ShadowLeg:
    return ShadowLeg(direction, OrderType.MARKET, float("nan"), sl, tp, start, start + 3600)


def test_a_market_buy_fills_at_the_next_open_and_reaches_its_target() -> None:
    bars = path([1.1000 + 0.0005 * i for i in range(20)])
    found = follow(market(Direction.LONG, 1.0980, 1.1030), bars, POINT, START + 3600)
    assert found.state is ShadowState.WIN and found.fill_price == 1.1000
    assert found.r == 1.5 and found.exit_price == 1.1030


def test_the_stop_comes_first_inside_one_bar_and_the_spread_is_paid() -> None:
    flat = path([1.1000] * 10, size=0.0040)
    both = follow(market(Direction.LONG, 1.0990, 1.1010), flat, POINT, START + 900)
    assert both.state is ShadowState.LOSS and both.r == -1.0
    costly = path([1.1000] * 3, size=0.0, spread=20)  # 2 pips
    buy = follow(market(Direction.LONG, 1.0990, None), costly, POINT, START + 900)
    assert buy.state is ShadowState.OPEN and buy.fill_price == 1.1002
    sell = follow(market(Direction.SHORT, 1.1001, None), costly, POINT, START + 900)
    assert sell.state is ShadowState.LOSS  # the ask is above the stop at once


def test_a_pending_entry_fills_when_reached_or_expires() -> None:
    down = [1.1000 - 0.0005 * i for i in range(10)]
    falling = path(down + [1.0960 + 0.001 * i for i in range(10)])
    limit = ShadowLeg(Direction.LONG, OrderType.LIMIT, 1.0980, 1.0940, 1.1000, START, START + 3600)
    found = follow(limit, falling, POINT, START + 3600)
    assert found.state is ShadowState.WIN and found.fill_price == 1.0980
    assert found.r == 0.5
    far = ShadowLeg(Direction.LONG, OrderType.LIMIT, 1.0500, 1.0400, 1.1000, START, START + 600)
    assert follow(far, falling, POINT, START + 3600).state is ShadowState.EXPIRED
    early = ShadowLeg(Direction.LONG, OrderType.LIMIT, 1.0500, 1.0400, 1.1, START, START + 86_400)
    assert follow(early, falling, POINT, START + 1_200).state is ShadowState.WAITING
    later = market(Direction.LONG, 1.09, 1.12, start=START + 99_999)
    assert follow(later, falling, POINT, START + 1_200).state is ShadowState.WAITING


def test_an_open_target_runs_until_the_week_is_over() -> None:
    week = int(HORIZON_SECONDS // 60) + 5
    bars = path([1.1000 + 0.000001 * i for i in range(week)], size=0.0)
    found = follow(market(Direction.LONG, 1.0900, None), bars, POINT, START + HORIZON_SECONDS)
    assert found.state is ShadowState.TIMEOUT and found.state.resolved
    assert found.r is not None and found.r > 0


def test_the_legs_of_a_parsed_signal() -> None:
    zone = parse("EURUSD buy 1.0950-1.0960 sl 1.0900 tp 1.1000 tp 1.1050")
    legs = legs_of(zone, START, 1.1000, 60)
    assert [leg.tp for leg in legs] == [1.1000, 1.1050]
    assert legs[0].order is OrderType.LIMIT and legs[0].entry == 1.0960
    assert legs[0].expires == START + 3600
    inside = legs_of(zone, START, 1.0955, 60)
    assert inside[0].order is OrderType.MARKET
    above = legs_of(parse("EURUSD buy 1.1100 sl 1.1000 tp 1.1200"), START, 1.1000, 60)
    assert above[0].order is OrderType.STOP and above[0].entry == 1.1100
    plain = legs_of(parse("EURUSD sell sl 1.1100 tp 1.0900"), START, 1.1000, 60)
    assert plain[0].order is OrderType.MARKET and plain[0].direction is Direction.SHORT
    assert legs_of(parse("EURUSD buy tp 1.12"), START, 1.1, 60) == ()


def test_the_channels_follow_ups_change_the_shadow() -> None:
    rising = path([1.1000 + 0.0002 * i for i in range(40)], size=0.0)
    leg = market(Direction.LONG, 1.0980, 1.1100)
    closed = follow(replace(leg, close_at=START + 600), rising, POINT, START + 3600)
    assert closed.state is ShadowState.CLOSED and closed.exit_time == START + 600
    assert closed.r == 0.9  # closed at the open 1.1018: 180 of the 200 points risked
    limit = ShadowLeg(Direction.LONG, OrderType.LIMIT, 1.0900, 1.0800, 1.1, START, START + 7200)
    cancelled = follow(replace(limit, close_at=START + 600), rising, POINT, START + 3600)
    assert cancelled.state is ShadowState.EXPIRED
    up = [1.1000 + 0.0005 * i for i in range(10)]
    turn = path(up + [1.1045 - 0.0005 * i for i in range(20)])
    safe = follow(replace(leg, break_even_at=START + 300), turn, POINT, START + 3600)
    assert safe.state is ShadowState.CLOSED and safe.r == 0.0
    assert follow(leg, turn, POINT, START + 3600).state is ShadowState.LOSS
