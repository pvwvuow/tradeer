"""Candle patterns on the last closed bar (spec C3): information only, never a signal."""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis import indicators
from app.analysis.bars import Bars

MIN_RANGE_ATR = 0.5


@dataclass(frozen=True)
class Pattern:
    name: str
    direction: str  # bullish, bearish or neutral
    timeframe: str
    time: int

    def text(self) -> str:
        side = "" if self.direction == "neutral" else f"{self.direction} "
        return f"{self.timeframe} {side}{self.name}"


def find_patterns(bars: Bars) -> list[Pattern]:
    if len(bars) < 16:
        return []
    atr = indicators.last(indicators.atr(bars.high, bars.low, bars.close)[:-1])
    open_, high, low, close = (
        float(bars.open[-1]),
        float(bars.high[-1]),
        float(bars.low[-1]),
        float(bars.close[-1]),
    )
    prev_open, prev_high, prev_low, prev_close = (
        float(bars.open[-2]),
        float(bars.high[-2]),
        float(bars.low[-2]),
        float(bars.close[-2]),
    )
    found: list[Pattern] = []
    time, timeframe = int(bars.time[-1]), bars.timeframe
    size = high - low
    body = abs(close - open_)
    big_enough = atr > 0 and size >= MIN_RANGE_ATR * atr
    previous_body = abs(prev_open - prev_close)
    wraps_up = close >= prev_open and open_ <= prev_close and body > previous_body
    wraps_down = close <= prev_open and open_ >= prev_close and body > previous_body
    if big_enough and prev_close < prev_open and close > open_ and wraps_up:
        found.append(Pattern("engulfing", "bullish", timeframe, time))
    if big_enough and prev_close > prev_open and close < open_ and wraps_down:
        found.append(Pattern("engulfing", "bearish", timeframe, time))
    if big_enough and body <= size / 3.0:
        upper = high - max(open_, close)
        lower = min(open_, close) - low
        if lower >= size * 2.0 / 3.0:
            found.append(Pattern("pin bar", "bullish", timeframe, time))
        elif upper >= size * 2.0 / 3.0:
            found.append(Pattern("pin bar", "bearish", timeframe, time))
    if high < prev_high and low > prev_low:
        found.append(Pattern("inside bar", "neutral", timeframe, time))
    return found
