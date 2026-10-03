"""The backtest broker (spec C8) on hand-made bars: fills, the same-candle rule, gaps,
pending orders, expiry, slippage, commission and swap."""

import math

import pytest

from app.backtest.costs import BacktestCosts, margin, profit, quote_to_account
from app.backtest.market import ReplayMarket
from app.brokers.backtest_broker import BacktestBroker, ReplayBar
from app.brokers.paper_broker import DEAL_REASON_SL, DEAL_REASON_TP
from app.brokers.requests import OrderPlan
from app.core.execution_settings import ExecutionSettings
from app.domain.history import summarize_position
from app.domain.signals import Direction, OrderType
from app.strategies.registry import strategy_for_magic
from tests.unit.backtest_helpers import fx_spec

MAGIC = 26_070_001
MONDAY = 1_790_553_600  # Monday 2026-09-28 00:00 UTC (server time = UTC here)
DAY = 86_400
SPREAD = 0.0001


def make(
    *,
    slippage: int = 0,
    commission: float = 0.0,
    swap_long: float = 0.0,
) -> tuple[ReplayMarket, BacktestBroker]:
    costs = BacktestCosts(
        start_balance=10_000,
        slippage_points=slippage,
        commission_per_lot=commission,
        swap_long_per_lot=swap_long,
    )
    market = ReplayMarket({"EURUSD": fx_spec()}, costs, strategy_for_magic)
    settings = ExecutionSettings(
        paper_slippage_points=slippage,
        paper_commission_per_lot=commission,
        paper_start_balance=10_000,
    )
    broker = BacktestBroker(
        market,
        lambda: settings,
        swap_per_lot=costs.swap_per_lot,
        triple_weekday=costs.triple_swap_weekday,
    )
    market.broker = broker
    return market, broker


def bar(o: float, h: float, low: float, c: float, at: int = MONDAY) -> ReplayBar:
    return ReplayBar("EURUSD", at, at + 300, o, h, low, c, SPREAD)


def plan(
    direction: Direction = Direction.LONG,
    order_type: OrderType = OrderType.MARKET,
    *,
    price: float = 1.10010,
    sl: float = 1.09900,
    tp: float = 1.10200,
    expiration: int | None = None,
) -> OrderPlan:
    return OrderPlan(
        symbol="EURUSD",
        direction=direction,
        order_type=order_type,
        volume=1.0,
        price=price,
        sl=sl,
        tp=tp,
        magic=MAGIC,
        comment="test",
        deviation=10,
        filling=1,
        expiration=expiration,
    )


def buy_at_market(market: ReplayMarket, broker: BacktestBroker, **changes: float) -> int:
    market.set_quote("EURUSD", 1.10000, SPREAD, MONDAY)
    result = broker.open(plan(**changes))  # type: ignore[arg-type]
    assert result.ok
    return result.position


def test_sl_and_tp_inside_one_candle_count_as_a_loss() -> None:
    market, broker = make()
    ticket = buy_at_market(market, broker)
    broker.step_bar(bar(1.10050, 1.10300, 1.09800, 1.10100, MONDAY + 300))
    summary = summarize_position(list(broker.deals(ticket) or []))
    assert summary is not None and summary.closed
    assert summary.close_price == pytest.approx(1.09900)
    assert summary.profit == pytest.approx(-110.0)  # entry at the ask 1.10010
    assert broker.deals(ticket)[-1].reason == DEAL_REASON_SL  # type: ignore[index]


def test_take_profit_on_the_bid_high() -> None:
    market, broker = make()
    ticket = buy_at_market(market, broker)
    broker.step_bar(bar(1.10050, 1.10199, 1.10000, 1.10100, MONDAY + 300))
    assert broker.positions([MAGIC])  # 1.10199 is short of the TP
    broker.step_bar(bar(1.10100, 1.10210, 1.10050, 1.10150, MONDAY + 600))
    deals = list(broker.deals(ticket) or [])
    assert deals[-1].reason == DEAL_REASON_TP and deals[-1].price == pytest.approx(1.10200)


def test_a_short_stop_is_hit_by_the_ask() -> None:
    market, broker = make()
    market.set_quote("EURUSD", 1.10000, SPREAD, MONDAY)
    ticket = broker.open(plan(Direction.SHORT, sl=1.10100, tp=1.09800, price=1.1)).position
    # The bid high 1.10095 plus the spread reaches the stop at 1.10100.
    broker.step_bar(bar(1.10000, 1.10095, 1.09950, 1.10000, MONDAY + 300))
    deals = list(broker.deals(ticket) or [])
    assert deals[-1].reason == DEAL_REASON_SL and deals[-1].price == pytest.approx(1.10100)


def test_a_weekend_gap_past_the_stop_fills_at_the_open_with_slippage() -> None:
    market, broker = make(slippage=2)
    ticket = buy_at_market(market, broker)
    broker.step_bar(bar(1.09700, 1.09800, 1.09600, 1.09750, MONDAY + 3 * DAY))
    deals = list(broker.deals(ticket) or [])
    assert deals[-1].reason == DEAL_REASON_SL
    assert deals[-1].price == pytest.approx(1.09698)  # the open minus 2 points


def test_a_buy_stop_fills_at_its_price_and_only_the_stop_counts_in_that_bar() -> None:
    market, broker = make(slippage=1)
    market.set_quote("EURUSD", 1.10000, SPREAD, MONDAY)
    order = plan(
        order_type=OrderType.STOP,
        price=1.10100,
        sl=1.09900,
        tp=1.10150,
    )
    placed = broker.open(order)
    assert placed.ok and placed.placed
    # The ask runs 1.10050 -> 1.10170: fills at 1.10101 and passes the TP in the same bar,
    # which is not counted (the order of the moves inside the bar is unknown).
    broker.step_bar(bar(1.09950, 1.10160, 1.09940, 1.10060, MONDAY + 300))
    positions = broker.positions([MAGIC]) or []
    assert len(positions) == 1 and positions[0].price_open == pytest.approx(1.10101)
    broker.step_bar(bar(1.10060, 1.10160, 1.10050, 1.10100, MONDAY + 600))
    deals = list(broker.deals(placed.order) or [])
    assert deals[-1].reason == DEAL_REASON_TP


def test_a_gap_past_a_buy_stop_fills_at_the_worse_open() -> None:
    market, broker = make()
    market.set_quote("EURUSD", 1.10000, SPREAD, MONDAY)
    placed = broker.open(plan(order_type=OrderType.STOP, price=1.10100, sl=1.0990, tp=1.1050))
    broker.step_bar(bar(1.10200, 1.10250, 1.10150, 1.10220, MONDAY + 300))
    positions = broker.positions([MAGIC]) or []
    assert positions[0].ticket == placed.order
    assert positions[0].price_open == pytest.approx(1.10210)  # the ask at the open


def test_an_order_past_its_expiry_is_dropped() -> None:
    market, broker = make()
    market.set_quote("EURUSD", 1.10000, SPREAD, MONDAY)
    broker.open(plan(order_type=OrderType.STOP, price=1.10100, expiration=MONDAY + 600))
    broker.step_bar(bar(1.10000, 1.10020, 1.09990, 1.10000, MONDAY + 300))
    assert broker.orders([MAGIC])
    events = broker.step_bar(bar(1.10000, 1.10300, 1.09990, 1.10200, MONDAY + 600))
    assert not broker.orders([MAGIC]) and not broker.positions([MAGIC])
    assert any("expired" in line for line in events)


def test_commission_is_half_on_entry_and_half_on_exit() -> None:
    market, broker = make(commission=7.0)
    ticket = buy_at_market(market, broker)
    assert broker.balance == pytest.approx(10_000 - 3.5)
    broker.step_bar(bar(1.10050, 1.10300, 1.10000, 1.10100, MONDAY + 300))
    summary = summarize_position(list(broker.deals(ticket) or []))
    assert summary is not None and summary.commission == pytest.approx(-7.0)
    assert broker.balance == pytest.approx(10_000 - 7.0 + 190.0)


def test_swap_is_charged_per_night_and_three_times_on_wednesday() -> None:
    market, broker = make(swap_long=-2.0)
    start = MONDAY
    market.set_quote("EURUSD", 1.10000, SPREAD, start)
    ticket = broker.open(plan(sl=1.05, tp=1.15)).position
    for day in range(4):  # Tue, Wed, Thu (after the Wednesday night), Fri
        broker.step_bar(bar(1.1, 1.1001, 1.0999, 1.1, start + (day + 1) * DAY))
    positions = broker.positions([MAGIC]) or []
    assert positions[0].swap == pytest.approx(-2.0 * (1 + 1 + 3 + 1))
    market.set_quote("EURUSD", 1.10000, SPREAD, start + 4 * DAY + 300)
    broker.close(positions[0])
    summary = summarize_position(list(broker.deals(ticket) or []))
    assert summary is not None and summary.swap == pytest.approx(-12.0)


def test_profit_is_converted_like_mt5() -> None:
    costs = BacktestCosts(account_currency="USD")
    eurusd, usdjpy, eurgbp = fx_spec("EURUSD"), fx_spec("USDJPY"), fx_spec("EURGBP")
    assert profit(eurusd, costs, Direction.LONG, 1.0, 1.1, 1.101) == pytest.approx(100.0)
    assert profit(usdjpy, costs, Direction.SHORT, 1.0, 150.0, 149.0) == pytest.approx(
        100_000 / 149.0,
    )
    assert quote_to_account(eurgbp, costs, 0.85) is None
    with_rate = costs.model_copy(update={"quote_rate": 1.3})
    assert profit(eurgbp, with_rate, Direction.LONG, 1.0, 0.85, 0.851) == pytest.approx(130.0)
    assert margin(eurusd, costs, 1.0, 1.1) == pytest.approx(1_100.0)
    assert margin(usdjpy, costs, 1.0, 150.0) == pytest.approx(1_000.0)


def test_the_bar_spread_has_a_floor_and_an_extra() -> None:
    costs = BacktestCosts(min_spread_points=5, extra_spread_points=2)
    assert costs.spread_points(3) == 7 and costs.spread_points(10) == 12
    assert costs.spread_points(math.nan) == 7


def test_weekend_nights_carry_no_swap() -> None:
    market, broker = make(swap_long=-2.0)
    friday = MONDAY + 4 * DAY
    market.set_quote("EURUSD", 1.10000, SPREAD, friday + 3600)
    broker.open(plan(sl=1.05, tp=1.15))
    broker.step_bar(bar(1.1, 1.1001, 1.0999, 1.1, friday + 3 * DAY))  # Monday
    positions = broker.positions([MAGIC]) or []
    assert positions[0].swap == pytest.approx(-2.0)  # only Friday night
