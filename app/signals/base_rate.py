"""The same-geometry base rate of the Signal desk's full check (docs/SIGNAL_DESK.md 2.5).

On the symbol's M15 history (the last 2 years): how often did a trade with this side, this
stop loss and this target, both measured in ATR(14) of M15, opened in the same session,
reach the target before the stop loss? Every hour of history is one sample (an entry at the
bar's close); the trade is followed for at most 4 days. The rules are the shadow rules of
docs/AI_DESK.md section 2: when the stop loss and the target are both inside one bar, the
stop loss came first; the spread is paid (a buy enters at the ask, a sell's exits are at the
ask). Under 30 decided samples there is no percent, only "too little data".

It is a base rate of the geometry, not a forecast of this trade, and the card says so.
Pure numpy: no I/O, no MT5.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from app.analysis.bars import Bars
from app.analysis.indicators import atr
from app.analysis.sessions import active_sessions
from app.domain.signals import Direction
from app.signals.plan import OrderPlan

MIN_SAMPLES = 30
ATR_PERIOD = 14
HORIZON_BARS = 4 * 96  # follow a sample for at most 4 days of M15 bars
SAMPLE_STEP = 4  # one sample per hour, so neighbouring samples overlap less
HISTORY_DAYS = 730
TIMEFRAME = "M15"
BoolArray = npt.NDArray[np.bool_]


def session_of(moment: float) -> str:
    """The sessions open at a moment, as one key ("london + new_york"), "" for none."""
    return " + ".join(sorted(session.value for session in active_sessions(moment)))


@dataclass(frozen=True)
class Geometry:
    """A signal's shape: the side, the stop loss and every target in ATR, the session."""

    direction: Direction
    sl_atr: float
    tp_atr: tuple[float, ...]
    session: str = ""  # "" = any session

    @classmethod
    def of(cls, plan: OrderPlan, atr_value: float, moment: float) -> Geometry | None:
        """The geometry of a checked plan, or None without a usable ATR."""
        if plan.direction is None or not (math.isfinite(atr_value) and atr_value > 0):
            return None
        risk = abs(plan.entry - plan.sl)
        if not risk > 0:
            return None
        targets = tuple(abs(tp - plan.entry) / atr_value for tp in plan.tps)
        return cls(plan.direction, risk / atr_value, targets, session_of(moment))


@dataclass(frozen=True)
class TargetRate:
    tp_atr: float
    wins: int = 0
    losses: int = 0
    undecided: int = 0  # neither the target nor the stop within the horizon

    @property
    def samples(self) -> int:
        return self.wins + self.losses

    @property
    def rate(self) -> float | None:
        """The share of decided samples that hit the target first; None under 30."""
        if self.samples < MIN_SAMPLES:
            return None
        return self.wins / self.samples


@dataclass(frozen=True)
class BaseRate:
    geometry: Geometry
    targets: tuple[TargetRate, ...]
    bars: int  # M15 bars looked at
    first: float = 0.0  # UTC open time of the first bar
    last: float = 0.0

    @property
    def days(self) -> int:
        return max(0, round((self.last - self.first) / 86_400.0))


def first_hit(mask: BoolArray) -> int:
    """The index of the first True, or -1."""
    if not mask.any():
        return -1
    return int(np.argmax(mask))


def base_rate(
    bars: Bars,
    geometry: Geometry,
    point: float,
    *,
    step: int = SAMPLE_STEP,
    horizon: int = HORIZON_BARS,
) -> BaseRate:
    """The base rate of `geometry` on `bars` (M15, bid prices, spread in points)."""
    count = len(bars)
    targets = len(geometry.tp_atr)
    wins = [0] * targets
    losses = [0] * targets
    undecided = [0] * targets
    if count <= ATR_PERIOD + 1:
        empty = tuple(TargetRate(tp) for tp in geometry.tp_atr)
        return BaseRate(geometry, empty, count)
    ranges = atr(bars.high, bars.low, bars.close, ATR_PERIOD)
    spreads = bars.spread.astype(np.float64) * max(point, 0.0)
    closes = bars.time + bars.seconds
    long = geometry.direction is Direction.LONG
    for index in range(ATR_PERIOD, count - 1, max(step, 1)):
        size = float(ranges[index])
        if not (math.isfinite(size) and size > 0):
            continue
        if geometry.session and session_of(float(closes[index])) != geometry.session:
            continue
        stop = min(count, index + 1 + horizon)
        high = bars.high[index + 1 : stop]
        low = bars.low[index + 1 : stop]
        gap = spreads[index + 1 : stop]  # a sell closes at the ask
        risk = geometry.sl_atr * size
        if long:
            entry = float(bars.close[index] + spreads[index])  # a buy pays the ask
            lost = first_hit(low <= entry - risk)
        else:
            entry = float(bars.close[index])
            lost = first_hit(high + gap >= entry + risk)
        for number, reach in enumerate(geometry.tp_atr):
            distance = reach * size
            if long:
                won = first_hit(high >= entry + distance)
            else:
                won = first_hit(low + gap <= entry - distance)
            if won < 0 and lost < 0:
                undecided[number] += 1
            elif lost >= 0 and (won < 0 or lost <= won):
                losses[number] += 1  # the stop first, or both inside one bar
            else:
                wins[number] += 1
    found = tuple(
        TargetRate(tp, wins[n], losses[n], undecided[n]) for n, tp in enumerate(geometry.tp_atr)
    )
    return BaseRate(geometry, found, count, float(bars.time[0]), float(bars.time[-1]))
