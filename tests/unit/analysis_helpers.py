"""Synthetic bars for the analysis tests (test code only)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from app.analysis.bars import TF_SECONDS, Bars

START = 1_790_000_000 // 86_400 * 86_400  # a UTC midnight


def bars_from_closes(
    closes: Sequence[float],
    timeframe: str = "H1",
    *,
    symbol: str = "EURUSD",
    start: int = START,
    wick: float = 0.0005,
    spread: int | Sequence[int] = 10,
) -> Bars:
    close = np.asarray(closes, dtype=np.float64)
    open_ = np.r_[close[0], close[:-1]]
    step = TF_SECONDS[timeframe]
    return Bars.build(
        symbol,
        timeframe,
        time=start + step * np.arange(len(close)),
        open=open_,
        high=np.maximum(open_, close) + wick,
        low=np.minimum(open_, close) - wick,
        close=close,
        volume=np.full(len(close), 100),
        spread=np.full(len(close), spread) if isinstance(spread, int) else np.asarray(spread),
    )


def trending(count: int, drift: float, *, seed: int = 1, base: float = 1.1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return base + np.cumsum(rng.normal(drift, 0.0004, count))


def ohlc_bars(rows: Sequence[tuple[float, float, float, float]], timeframe: str = "H1") -> Bars:
    """Bars from explicit (open, high, low, close) rows."""
    values = np.asarray(rows, dtype=np.float64)
    step = TF_SECONDS[timeframe]
    return Bars.build(
        "EURUSD",
        timeframe,
        time=START + step * np.arange(len(values)),
        open=values[:, 0],
        high=values[:, 1],
        low=values[:, 2],
        close=values[:, 3],
    )
