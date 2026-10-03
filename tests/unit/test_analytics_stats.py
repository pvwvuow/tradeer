"""Performance statistics (spec C11) against hand-checked numbers."""

import pytest

from app.analytics.stats import compute_stats, equity_curve, stat_rows, streaks
from tests.unit.analytics_helpers import DAY, HOUR, sample, trade


def test_the_headline_numbers_match_a_hand_calculation() -> None:
    stats = compute_stats(sample(), 1000.0)
    assert (stats.trades, stats.wins, stats.losses, stats.breakeven) == (5, 2, 3, 0)
    assert stats.win_rate == pytest.approx(0.4)
    assert stats.net_profit == pytest.approx(125.0)
    assert stats.gross_profit == pytest.approx(250.0) and stats.gross_loss == pytest.approx(-125.0)
    assert stats.profit_factor == pytest.approx(2.0)
    assert stats.expectancy_money == pytest.approx(25.0)
    assert stats.expectancy_r == pytest.approx(0.5)  # 125 / 5 / 50
    assert stats.average_win == pytest.approx(125.0)
    assert stats.average_loss == pytest.approx(-125.0 / 3)
    assert stats.payoff_ratio == pytest.approx(3.0)
    assert (stats.largest_win, stats.largest_loss) == (150.0, -50.0)
    assert stats.return_percent == pytest.approx(12.5)


def test_drawdown_recovery_and_streaks_by_hand() -> None:
    stats = compute_stats(sample(), 1000.0)
    # equity 1000, 1100, 1050, 1000, 1150, 1125: the deepest fall is 1100 -> 1000
    assert stats.max_drawdown == pytest.approx(100.0)
    assert stats.max_drawdown_percent == pytest.approx(100 / 1100 * 100)
    assert stats.recovery_factor == pytest.approx(1.25)
    # bottom at trade 2's close (2.5 h), back above 1100 at trade 3's close (3.5 h)
    assert stats.time_to_recover_seconds == pytest.approx(HOUR)
    assert (stats.longest_losing_streak, stats.longest_winning_streak) == (2, 1)
    assert stats.current_streak == -1
    assert streaks([trade(0, 5), trade(1, 5)])[2] == 2


def test_an_unrecovered_drawdown_and_few_trades_say_so() -> None:
    stats = compute_stats([trade(0, 100), trade(1, -150)], 1000.0)
    assert stats.time_to_recover_seconds is None
    assert any("mostly luck" in text for text in stats.warnings)
    rows = dict(stat_rows(stats, "USD"))
    assert rows["Time to recover"] == "not yet"
    assert rows["Net profit"] == "-50.00 USD"


def test_without_a_balance_no_percentages_are_made_up() -> None:
    stats = compute_stats(sample())
    assert stats.return_percent is None and stats.sharpe is None
    assert stats.max_drawdown == pytest.approx(100.0) and stats.max_drawdown_percent == 0.0
    assert any("No start balance" in text for text in stats.warnings)


def test_the_equity_curve_starts_at_the_first_open() -> None:
    curve = equity_curve(sample(), 1000.0)
    assert list(curve.equity) == [1000.0, 1100.0, 1050.0, 1000.0, 1150.0, 1125.0]
    assert curve.times[0] == sample()[0].open_time
    assert curve.drawdown_percent[3] == pytest.approx((1000 / 1100 - 1) * 100)


def test_sharpe_uses_daily_returns() -> None:
    days = [trade(int(i * 24), 10.0 if i % 2 else 20.0) for i in range(6)]
    stats = compute_stats(days, 1000.0)
    assert stats.sharpe is not None and stats.sharpe > 0
    assert stats.drawdown_duration_seconds == 0.0
    assert days[-1].close_time - days[0].open_time == pytest.approx(5 * DAY + 1800)
