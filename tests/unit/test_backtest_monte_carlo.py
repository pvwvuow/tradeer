"""Monte-Carlo reshuffling (spec C8): drawdown distribution and risk of ruin."""

import numpy as np
import pytest

from app.backtest.monte_carlo import simulate, summary_lines, trade_returns
from tests.unit.test_backtest_metrics import TRADES


def test_returns_are_on_the_balance_before_each_trade() -> None:
    found = trade_returns(TRADES, 10_000.0)
    assert found[0] == pytest.approx(0.01)
    assert found[1] == pytest.approx(-50 / 10_100)
    assert found[2] == pytest.approx(200 / 10_050)


def test_shuffling_keeps_the_final_result_and_spreads_the_drawdown() -> None:
    returns = np.array([0.02] * 10 + [-0.01] * 10)
    result = simulate(returns, runs=500, method="shuffle", seed=1)
    final = (np.prod(1 + returns) - 1) * 100
    assert result.return_percentiles["5"] == pytest.approx(final)
    assert result.return_percentiles["95"] == pytest.approx(final)
    worst = (1 - 0.99**10) * 100  # all ten losses in a row
    assert result.drawdown_percentiles["99"] <= worst + 1e-9
    assert result.drawdown_percentiles["50"] >= 1.0 - 1e-9  # at least one loss somewhere
    assert result.risk_of_ruin == 0.0 and len(result.max_drawdowns) == 500


def test_risk_of_ruin_counts_runs_past_the_ruin_level() -> None:
    returns = np.array([-0.1] * 7 + [0.15] * 7)
    result = simulate(returns, runs=400, method="shuffle", ruin_percent=40.0, seed=2)
    # Ruin needs at least 5 losses before enough wins: some orders do it, some do not.
    assert 0.0 < result.risk_of_ruin < 1.0
    sure = simulate(returns, runs=50, ruin_percent=1.0)
    assert sure.risk_of_ruin == 1.0


def test_bootstrap_changes_the_final_result_and_is_seeded() -> None:
    returns = np.array([0.02, -0.01, 0.03, -0.02, 0.01])
    one = simulate(returns, runs=300, method="bootstrap", seed=5)
    two = simulate(returns, runs=300, method="bootstrap", seed=5)
    assert one == two
    assert one.return_percentiles["5"] < one.return_percentiles["95"]


def test_no_trades_and_the_summary() -> None:
    empty = simulate(np.zeros(0))
    assert empty.trades == 0 and summary_lines(empty) == ["Monte-Carlo: no trades to reshuffle"]
    lines = summary_lines(simulate(np.array([0.01, -0.02, 0.03]), runs=100))
    assert lines[0].startswith("Monte-Carlo (shuffle, 100 runs of 3 trades)")
