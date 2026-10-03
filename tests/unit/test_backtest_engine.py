"""The backtest engine (spec C8): the live pipeline, risk and execution replayed on history;
entries at the next bar open, money that adds up, no look-ahead, the same result twice."""

import math

import numpy as np
import pytest

from app.backtest.engine import END_REASON, BacktestResult, BacktestSetup, History, run_backtest
from app.domain.signals import Direction, OrderType, SignalState
from app.strategies.base import Condition, Evaluation, SetupState
from app.strategies.context import MarketContext
from app.strategies.registry import create_strategy
from app.strategies.trend_pullback import TrendPullback
from tests.unit.backtest_helpers import DAY, frames_from_m5, noisy_history
from tests.unit.strategy_helpers import WEDNESDAY

START = WEDNESDAY - 6 * DAY
SIGNAL_BAR = WEDNESDAY + 10 * 3600  # Wednesday 10:00 UTC, London and New York open


class ScriptedBuy(TrendPullback):
    """Buys at market once, when the M15 bar opened at SIGNAL_BAR closes (test only)."""

    def evaluate(self, ctx: MarketContext) -> Evaluation:
        rule = Condition("scripted bar", ctx.bar_time == SIGNAL_BAR)
        if not rule.passed:
            return self.result(ctx, SetupState.NONE, [rule])
        price = float(ctx.entry.close[-1])
        signal = self.make_signal(
            ctx,
            Direction.LONG,
            OrderType.MARKET,
            entry=price,
            sl=price - 0.0020,
            tp=price + 0.0040,
            reason="scripted buy",
            expires_at=self.bars_later(ctx, 2),
        )
        return self.result(ctx, SetupState.READY, [rule], [signal])


def both() -> tuple:
    return (create_strategy("trend_pullback"), create_strategy("london_breakout"))


def test_the_replay_trades_and_the_money_adds_up() -> None:
    history = noisy_history(26, 1)
    result = run_backtest(history, BacktestSetup(strategies=both(), start=START, end=WEDNESDAY))
    assert result.bars == 6 * 288 and len(result.equity) == result.bars
    assert result.analyses >= 6 * 96
    assert result.trades and all(math.isfinite(t.close_price) for t in result.trades)
    net = sum(t.net_profit for t in result.trades)
    assert result.end_balance == pytest.approx(result.start_balance + net, abs=0.05)
    assert result.equity[-1] == pytest.approx(result.end_balance)
    assert all(record.trace.complete for record in result.signals)
    states = result.counts()
    assert states.get(SignalState.CLOSED.value, 0) == len(result.trades)
    for trade in result.trades:
        assert trade.r_multiple is not None and trade.risk_money and trade.risk_money > 0
        assert trade.strategy in ("trend_pullback", "london_breakout")


def test_a_market_entry_fills_at_the_next_bar_open() -> None:
    history = noisy_history(27, 2, end=WEDNESDAY + DAY)
    setup = BacktestSetup(strategies=(ScriptedBuy(),), start=WEDNESDAY, end=WEDNESDAY + DAY)
    result = run_backtest(history, setup)
    assert len(result.trades) == 1, result.counts()
    trade = result.trades[0]
    m5 = history.bars["M5"]
    decided = SIGNAL_BAR + 900  # the signal bar's close is the next M5 bar's open
    index = int(np.searchsorted(m5.time, decided))
    ask = float(m5.open[index]) + 8 * 1e-5  # bar spread 8 points
    assert trade.open_time == decided
    assert trade.open_price == pytest.approx(ask + 1e-5)  # plus 1 point slippage
    signal = result.signals[0].signal
    assert signal.created_at == decided
    assert signal.entry == pytest.approx(round(float(m5.close[index - 1]), 5))


def test_future_bars_never_change_the_past() -> None:
    history = noisy_history(26, 3)
    setup = BacktestSetup(strategies=both(), start=START, end=WEDNESDAY - DAY)
    first = run_backtest(history, setup)
    changed = {}
    for timeframe, series in history.bars.items():
        cut = int(np.searchsorted(series.time, WEDNESDAY - DAY))
        close = series.close.copy()
        close[cut:] = close[cut:] * 1.05  # a different future
        changed[timeframe] = type(series)(
            series.symbol,
            series.timeframe,
            series.time,
            series.open,
            np.maximum(series.high, close),
            np.minimum(series.low, close),
            close,
            series.volume,
            series.spread,
            series.server_time,
        )
    other = History(history.symbol, history.broker_symbol, history.spec, history.clock, changed)
    second = run_backtest(other, setup)
    assert [r.signal.id for r in first.signals] == [r.signal.id for r in second.signals]

    def closed(result: BacktestResult) -> list[tuple[float, float]]:
        return [(t.open_price, t.net_profit) for t in result.trades if t.exit_reason != END_REASON]

    assert closed(first) == closed(second)


def test_the_same_setup_gives_the_same_result() -> None:
    history = noisy_history(24, 4)
    setup = BacktestSetup(strategies=both(), start=WEDNESDAY - 3 * DAY, end=WEDNESDAY)
    one, two = run_backtest(history, setup), run_backtest(history, setup)
    assert [(t.open_time, t.net_profit) for t in one.trades] == [
        (t.open_time, t.net_profit) for t in two.trades
    ]
    assert np.array_equal(one.equity, two.equity)


def test_a_cancelled_run_stops_and_says_so() -> None:
    history = noisy_history(24, 5)
    seen: list[tuple[int, int]] = []
    setup = BacktestSetup(strategies=both(), start=WEDNESDAY - 3 * DAY, end=WEDNESDAY)
    result = run_backtest(
        history,
        setup,
        progress=lambda done, total: seen.append((done, total)),
        cancelled=lambda: len(seen) >= 1,
    )
    assert result.cancelled and result.bars == 288
    assert any("cancelled" in note for note in result.notes)


def test_bad_setups_are_refused() -> None:
    history = noisy_history(20, 6)
    with pytest.raises(ValueError):
        run_backtest(history, BacktestSetup(strategies=(), start=START, end=WEDNESDAY))
    with pytest.raises(ValueError):
        run_backtest(history, BacktestSetup(strategies=both(), start=WEDNESDAY, end=WEDNESDAY))
    nothing = frames_from_m5(history.bars["M5"].slice(0, 0))
    empty = History("X", "X", history.spec, history.clock, nothing)
    with pytest.raises(ValueError):
        run_backtest(empty, BacktestSetup(strategies=both(), start=START, end=WEDNESDAY))
