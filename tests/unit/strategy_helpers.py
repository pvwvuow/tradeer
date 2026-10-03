"""Synthetic markets for the strategy and pipeline tests (test code only)."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from app.analysis.bars import TF_SECONDS, Bars
from app.analysis.levels import LevelSet
from app.analysis.symbol import SymbolAnalysis, Tick, analyze_symbol
from app.core.clock import BrokerClock
from app.strategies.context import MarketContext

DAY = 86_400
# Wednesday 2026-09-30 00:00 UTC: a normal FX weekday in EU and US summer time.
WEDNESDAY = 1_790_726_400


def wave(count: int, *, drift: float, amp: float, period: float, base: float = 1.1) -> np.ndarray:
    """A trend with a regular pullback: drift per bar plus a sine wave."""
    index = np.arange(count, dtype=np.float64)
    return base + drift * index + amp * np.sin(2 * np.pi * index / period)


def bars(
    closes: np.ndarray,
    timeframe: str = "M15",
    *,
    start: int,
    wick: float = 0.0003,
    symbol: str = "EURUSD",
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
        spread=np.full(len(close), 8),
    )


def aggregate(source: Bars, timeframe: str) -> Bars:
    """Higher-timeframe bars built from lower ones (only complete groups)."""
    step = TF_SECONDS[timeframe]
    groups = source.time // step
    per_group = step // source.seconds
    keys, first, counts = np.unique(groups, return_index=True, return_counts=True)
    rows = [
        (key, start)
        for key, start, size in zip(keys, first, counts, strict=True)
        if size == per_group
    ]
    times, opens, highs, lows, closes = [], [], [], [], []
    for key, start in rows:
        stop = start + per_group
        times.append(int(key) * step)
        opens.append(source.open[start])
        highs.append(source.high[start:stop].max())
        lows.append(source.low[start:stop].min())
        closes.append(source.close[stop - 1])
    return Bars.build(
        source.symbol,
        timeframe,
        time=times,
        open=opens,
        high=highs,
        low=lows,
        close=closes,
    )


def context(
    by_timeframe: Mapping[str, Bars],
    *,
    timeframe: str = "M15",
    levels: LevelSet | None = None,
    clock: BrokerClock | None = None,
    digits: int = 5,
) -> MarketContext:
    entry = by_timeframe[timeframe]
    close = int(entry.time[-1]) + TF_SECONDS[timeframe]
    return MarketContext(
        symbol=entry.symbol,
        timeframe=timeframe,
        bars=dict(by_timeframe),
        clock=clock or BrokerClock.assumed(),
        now=float(close),
        digits=digits,
        point=10.0**-digits,
        spread=2 * 10.0**-digits,
        session="London",
        levels=levels,
    )


def trend_market(direction: int, count: int = 6000) -> Bars:
    """M15 bars of a steady trend with pullbacks every 32 bars (8 hours)."""
    closes = wave(count, drift=direction * 0.00004, amp=0.0012, period=32)
    return bars(closes, start=WEDNESDAY - count * 900)


def frames(m15: Bars) -> dict[str, Bars]:
    return {"M15": m15, "H1": aggregate(m15, "H1"), "D1": aggregate(m15, "D1")}


def london_days(days: int = 25, *, asia_amp: float = 0.0015, day_amp: float = 0.004) -> Bars:
    """M15 bars of `days` summer weekdays ending at the London open (07:00 UTC) of WEDNESDAY.

    23:00 to 06:00 UTC (00:00 to 07:00 London time) moves within +-asia_amp; the rest of the
    day swings by +-day_amp, so ATR(D1) is about 2 x day_amp.
    """
    end = WEDNESDAY + 7 * 3600
    start = end - days * DAY
    times = np.arange(start, end, 900)
    hours = (times % DAY) / 3600.0
    asia = (hours >= 23) | (hours < 6)
    phase = 2 * np.pi * np.arange(len(times)) / 16
    closes = np.where(asia, 1.1 + asia_amp * np.sin(phase), 1.1 + day_amp * np.sin(phase / 2))
    return bars(closes, start=int(start), wick=0.0002)


def m5_from(m15: Bars) -> Bars:
    """Three flat M5 bars per M15 bar (enough for the data checks of the analysis)."""
    close = np.repeat(m15.close, 3)
    start = int(m15.time[0])
    return bars(close, "M5", start=start, wick=0.0001, symbol=m15.symbol)


def analysis_at(
    m15: Bars,
    stop: int,
    *,
    spread: float = 0.00002,
    clock: BrokerClock | None = None,
) -> tuple[SymbolAnalysis, float]:
    """The analysis just after M15 bar `stop - 1` closed, and that moment (UTC)."""
    entry = m15.slice(0, stop)
    now = float(entry.time[-1] + 900)
    by_timeframe = {
        "M5": m5_from(entry),
        "M15": entry,
        "H1": aggregate(entry, "H1"),
        "H4": aggregate(entry, "H4"),
        "D1": aggregate(entry, "D1"),
    }
    price = float(entry.close[-1])
    tick = Tick(price, price + spread, now - 1, 1e-5, 5)
    analysis = analyze_symbol(
        entry.symbol,
        by_timeframe,
        now=now,
        clock=clock or BrokerClock.assumed(),
        tick=tick,
        digits=5,
        broker_symbol=entry.symbol,
    )
    return analysis, now
