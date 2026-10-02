import math

import numpy as np

from app.analysis import indicators


def close_to(values: np.ndarray, expected: list[float]) -> bool:
    return bool(np.allclose(values, expected, equal_nan=True, atol=1e-9))


def naive_adx(high: list[float], low: list[float], close: list[float], n: int) -> float:
    """Wilder's ADX written out step by step, as the reference."""
    trs, pdm, mdm = [], [], []
    for i in range(1, len(close)):
        up, down = high[i] - high[i - 1], low[i - 1] - low[i]
        pdm.append(up if up > down and up > 0 else 0.0)
        mdm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))

    def wilder(values: list[float]) -> list[float]:
        out = [sum(values[:n]) / n]
        for value in values[n:]:
            out.append((out[-1] * (n - 1) + value) / n)
        return out

    s_tr, s_p, s_m = wilder(trs), wilder(pdm), wilder(mdm)
    dx = []
    for t, p, m in zip(s_tr, s_p, s_m, strict=True):
        plus, minus = 100 * p / t, 100 * m / t
        dx.append(100 * abs(plus - minus) / (plus + minus) if plus + minus else 0.0)
    return wilder(dx)[-1]


def test_simple_and_exponential_averages_match_hand_calculations() -> None:
    values = np.arange(1.0, 11.0)
    assert close_to(indicators.sma(values, 3), [math.nan, math.nan, *range(2, 10)])
    assert close_to(indicators.ema(np.array([2.0, 4, 6, 8]), 2), [math.nan, 3, 5, 7])
    smoothed = indicators.rma(np.array([1.0, 2, 3, 4, 5]), 3)
    assert close_to(smoothed, [math.nan, math.nan, 2, 8 / 3, 31 / 9])


def test_short_input_gives_only_nan() -> None:
    assert np.all(np.isnan(indicators.ema(np.array([1.0, 2.0]), 5)))
    assert np.all(np.isnan(indicators.sma(np.array([1.0]), 2)))
    assert math.isnan(indicators.last(np.array([])))


def test_true_range_and_atr() -> None:
    high = np.array([10.0, 12.0, 11.0])
    low = np.array([9.0, 10.5, 8.0])
    close = np.array([9.5, 11.0, 10.0])
    assert close_to(indicators.true_range(high, low, close), [1.0, 2.5, 3.0])
    assert close_to(indicators.atr(high, low, close, 2), [math.nan, 1.75, 2.375])


def test_adx_matches_the_step_by_step_reference() -> None:
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0.1, 1.0, 120))
    high = close + rng.uniform(0.2, 1.0, 120)
    low = close - rng.uniform(0.2, 1.0, 120)
    adx, plus, minus = indicators.adx(high, low, close, 14)
    assert math.isclose(adx[-1], naive_adx(list(high), list(low), list(close), 14), rel_tol=1e-9)
    assert np.isnan(adx[:27]).all() and np.isfinite(adx[27:]).all()
    steady = np.arange(50, dtype=np.float64)
    adx_up, plus_up, minus_up = indicators.adx(steady + 1, steady - 1, steady, 14)
    assert plus_up[-1] > minus_up[-1] and adx_up[-1] > 50


def test_slope_percentile_and_returns() -> None:
    line = np.arange(10, dtype=np.float64) * 0.5
    assert close_to(indicators.slope(line, 4)[3:], [0.5] * 7)
    assert indicators.percentile_rank(np.array([1.0, 2, 3, 4, math.nan]), 3.0) == 75.0
    assert close_to(indicators.log_returns(np.array([1.0, math.e])), [1.0])


def wilder_rsi_reference(close: list[float], period: int) -> list[float]:
    """Step-by-step Wilder RSI: the first average is a simple mean, then smoothing."""
    out = [math.nan] * len(close)
    gains = [max(close[i] - close[i - 1], 0.0) for i in range(1, len(close))]
    losses = [max(close[i - 1] - close[i], 0.0) for i in range(1, len(close))]
    if len(gains) < period:
        return out
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    for index in range(period, len(close)):
        if index > period:
            average_gain = (average_gain * (period - 1) + gains[index - 1]) / period
            average_loss = (average_loss * (period - 1) + losses[index - 1]) / period
        total = average_gain + average_loss
        out[index] = 100.0 * average_gain / total if total > 0 else 50.0
    return out


def test_rsi_matches_a_step_by_step_wilder_reference() -> None:
    close = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89]
    close += [46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25]
    values = indicators.rsi(np.array(close), 14)
    expected = wilder_rsi_reference(close, 14)
    for got, want in zip(values, expected, strict=True):
        if math.isnan(want):
            assert math.isnan(got)
        else:
            assert abs(got - want) < 1e-9
    assert 65.0 < values[14] < 75.0
    assert indicators.rsi(np.arange(30, dtype=np.float64), 14)[-1] == 100.0
    assert indicators.rsi(np.full(30, 1.5), 14)[-1] == 50.0
    assert np.all(np.isnan(indicators.rsi(np.arange(10, dtype=np.float64), 14)))
