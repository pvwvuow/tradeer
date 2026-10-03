"""Spread monitor (spec C3): is the spread unusually wide for this hour? (ADR 47)

MT5 stores the MINIMUM spread of each bar, not a live spread. Comparing today's live spread with
those minimums made spreads look three times wider than normal on real accounts, so the check
compares like with like: the last closed M5 bar's spread against the median bar spread of this
hour. The live spread is still shown.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.analysis.bars import Bars

WIDE = 1.5
VERY_WIDE = 3.0


@dataclass(frozen=True)
class SpreadStatus:
    current_points: float  # live spread now
    bar_points: float  # minimum spread of the last closed M5 bar
    typical_points: float  # median bar spread of this hour
    ratio: float  # bar_points / typical_points
    status: str  # normal, wide, very wide, unknown

    def text(self) -> str:
        if self.status == "unknown":
            return f"spread {self.current_points:.0f} points"
        return (
            f"spread {self.current_points:.0f} points now, {self.status} "
            f"(last M5 bar {self.bar_points:.0f}, typical {self.typical_points:.0f} at this hour)"
        )


def typical_spread(bars: Bars, utc_hour: int) -> float:
    """Median bar spread (points) of the bars that opened in this UTC hour."""
    if not len(bars):
        return math.nan
    hours = (bars.time // 3600) % 24
    values = bars.spread[(hours == utc_hour) & (bars.spread > 0)]
    if not len(values):
        values = bars.spread[bars.spread > 0]
    return float(np.median(values)) if len(values) else math.nan


def spread_status(current_points: float, bars: Bars, utc_now: float) -> SpreadStatus:
    """`bars` are closed M5 bars; `current_points` is the live spread (shown, not judged)."""
    typical = typical_spread(bars, int(utc_now // 3600) % 24)
    last = float(bars.spread[-1]) if len(bars) else math.nan
    if not (typical > 0 and math.isfinite(last) and last >= 0):
        return SpreadStatus(current_points, last, typical, math.nan, "unknown")
    ratio = last / typical
    status = "very wide" if ratio >= VERY_WIDE else ("wide" if ratio >= WIDE else "normal")
    return SpreadStatus(current_points, last, typical, ratio, status)
