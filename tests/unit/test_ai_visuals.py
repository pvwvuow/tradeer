"""The data of the AI Lab's chart cards (docs/NOCURVE_V2.md 20e4): only real records, an
empty shape when there are too few."""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.analytics.ai_visuals import (
    FAN_PATHS,
    MIN_FOR_FAN,
    Candle,
    candles,
    equity_curve,
    markers,
    max_drawdown_percent,
    monte_carlo_fan,
    r_histogram,
    trade_returns,
    trade_stats,
    trade_table,
)


@dataclass(frozen=True)
class FakeTrade:
    close_time: float
    net_profit: float
    r_multiple: float | None
    open_time: float = 0.0
    symbol: str = "EURUSD"
    strategy: str = "trend_pullback"
    direction: str = "buy"
    open_price: float = 1.1


TRADES = [
    FakeTrade(300.0, -50.0, -1.0, 250.0),
    FakeTrade(100.0, 100.0, 2.0, 50.0),
    FakeTrade(200.0, -100.0, -1.0, 150.0, direction="sell"),
]


def test_equity_drawdown_and_stats() -> None:
    assert equity_curve(TRADES, 1000.0) == [1000.0, 1100.0, 1000.0, 950.0]
    assert math.isclose(max_drawdown_percent([1000.0, 1100.0, 1000.0, 950.0]), 150 / 1100 * 100)
    stats = trade_stats(TRADES, 1000.0)
    assert stats.trades == 3 and stats.net == -50.0 and stats.net_percent == -5.0
    assert stats.expectancy_r == 0.0 and stats.with_r == 3
    empty = trade_stats([], 0.0)
    assert empty.net_percent is None and empty.expectancy_r is None


def test_the_r_histogram_keeps_the_tails() -> None:
    found = r_histogram([-7.0, -1.0, -0.9, 0.2, 2.0, 9.0, None, float("nan")])
    assert found.total == 6 and found.losing == 3
    assert len(found.edges) == len(found.counts) + 1 and found.edges[0] == -3.0
    assert found.counts[0] == 1 and found.counts[-1] == 1  # -7 and +9 in the outer bins
    assert found.counts[4] == 2  # -1.0 and -0.9 in [-1.0, -0.5)
    assert r_histogram([]).mean is None


def test_the_fan_is_reproducible_and_needs_enough_trades() -> None:
    returns = trade_returns(TRADES, 1000.0)
    assert math.isclose(returns[0], 0.1) and math.isclose(returns[1], -100 / 1100)
    assert monte_carlo_fan(returns).empty  # fewer than MIN_FOR_FAN trades
    many = [0.01, -0.005, 0.02, -0.01, 0.015, -0.02, 0.01] * 3
    assert len(many) >= MIN_FOR_FAN
    fan = monte_carlo_fan(many)
    assert len(fan.paths) == FAN_PATHS and all(path[0] == 0.0 for path in fan.paths)
    assert len(fan.middle) == len(many) + 1 and fan.low[-1] <= fan.middle[-1] <= fan.high[-1]
    finals = {round(path[-1], 9) for path in fan.paths}
    assert len(finals) == 1  # a shuffle keeps every trade once: the same final result
    assert math.isclose(fan.real[-1], fan.paths[0][-1])
    assert monte_carlo_fan(many) == fan and fan.drawdown_worst >= fan.drawdown_median


def test_the_table_candles_and_markers() -> None:
    rows = trade_table(TRADES, limit=2)
    assert [row[5] for row in rows] == ["-50.00", "-100.00"] and rows[1][3] == "SELL"
    assert rows[0][4] == "-1.00"
    bars = candles([0.0, 60.0, 120.0], [1.0, 2.0, 3.0], [2, 3, 4], [0, 1, 2], [2, 3, 4], count=2)
    assert bars == [Candle(60.0, 2.0, 3.0, 1.0, 3.0), Candle(120.0, 3.0, 4.0, 2.0, 4.0)]
    trades = [
        FakeTrade(200.0, 5.0, 1.0, 130.0),
        FakeTrade(200.0, -5.0, -1.0, 30.0),  # before the shown bars
        FakeTrade(200.0, 5.0, 1.0, 70.0, symbol="GBPUSD"),
    ]
    found = markers(bars, trades, "EUR/USD", 60.0)
    assert len(found) == 1 and found[0].index == 1 and found[0].buy and found[0].win
    assert markers([], trades, "EURUSD", 60.0) == []
