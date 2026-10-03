"""Volatility (spec C3): ATR and its 100-day percentile, the average daily range and the
share of it used today, and a plain regime word."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.analysis import indicators
from app.analysis.bars import Bars

ATR_PERIOD = 14
PERCENTILE_DAYS = 100
ADR_DAYS = 20


@dataclass(frozen=True)
class Volatility:
    atr_d1: float
    atr_h1: float
    atr_percentile: float
    adr: float
    today_range: float
    adr_used_percent: float
    regime: str

    def text(self) -> str:
        if self.regime == "unknown":
            return "volatility unknown (not enough daily history)"
        used = (
            f", {self.adr_used_percent:.0f}% of the daily range used"
            if math.isfinite(self.adr_used_percent)
            else ""
        )
        return f"volatility {self.regime} (ATR at the {self.atr_percentile:.0f}th percentile{used})"


def regime_for(percentile: float) -> str:
    if not math.isfinite(percentile):
        return "unknown"
    if percentile >= 95:
        return "extreme"
    if percentile >= 75:
        return "high"
    if percentile >= 25:
        return "normal"
    return "low"


def volatility(daily: Bars, hourly: Bars | None, today: Bars | None) -> Volatility:
    """`daily` holds closed D1 bars; `today` the closed intraday bars of the current day."""
    atr_line = indicators.atr(daily.high, daily.low, daily.close, ATR_PERIOD)
    atr_d1 = indicators.last(atr_line)
    window = atr_line[-PERCENTILE_DAYS:]
    percentile = indicators.percentile_rank(window, atr_d1) if len(daily) > ATR_PERIOD else math.nan
    atr_h1 = math.nan
    if hourly is not None and len(hourly) > ATR_PERIOD:
        atr_h1 = indicators.last(indicators.atr(hourly.high, hourly.low, hourly.close, ATR_PERIOD))
    ranges = (daily.high - daily.low)[-ADR_DAYS:]
    adr = float(np.mean(ranges)) if len(ranges) else math.nan
    today_range = math.nan
    if today is not None and len(today):
        today_range = float(np.max(today.high) - np.min(today.low))
    used = today_range / adr * 100.0 if adr > 0 and math.isfinite(today_range) else math.nan
    return Volatility(atr_d1, atr_h1, percentile, adr, today_range, used, regime_for(percentile))
