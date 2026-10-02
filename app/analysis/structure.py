"""Market structure (spec C3): confirmed swings, HH/HL/LH/LL, BOS and CHoCH, trend or range.

A swing high at bar i is confirmed only after `strength` more bars have closed below it, so
nothing here looks ahead: the result for the first k bars never changes when bar k+1 arrives
(`tests/unit/test_analysis_structure.py` proves it).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.analysis.bars import Bars

DEFAULT_STRENGTH = 3


class SwingKind(StrEnum):
    HIGH = "high"
    LOW = "low"


class Trend(StrEnum):
    UP = "up"
    DOWN = "down"
    RANGE = "range"


@dataclass(frozen=True)
class Swing:
    kind: SwingKind
    index: int
    time: int
    price: float
    confirmed_index: int
    label: str  # HH, LH, EH for highs; HL, LL, EL for lows; "" for the first one


@dataclass(frozen=True)
class StructureEvent:
    """A close beyond the last confirmed swing: BOS continues the trend, CHoCH turns it."""

    kind: str  # "BOS" or "CHoCH"
    direction: Trend
    index: int
    time: int
    level: float

    def text(self) -> str:
        side = "bullish" if self.direction is Trend.UP else "bearish"
        return f"{side} {self.kind} at {self.level:g}"


@dataclass(frozen=True)
class Structure:
    timeframe: str
    swings: tuple[Swing, ...]
    events: tuple[StructureEvent, ...]
    trend: Trend
    bias: Trend | None

    @property
    def last_event(self) -> StructureEvent | None:
        return self.events[-1] if self.events else None

    def last(self, kind: SwingKind) -> Swing | None:
        return next((swing for swing in reversed(self.swings) if swing.kind is kind), None)

    def labels(self) -> str:
        """The latest swing labels, newest last, for example "HH HL HH HL"."""
        return " ".join(swing.label for swing in self.swings[-4:] if swing.label)


def _label(kind: SwingKind, price: float, previous: Swing | None) -> str:
    if previous is None:
        return ""
    if kind is SwingKind.HIGH:
        return "HH" if price > previous.price else ("LH" if price < previous.price else "EH")
    return "HL" if price > previous.price else ("LL" if price < previous.price else "EL")


def find_swings(bars: Bars, strength: int = DEFAULT_STRENGTH) -> list[Swing]:
    """Swing highs and lows, each confirmed `strength` bars after it."""
    high, low = bars.high, bars.low
    swings: list[Swing] = []
    previous: dict[SwingKind, Swing] = {}
    for index in range(strength, len(bars) - strength):
        left = slice(index - strength, index)
        right = slice(index + 1, index + strength + 1)
        found: list[tuple[SwingKind, float]] = []
        if high[index] > high[left].max() and high[index] >= high[right].max():
            found.append((SwingKind.HIGH, float(high[index])))
        if low[index] < low[left].min() and low[index] <= low[right].min():
            found.append((SwingKind.LOW, float(low[index])))
        for kind, price in found:
            swing = Swing(
                kind,
                index,
                int(bars.time[index]),
                price,
                index + strength,
                _label(kind, price, previous.get(kind)),
            )
            previous[kind] = swing
            swings.append(swing)
    return swings


def _trend(swings: list[Swing]) -> Trend:
    highs = [swing for swing in swings if swing.kind is SwingKind.HIGH][-2:]
    lows = [swing for swing in swings if swing.kind is SwingKind.LOW][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return Trend.RANGE
    if highs[-1].label == "HH" and lows[-1].label == "HL":
        return Trend.UP
    if highs[-1].label == "LH" and lows[-1].label == "LL":
        return Trend.DOWN
    return Trend.RANGE


def analyze_structure(bars: Bars, strength: int = DEFAULT_STRENGTH) -> Structure:
    swings = find_swings(bars, strength)
    by_confirmation = sorted(swings, key=lambda swing: (swing.confirmed_index, swing.index))
    events: list[StructureEvent] = []
    bias: Trend | None = None
    level_high: float | None = None
    level_low: float | None = None
    pointer = 0
    for index in range(len(bars)):
        while pointer < len(by_confirmation) and by_confirmation[pointer].confirmed_index <= index:
            swing = by_confirmation[pointer]
            if swing.kind is SwingKind.HIGH:
                level_high = swing.price
            else:
                level_low = swing.price
            pointer += 1
        close = float(bars.close[index])
        direction: Trend | None = None
        level = 0.0
        if level_high is not None and close > level_high:
            direction, level, level_high = Trend.UP, level_high, None
        elif level_low is not None and close < level_low:
            direction, level, level_low = Trend.DOWN, level_low, None
        if direction is None:
            continue
        kind = "CHoCH" if bias is not None and bias is not direction else "BOS"
        events.append(StructureEvent(kind, direction, index, int(bars.time[index]), level))
        bias = direction
    return Structure(bars.timeframe, tuple(swings), tuple(events), _trend(swings), bias)
