"""Synthetic M5 markets with a known shape for the trendline break tests (test code only)."""

from __future__ import annotations

import numpy as np

from app.analysis.bars import Bars

START = 1_790_726_400  # a Wednesday 00:00 UTC

# Down, then a rally that breaks the line through two falling highs (a first entry).
FIRST = [
    (0, 100), (60, 125), (100, 150), (130, 118), (150, 135), (170, 112), (190, 120),
    (215, 100), (225, 108), (233, 98), (250, 112), (270, 112),
]  # fmt: skip
# Up, a small correction with falling highs, then the break (a pullback entry).
PULLBACK = [
    (0, 100), (60, 130), (90, 115), (130, 160), (150, 150), (175, 156), (195, 152),
    (220, 163), (260, 163),
]  # fmt: skip
FIRST_END = 241
PULLBACK_END = 202


def mirror(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    return [(x, 300 - y) for x, y in points]


def m5_bars(points: list[tuple[int, int]], count: int, wick: float = 0.6) -> Bars:
    """The first `count` M5 bars of a path that goes straight between the given points."""
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    close = np.interp(np.arange(count), xs, ys)
    open_ = np.r_[close[0], close[:-1]]
    return Bars.build(
        "NAS100",
        "M5",
        time=START + 300 * np.arange(count),
        open=open_,
        high=np.maximum(open_, close) + wick,
        low=np.minimum(open_, close) - wick,
        close=close,
    )
