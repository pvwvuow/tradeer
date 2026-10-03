"""Multi-timeframe trend matrix (spec C3): EMA structure, EMA slope and ADX per timeframe.

Each timeframe scores -100 (clear downtrend) to +100 (clear uptrend). The bias is the
weighted average, with higher timeframes weighing more. Every score comes with its reasons.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from app.analysis import indicators
from app.analysis.bars import ANALYSIS_TIMEFRAMES, Bars
from app.analysis.structure import Trend

FAST, MID, SLOW = 20, 50, 200
SLOPE_BARS = 10
MIN_BARS = SLOW + SLOPE_BARS
TREND_THRESHOLD = 25.0
WEIGHTS: dict[str, float] = {"M5": 1.0, "M15": 1.5, "H1": 2.0, "H4": 2.5, "D1": 3.0}


@dataclass(frozen=True)
class TimeframeTrend:
    timeframe: str
    score: float
    direction: Trend
    adx: float
    slope_atr: float
    reasons: tuple[str, ...]
    enough_data: bool = True

    def text(self) -> str:
        if not self.enough_data:
            return f"{self.timeframe}: not enough history"
        words = {Trend.UP: "uptrend", Trend.DOWN: "downtrend", Trend.RANGE: "no clear trend"}
        return f"{self.timeframe} {words[self.direction]} ({self.score:+.0f})"


@dataclass(frozen=True)
class TrendMatrix:
    rows: tuple[TimeframeTrend, ...]
    bias: float
    direction: Trend
    reasons: tuple[str, ...]

    def get(self, timeframe: str) -> TimeframeTrend | None:
        return next((row for row in self.rows if row.timeframe == timeframe), None)


def direction_of(score: float) -> Trend:
    if score >= TREND_THRESHOLD:
        return Trend.UP
    if score <= -TREND_THRESHOLD:
        return Trend.DOWN
    return Trend.RANGE


def _sign(value: float) -> float:
    return 0.0 if value == 0 or not math.isfinite(value) else math.copysign(1.0, value)


def _adx_factor(value: float) -> float:
    if not math.isfinite(value) or value < 15:
        return 0.4
    if value < 20:
        return 0.6
    if value < 25:
        return 0.8
    return 1.0


def timeframe_trend(bars: Bars) -> TimeframeTrend:
    if len(bars) < MIN_BARS:
        reason = f"{len(bars)} closed bars, needs {MIN_BARS}"
        return TimeframeTrend(bars.timeframe, 0.0, Trend.RANGE, math.nan, 0.0, (reason,), False)
    close = bars.close
    fast = indicators.last(indicators.ema(close, FAST))
    mid_line = indicators.ema(close, MID)
    mid = indicators.last(mid_line)
    slow = indicators.last(indicators.ema(close, SLOW))
    price = float(close[-1])
    atr = indicators.last(indicators.atr(bars.high, bars.low, close))
    adx_line, plus_di, minus_di = indicators.adx(bars.high, bars.low, close)
    adx = indicators.last(adx_line)
    alignment = (_sign(price - fast) + _sign(fast - mid) + _sign(mid - slow)) / 3.0
    per_bar = indicators.last(indicators.slope(mid_line, SLOPE_BARS))
    slope_atr = per_bar / atr if atr > 0 and math.isfinite(per_bar) else 0.0
    slope_score = math.tanh(slope_atr * 10.0)
    raw = (0.6 * alignment + 0.4 * slope_score) * _adx_factor(adx)
    score = float(np.clip(raw * 100.0, -100.0, 100.0))
    reasons: list[str] = []
    if alignment == 1:
        reasons.append(f"price above EMA {FAST}/{MID}/{SLOW}, stacked upward")
    elif alignment == -1:
        reasons.append(f"price below EMA {FAST}/{MID}/{SLOW}, stacked downward")
    else:
        side = "above" if price > slow else "below"
        reasons.append(f"EMAs mixed, price {side} EMA {SLOW}")
    verb = "rising" if slope_atr > 0 else "falling"
    reasons.append(f"EMA {MID} {verb} {abs(slope_atr):.2f} ATR per bar")
    if math.isfinite(adx):
        strength = "trending" if adx >= 25 else ("weak trend" if adx >= 20 else "no trend")
        leader = "+DI" if indicators.last(plus_di) >= indicators.last(minus_di) else "-DI"
        reasons.append(f"ADX {adx:.0f} ({strength}, {leader} leads)")
    direction = direction_of(score)
    return TimeframeTrend(bars.timeframe, score, direction, adx, slope_atr, tuple(reasons))


def trend_matrix(bars_by_timeframe: Mapping[str, Bars]) -> TrendMatrix:
    rows: list[TimeframeTrend] = []
    for timeframe in ANALYSIS_TIMEFRAMES:
        bars = bars_by_timeframe.get(timeframe)
        if bars is not None:
            rows.append(timeframe_trend(bars))
    usable = [row for row in rows if row.enough_data]
    total = sum(WEIGHTS[row.timeframe] for row in usable)
    bias = sum(row.score * WEIGHTS[row.timeframe] for row in usable) / total if total else 0.0
    agree = [row.timeframe for row in usable if row.direction is direction_of(bias)]
    reasons = [f"weighted bias {bias:+.0f} from {len(usable)} timeframes"]
    if agree and direction_of(bias) is not Trend.RANGE:
        reasons.append(f"{', '.join(agree)} agree")
    missing = [row.timeframe for row in rows if not row.enough_data]
    if missing:
        reasons.append(f"not enough history on {', '.join(missing)}")
    return TrendMatrix(tuple(rows), float(bias), direction_of(bias), tuple(reasons))
