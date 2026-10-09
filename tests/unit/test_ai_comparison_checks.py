"""The three rules of the AI Lab verdict as the comparison card shows them (docs/NOCURVE_V2.md
20e2): the same predicates as `compare_runs`, each with its numbers."""

from __future__ import annotations

from app.analytics.ai_import import (
    MIN_TRADES,
    RunSummary,
    compare_runs,
    comparison_checks,
    drawdown_limit,
)


def summary(trades: int, expectancy: float | None, drawdown: float) -> RunSummary:
    return RunSummary(trades, 0.5, 100.0, expectancy, 1.2, drawdown)


def test_the_checks_match_the_verdict() -> None:
    current = summary(142, 0.21, 5.8)
    cases = [
        summary(151, 0.18, 6.4),  # lower expectancy
        summary(151, 0.25, 6.4),  # better
        summary(MIN_TRADES - 1, 0.5, 1.0),  # too few trades
        summary(151, 0.25, 9.0),  # much deeper
        summary(151, None, 1.0),  # no expectancy
    ]
    for proposed in cases:
        checks = comparison_checks(current, proposed)
        assert [check.key for check in checks] == ["trades", "expectancy", "drawdown"]
        assert all(check.ok for check in checks) == compare_runs(current, proposed).better


def test_the_numbers_of_each_check() -> None:
    current = summary(142, 0.21, 5.8)
    trades, expectancy, drawdown = comparison_checks(current, summary(151, 0.18, 6.4))
    assert trades.ok and trades.value == "151"
    assert not expectancy.ok and expectancy.value == "0.180 <= 0.210"
    assert drawdown.ok and drawdown.value == "6.40% <= 8.25%"
    assert drawdown_limit(current) == 5.8 * 1.25 + 1.0
    _, better, deeper = comparison_checks(current, summary(151, 0.3, 8.3))
    assert better.ok and better.value == "0.300 > 0.210"
    assert not deeper.ok and deeper.value == "8.30% > 8.25%"
    _, missing, _ = comparison_checks(summary(10, None, 1.0), summary(40, 0.1, 1.0))
    assert missing.ok and missing.value == "0.100 > n/a"
