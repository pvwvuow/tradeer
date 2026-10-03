"""Vectorized indicators (spec D1): own code, unit-tested against hand-calculated values.

Every function returns an array as long as its input; values without enough history are NaN.
"""

from __future__ import annotations

import numpy as np

from app.analysis.bars import FloatArray


def _nan(size: int) -> FloatArray:
    return np.full(size, np.nan, dtype=np.float64)


def sma(values: FloatArray, period: int) -> FloatArray:
    out = _nan(len(values))
    if period <= 0 or len(values) < period:
        return out
    sums = np.cumsum(np.insert(values, 0, 0.0))
    out[period - 1 :] = (sums[period:] - sums[:-period]) / period
    return out


def ema(values: FloatArray, period: int) -> FloatArray:
    """Exponential average (alpha 2 / (period + 1)), seeded with the first simple average."""
    out = _nan(len(values))
    if period <= 0 or len(values) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    keep = 1.0 - alpha
    value = float(np.mean(values[:period]))
    found = [value]
    for item in values[period:].tolist():  # plain floats: the same IEEE math, 5x faster
        value = alpha * item + keep * value
        found.append(value)
    out[period - 1 :] = found
    return out


def rma(values: FloatArray, period: int) -> FloatArray:
    """Wilder's smoothing (alpha 1 / period). Leading NaNs are skipped."""
    out = _nan(len(values))
    finite = np.flatnonzero(np.isfinite(values))
    if period <= 0 or not len(finite):
        return out
    start = int(finite[0])
    if len(values) - start < period or not np.all(np.isfinite(values[start : start + period])):
        return out
    seed = start + period - 1
    value = float(np.mean(values[start : start + period]))
    found = [value]
    weight = period - 1
    for item in values[seed + 1 :].tolist():
        value = (value * weight + item) / period
        found.append(value)
    out[seed:] = found
    return out


def true_range(high: FloatArray, low: FloatArray, close: FloatArray) -> FloatArray:
    out = high - low
    if len(out) > 1:
        previous = close[:-1]
        out[1:] = np.maximum.reduce(
            [high[1:] - low[1:], np.abs(high[1:] - previous), np.abs(low[1:] - previous)],
        )
    return out.astype(np.float64)


def atr(high: FloatArray, low: FloatArray, close: FloatArray, period: int = 14) -> FloatArray:
    return rma(true_range(high, low, close), period)


def rsi(close: FloatArray, period: int = 14) -> FloatArray:
    """Wilder's RSI (0 to 100). A bar with no losses gives 100, one with no moves 50."""
    size = len(close)
    if size <= period:
        return _nan(size)
    change = np.diff(close, prepend=np.nan)
    gain = np.where(change > 0, change, 0.0)
    loss = np.where(change < 0, -change, 0.0)
    gain[0] = loss[0] = np.nan
    average_gain = rma(gain, period)
    average_loss = rma(loss, period)
    out = _nan(size)
    valid = np.isfinite(average_gain) & np.isfinite(average_loss)
    total = average_gain + average_loss
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(total > 0, 100.0 * average_gain / total, 50.0)
    out[valid] = ratio[valid]
    return out


def adx(
    high: FloatArray,
    low: FloatArray,
    close: FloatArray,
    period: int = 14,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Wilder's ADX with +DI and -DI."""
    size = len(close)
    if size < 2:
        return _nan(size), _nan(size), _nan(size)
    up = np.diff(high, prepend=np.nan)
    down = -np.diff(low, prepend=np.nan)
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    plus_dm[0] = minus_dm[0] = np.nan
    ranges = true_range(high, low, close)
    ranges[0] = np.nan
    smooth_range = rma(ranges, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * rma(plus_dm, period) / smooth_range
        minus_di = 100.0 * rma(minus_dm, period) / smooth_range
        total = plus_di + minus_di
        dx = np.where(total > 0, 100.0 * np.abs(plus_di - minus_di) / total, 0.0)
    dx[~np.isfinite(plus_di)] = np.nan
    return rma(dx, period), plus_di, minus_di


def slope(values: FloatArray, period: int) -> FloatArray:
    """Least-squares slope per bar over the last `period` values."""
    out = _nan(len(values))
    if period < 2 or len(values) < period:
        return out
    x = np.arange(period, dtype=np.float64) - (period - 1) / 2.0
    weights = x / float(np.sum(x * x))
    windows = np.lib.stride_tricks.sliding_window_view(values, period)
    out[period - 1 :] = windows @ weights
    return out


def percentile_rank(history: FloatArray, value: float) -> float:
    """Share of the finite history values that are at or below `value`, in percent."""
    finite = history[np.isfinite(history)]
    if not len(finite) or not np.isfinite(value):
        return float("nan")
    return float(np.count_nonzero(finite <= value) * 100.0 / len(finite))


def log_returns(close: FloatArray) -> FloatArray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.diff(np.log(close)).astype(np.float64)


def last(values: FloatArray) -> float:
    """The newest value, NaN for an empty array."""
    return float(values[-1]) if len(values) else float("nan")
