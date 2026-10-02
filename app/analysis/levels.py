"""Key levels (spec C3): swing clusters, previous day and week high/low/close, session highs
and lows, round numbers, each with its distance from the price in ATR."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date

import numpy as np

from app.analysis.bars import Bars
from app.analysis.sessions import SessionRange
from app.analysis.structure import Swing

CLUSTER_ATR = 0.25
NEAR_ATR = 1.0
MAX_LEVELS = 12


@dataclass(frozen=True)
class Level:
    price: float
    kind: str
    touches: int = 1
    distance_atr: float = math.nan

    @property
    def side(self) -> str:
        return "resistance" if self.distance_atr > 0 else "support"

    def text(self, digits: int) -> str:
        return f"{self.kind} {self.price:,.{digits}f} ({abs(self.distance_atr):.1f} ATR)"


@dataclass(frozen=True)
class LevelSet:
    price: float
    atr: float
    levels: tuple[Level, ...]

    @property
    def nearest_support(self) -> Level | None:
        below = [level for level in self.levels if level.distance_atr <= 0]
        return max(below, key=_distance) if below else None

    @property
    def nearest_resistance(self) -> Level | None:
        above = [level for level in self.levels if level.distance_atr > 0]
        return min(above, key=_distance) if above else None

    def near(self, max_atr: float = NEAR_ATR) -> list[Level]:
        return [level for level in self.levels if abs(level.distance_atr) <= max_atr]


def _distance(level: Level) -> float:
    return level.distance_atr


def cluster_swings(swings: Iterable[Swing], tolerance: float) -> list[Level]:
    """Swing prices within `tolerance` of each other form one level; touches = swing count."""
    prices = sorted(swing.price for swing in swings)
    if not prices or tolerance <= 0:
        return []
    groups: list[list[float]] = [[prices[0]]]
    for price in prices[1:]:
        if price - groups[-1][-1] <= tolerance:
            groups[-1].append(price)
        else:
            groups.append([price])
    return [
        Level(float(np.mean(group)), "swing cluster" if len(group) > 1 else "swing", len(group))
        for group in groups
    ]


def round_step(price: float, daily_atr: float) -> float:
    """A 1-2-5 step close to half the daily ATR: 0.0050 for EURUSD, 20 for gold."""
    target = daily_atr / 2.0 if daily_atr > 0 else price / 200.0
    if target <= 0 or not math.isfinite(target):
        return 0.0
    power = 10.0 ** math.floor(math.log10(target))
    return next(step * power for step in (1.0, 2.0, 5.0, 10.0) if step * power >= target)


def round_numbers(price: float, step: float, count: int = 2) -> list[Level]:
    if step <= 0:
        return []
    base = math.floor(price / step) * step
    found = [base - step * index for index in range(count)]
    found += [base + step * (index + 1) for index in range(count)]
    return [Level(round(value, 10), "round number") for value in found]


def period_levels(daily: Bars, broker_days: Sequence[date], today: date) -> list[Level]:
    """Previous day and previous week high, low and close from closed D1 bars."""
    if not len(daily):
        return []
    found: list[Level] = []
    previous = [index for index, day in enumerate(broker_days) if day < today]
    if previous:
        last = previous[-1]
        found += [
            Level(float(daily.high[last]), "previous day high"),
            Level(float(daily.low[last]), "previous day low"),
            Level(float(daily.close[last]), "previous day close"),
        ]
    this_week = today.isocalendar()[:2]
    weeks = [day.isocalendar()[:2] for day in broker_days]
    earlier = sorted({week for week in weeks if week < this_week})
    if earlier:
        rows = [index for index, week in enumerate(weeks) if week == earlier[-1]]
        found += [
            Level(float(np.max(daily.high[rows])), "previous week high"),
            Level(float(np.min(daily.low[rows])), "previous week low"),
            Level(float(daily.close[rows[-1]]), "previous week close"),
        ]
    return found


def session_levels(ranges: Iterable[SessionRange]) -> list[Level]:
    found: list[Level] = []
    for item in ranges:
        found.append(Level(item.high, f"{item.session.value} high"))
        found.append(Level(item.low, f"{item.session.value} low"))
    return found


def build_levels(
    price: float,
    atr: float,
    swings: Iterable[Swing],
    extra: Iterable[Level],
    daily_atr: float,
) -> LevelSet:
    """All levels with their distance in ATR, the nearest first, at most MAX_LEVELS."""
    if not (atr > 0 and math.isfinite(atr) and math.isfinite(price)):
        return LevelSet(price, atr, ())
    candidates = cluster_swings(swings, atr * CLUSTER_ATR)
    candidates += list(extra)
    candidates += round_numbers(price, round_step(price, daily_atr))
    measured = [replace(level, distance_atr=(level.price - price) / atr) for level in candidates]
    measured.sort(key=lambda level: abs(level.distance_atr))
    return LevelSet(price, atr, tuple(measured[:MAX_LEVELS]))
