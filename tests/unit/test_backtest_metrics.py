"""Backtest metrics (spec C8, G1) against hand-calculated numbers."""

import math

import numpy as np
import pytest

from app.backtest.engine import BacktestTrade
from app.backtest.metrics import (
    DAY,
    compute_metrics,
    daily_returns,
    drawdown_curve,
    max_drawdown,
    sharpe_sortino,
    summary_lines,
)
from tests.unit.strategy_helpers import WEDNESDAY

MONDAY = WEDNESDAY - 2 * DAY


def trade(
    index: int,
    net: float,
    r: float,
    *,
    session: str = "London",
    strategy: str = "a",
) -> BacktestTrade:
    opened = float(MONDAY + index * DAY + 9 * 3600)
    return BacktestTrade(
        trade_id=f"t{index}",
        signal_id=f"s{index}",
        strategy=strategy,
        symbol="EURUSD",
        direction="buy",
        volume=0.1,
        open_time=opened,
        close_time=opened + 3600,
        open_price=1.1,
        close_price=1.1,
        sl=1.09,
        tp=1.12,
        risk_money=50.0,
        profit=net + 1.0,
        commission=-1.0,
        swap=0.0,
        net_profit=net,
        r_multiple=r,
        exit_reason="take profit" if net > 0 else "stop loss",
        session=session,
        mfe_r=None,
        mae_r=None,
    )


TRADES = [
    trade(0, 100.0, 1.0),
    trade(1, -50.0, -1.0, session="New York"),
    trade(2, 200.0, 2.0, strategy="b"),
    trade(3, -50.0, -1.0),
    trade(4, -50.0, -1.0, session="New York"),
    trade(5, 100.0, 1.0, strategy="b"),
]
TIMES = np.array([MONDAY + i * DAY for i in range(7)], dtype=np.float64)
EQUITY = np.array([10_000, 10_100, 10_050, 10_250, 10_200, 10_150, 10_250], dtype=np.float64)


def test_the_trade_numbers_match_the_hand_calculation() -> None:
    m = compute_metrics(TRADES, TIMES, EQUITY, 10_000.0)
    assert (m.trades, m.wins, m.losses) == (6, 3, 3)
    assert m.win_rate == pytest.approx(0.5)
    assert m.net_profit == pytest.approx(250.0)
    assert (m.gross_profit, m.gross_loss) == (pytest.approx(400.0), pytest.approx(-150.0))
    assert m.profit_factor == pytest.approx(400 / 150)
    assert m.expectancy_money == pytest.approx(250 / 6)
    assert m.expectancy_r == pytest.approx(1 / 6)
    assert m.average_win == pytest.approx(400 / 3) and m.average_loss == pytest.approx(-50.0)
    assert m.average_win_r == pytest.approx(4 / 3) and m.average_loss_r == pytest.approx(-1.0)
    assert m.payoff_ratio == pytest.approx(400 / 3 / 50)
    assert m.return_percent == pytest.approx(2.5)
    assert (m.longest_losing_streak, m.longest_winning_streak) == (2, 1)
    assert m.commission == pytest.approx(-6.0) and m.end_balance == pytest.approx(10_250.0)


def test_drawdown_depth_and_duration() -> None:
    dd = max_drawdown(TIMES, EQUITY)
    assert dd.depth == pytest.approx(100.0)
    assert dd.depth_percent == pytest.approx(100 / 10_250 * 100)
    assert dd.peak_time == TIMES[3] and dd.trough_time == TIMES[5]
    assert dd.duration_seconds == pytest.approx(3 * DAY) and dd.recovered
    curve = drawdown_curve(EQUITY)
    assert curve[5] == pytest.approx((10_150 / 10_250 - 1) * 100) and curve.max() == 0.0
    open_end = max_drawdown(TIMES[:6], EQUITY[:6])
    assert not open_end.recovered and open_end.duration_seconds == pytest.approx(2 * DAY)


def test_sharpe_and_sortino_from_daily_returns() -> None:
    returns = daily_returns(TIMES, EQUITY, 10_000.0)
    closes = np.r_[10_000.0, EQUITY]
    assert np.allclose(returns, np.diff(closes) / closes[:-1])
    sharpe, sortino = sharpe_sortino(returns)
    mean = returns.mean()
    assert sharpe == pytest.approx(mean / returns.std(ddof=1) * math.sqrt(252))
    downside = math.sqrt(np.mean(np.minimum(returns, 0) ** 2))
    assert sortino == pytest.approx(mean / downside * math.sqrt(252))
    assert sharpe_sortino(np.array([0.01])) == (None, None)


def test_breakdowns_by_session_weekday_strategy_and_month() -> None:
    m = compute_metrics(TRADES, TIMES, EQUITY, 10_000.0)
    sessions = {g.key: g for g in m.by_session}
    assert sessions["New York"].trades == 2 and sessions["New York"].net_profit == -100.0
    assert sessions["London"].win_rate == pytest.approx(3 / 4)
    assert [g.key for g in m.by_weekday][:3] == ["1 Monday", "2 Tuesday", "3 Wednesday"]
    strategies = {g.key: g for g in m.by_strategy}
    assert strategies["b"].profit_factor is None and strategies["b"].expectancy_r == 1.5
    assert [g.key for g in m.by_month] == ["2026-09", "2026-10"]


def test_few_trades_give_a_warning_and_json_is_clean() -> None:
    m = compute_metrics(TRADES, TIMES, EQUITY, 10_000.0)
    assert m.warnings and "Fewer than 100 trades (6)" in m.warnings[0]
    assert not compute_metrics(TRADES, TIMES, EQUITY, 10_000.0, minimum_trades=5).warnings
    empty = compute_metrics([], TIMES[:0], EQUITY[:0], 10_000.0)
    assert empty.trades == 0 and empty.profit_factor is None and empty.expectancy_r is None
    data = empty.to_json()
    assert data["max_drawdown"]["depth"] == 0.0 and data["sharpe"] is None
    lines = summary_lines(m, "USD")
    assert lines[0].startswith("Trades 6: 3 won, 3 lost") and "USD" in lines[1]
