"""Labels (spec C10): the backtest's rules and costs, played forward per signal."""

import numpy as np
import pytest

from app.analysis.bars import Bars
from app.backtest.costs import BacktestCosts
from app.backtest.engine import BacktestSetup, run_backtest
from app.core.execution_settings import ExecutionConfig
from app.domain.signals import Direction, OrderType
from app.ml.labeler import LabelSettings, label_signal
from app.strategies.registry import create_strategy
from tests.unit.backtest_helpers import DAY, fx_spec, noisy_history
from tests.unit.signal_helpers import MORNING, make_signal
from tests.unit.strategy_helpers import WEDNESDAY

POINT = 1e-5
NO_SLIP = BacktestCosts(slippage_points=0)


def m5(rows: list[tuple[float, float, float, float]], spread: int = 0) -> Bars:
    data = np.asarray(rows, dtype=np.float64)
    return Bars.build(
        "EURUSD",
        "M5",
        time=MORNING + 300 * np.arange(len(rows)),
        open=data[:, 0],
        high=data[:, 1],
        low=data[:, 2],
        close=data[:, 3],
        spread=np.full(len(rows), spread),
    )


def test_a_market_buy_that_reaches_the_target_is_a_win() -> None:
    bars = m5([(1.1, 1.1005, 1.0995, 1.1003), (1.1003, 1.1021, 1.1001, 1.1020)])
    label = label_signal(make_signal(), bars, fx_spec(), NO_SLIP)
    assert label is not None and label.outcome == "win" and label.win
    assert label.entry_price == pytest.approx(1.1) and label.exit_price == pytest.approx(1.102)
    assert label.r == pytest.approx(2.0)  # entry 1.1000, SL 1.0990, TP 1.1020
    assert label.exit_time == MORNING + 600


def test_stop_and_target_in_one_candle_count_as_a_loss() -> None:
    bars = m5([(1.1, 1.1025, 1.0985, 1.1)])
    label = label_signal(make_signal(), bars, fx_spec(), NO_SLIP)
    assert label is not None and label.outcome == "loss" and not label.win
    assert label.r == pytest.approx(-1.0)


def test_a_gap_past_the_stop_fills_at_the_open_with_slippage() -> None:
    bars = m5([(1.1, 1.1002, 1.0998, 1.1), (1.0980, 1.0985, 1.0975, 1.098)])
    costs = BacktestCosts(slippage_points=2)
    label = label_signal(make_signal(), bars, fx_spec(), costs)
    assert label is not None and label.outcome == "loss"
    assert label.entry_price == pytest.approx(1.10002)  # the open plus slippage
    assert label.exit_price == pytest.approx(1.09798)
    assert label.r == pytest.approx((1.09798 - 1.10002) / 0.001)


def test_a_sell_closes_at_the_ask() -> None:
    signal = make_signal(direction=Direction.SHORT, sl=1.1010, tp=1.0980)
    bars = m5([(1.1, 1.1002, 1.0995, 1.0999), (1.0999, 1.1001, 1.0975, 1.0985)], spread=10)
    label = label_signal(signal, bars, fx_spec(), NO_SLIP)
    # the ask low is 1.0975 + 0.0001, still below the target
    assert label is not None and label.outcome == "win"
    assert label.entry_price == pytest.approx(1.1)


def test_commission_and_the_risk_in_money_set_r() -> None:
    bars = m5([(1.1, 1.1005, 1.0995, 1.1003), (1.1003, 1.1021, 1.1001, 1.1020)])
    costs = BacktestCosts(slippage_points=0, commission_per_lot=10.0)
    label = label_signal(make_signal(), bars, fx_spec(), costs)
    # 1 lot: +200 profit - 10 commission, risk 100 at the SL + 10 commission
    assert label is not None and label.r == pytest.approx(190 / 110, abs=1e-4)


def test_an_unfilled_pending_order_has_no_label() -> None:
    signal = make_signal(order_type=OrderType.STOP, entry=1.1010, sl=1.1000, tp=1.1030)
    bars = m5([(1.1, 1.1005, 1.0995, 1.1)] * 10)
    assert label_signal(signal, bars, fx_spec(), NO_SLIP) is None


def test_in_the_fill_bar_only_the_stop_counts() -> None:
    signal = make_signal(order_type=OrderType.STOP, entry=1.1010, sl=1.1000, tp=1.1030)
    bars = m5([(1.1005, 1.1035, 1.1004, 1.1020), (1.1020, 1.1032, 1.1015, 1.1030)])
    label = label_signal(signal, bars, fx_spec(), NO_SLIP)
    assert label is not None and label.outcome == "win" and label.bars_held == 2


def test_timeouts_follow_the_setting() -> None:
    rows = [(1.1, 1.1004, 1.0996, 1.1002)] * 30
    signal = make_signal()
    short = LabelSettings(max_bars=2)  # two M15 bars = six M5 bars
    found = label_signal(signal, m5(rows), fx_spec(), NO_SLIP, short)
    assert found is not None and found.outcome == "timeout" and found.win
    assert found.bars_held == 6 and found.r == pytest.approx(0.2)
    as_loss = short.model_copy(update={"timeout": "loss"})
    lost = label_signal(signal, m5(rows), fx_spec(), NO_SLIP, as_loss)
    assert lost is not None and not lost.win
    skip = short.model_copy(update={"timeout": "skip"})
    assert label_signal(signal, m5(rows), fx_spec(), NO_SLIP, skip) is None


def test_labels_agree_with_the_backtest_trades() -> None:
    history = noisy_history(26, 1)
    setup = BacktestSetup(
        strategies=(create_strategy("trend_pullback"), create_strategy("london_breakout")),
        start=WEDNESDAY - 6 * DAY,
        end=WEDNESDAY,
        execution=ExecutionConfig(),
    )
    result = run_backtest(history, setup)
    signals = {record.id: record.signal for record in result.signals}
    checked = 0
    for trade in result.trades:
        if trade.exit_reason not in ("stop loss", "take profit"):
            continue
        signal = signals[trade.signal_id]
        rules = LabelSettings(max_bars=2000)
        label = label_signal(signal, history.bars["M5"], history.spec, setup.costs, rules)
        assert label is not None
        assert label.exit_price == pytest.approx(trade.close_price, abs=2e-5)
        assert label.win == (trade.net_profit > 0)
        checked += 1
    assert checked >= 3
