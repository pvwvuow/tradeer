"""Spread monitor (spec C3): the current spread against the typical spread for this hour."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.analysis.bars import Bars

WIDE = 1.5
VERY_WIDE = 3.0


@dataclass(frozen=True)
class SpreadStatus:
    current_points: float
    typical_points: float
    ratio: float
    status: str  # normal, wide, very wide, unknown

    def text(self) -> str:
        if self.status == "unknown":
            return f"spread {self.current_points:.0f} points"
        return (
            f"spread {self.current_points:.0f} points, {self.status} "
            f"(typical {self.typical_points:.0f} at this hour)"
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
    typical = typical_spread(bars, int(utc_now // 3600) % 24)
    if not (typical > 0 and math.isfinite(current_points)):
        return SpreadStatus(current_points, typical, math.nan, "unknown")
    ratio = current_points / typical
    status = "very wide" if ratio >= VERY_WIDE else ("wide" if ratio >= WIDE else "normal")
    return SpreadStatus(current_points, typical, ratio, status)
