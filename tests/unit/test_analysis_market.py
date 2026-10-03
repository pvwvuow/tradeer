import math

import numpy as np

from app.analysis.correlation import correlation_matrix
from app.analysis.currency_strength import currency_strength, pair_move
from app.analysis.patterns import find_patterns
from app.analysis.spread import SpreadStatus, spread_status, typical_spread
from app.analysis.volatility import regime_for, volatility
from tests.unit.analysis_helpers import bars_from_closes, ohlc_bars, trending


def test_volatility_regime_and_daily_range_use() -> None:
    assert [regime_for(value) for value in (10, 50, 80, 97)] == ["low", "normal", "high", "extreme"]
    assert regime_for(math.nan) == "unknown"
    rows = [(1.0, 1.01, 0.99, 1.0)] * 120 + [(1.0, 1.04, 0.96, 1.0)]
    daily = ohlc_bars(rows, "D1")
    today = ohlc_bars([(1.0, 1.005, 0.995, 1.0)], "M5")
    result = volatility(daily, None, today)
    assert result.regime == "extreme"
    assert round(result.adr, 6) == round((19 * 0.02 + 0.08) / 20, 6)
    assert round(result.adr_used_percent, 1) == round(0.01 / result.adr * 100, 1)
    assert "volatility extreme" in result.text()


def test_correlation_of_same_and_opposite_moves() -> None:
    base = trending(200, 0.0, seed=3)
    bars = {
        "EURUSD": bars_from_closes(base, symbol="EURUSD"),
        "GBPUSD": bars_from_closes(base * 1.2, symbol="GBPUSD"),
        "USDCHF": bars_from_closes(2.2 - base, symbol="USDCHF"),
    }
    matrix = correlation_matrix(bars)
    assert round(matrix.value("EURUSD", "GBPUSD"), 6) == 1.0
    assert matrix.value("EURUSD", "USDCHF") < -0.99
    assert matrix.returns == 120
    assert matrix.strong_pairs()[0][2] > 0.99
    short = correlation_matrix({"A": bars_from_closes(base[:10]), "B": bars_from_closes(base[:10])})
    assert math.isnan(short.value("A", "B"))


def test_currency_strength_ranks_the_strongest_first() -> None:
    up = bars_from_closes(np.linspace(1.0, 1.05, 60), symbol="EURUSD")
    down = bars_from_closes(np.linspace(150.0, 147.0, 60), symbol="USDJPY")
    assert pair_move(up) > 0
    ranked = currency_strength({"EURUSD": up, "USDJPY": down})
    # Per ATR, USDJPY fell further than EURUSD rose: the yen is strongest, the dollar weakest.
    assert ranked[0].currency == "JPY" and ranked[0].rank == 1
    assert ranked[-1].currency == "USD"
    assert {item.currency for item in ranked} == {"EUR", "USD", "JPY"}


def _with_last_spread(last: int) -> list[int]:
    return [10] * 47 + [last]


def test_spread_compares_the_last_bar_with_the_typical_spread_of_this_hour() -> None:
    bars = bars_from_closes(trending(48, 0.0), spread=10)
    assert typical_spread(bars, 5) == 10.0
    closes = trending(48, 0.0)

    def status(live: float, last: int) -> SpreadStatus:
        bars = bars_from_closes(closes, spread=_with_last_spread(last))
        return spread_status(live, bars, 5 * 3600)

    assert status(12, 12).status == "normal"
    assert status(18, 18).status == "wide"
    wide = status(40, 35)
    assert wide.status == "very wide"
    assert "typical 10" in wide.text() and "40 points now" in wide.text()
    assert spread_status(5, bars_from_closes([1.0], spread=0), 0).status == "unknown"


def test_a_live_spread_above_the_bar_minimums_is_not_called_wide() -> None:
    # Found on a real account: MT5 bars store the MINIMUM spread (3 points here) while the live
    # spread is 15. The old rule said "very wide" all day; the bars themselves say normal.
    bars = bars_from_closes(trending(48, 0.0), spread=3)
    status = spread_status(15, bars, 5 * 3600)
    assert status.status == "normal"
    assert status.current_points == 15 and status.bar_points == 3


def test_candle_patterns_on_the_last_closed_bar() -> None:
    calm = [(1.0, 1.002, 0.998, 1.0)] * 20
    engulf_rows = [*calm, (1.001, 1.0015, 0.9985, 0.999), (0.9985, 1.004, 0.998, 1.003)]
    engulfing = find_patterns(ohlc_bars(engulf_rows))
    assert [pattern.text() for pattern in engulfing] == ["H1 bullish engulfing"]
    pin_rows = [*calm, (1.0, 1.0005, 0.995, 0.9998), (1.0, 1.0006, 0.994, 1.0004)]
    pin = find_patterns(ohlc_bars(pin_rows))
    assert "H1 bullish pin bar" in [pattern.text() for pattern in pin]
    inside = find_patterns(ohlc_bars([*calm, (1.0, 1.005, 0.995, 1.0), (1.0, 1.001, 0.999, 1.0)]))
    assert [pattern.text() for pattern in inside] == ["H1 inside bar"]
    assert find_patterns(ohlc_bars(calm[:5])) == []
