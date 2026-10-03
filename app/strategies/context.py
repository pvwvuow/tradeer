"""What a strategy may look at (spec C4): `MarketContext`.

Closed bars of the entry timeframe and **only fully closed** higher-timeframe bars at the
moment the entry bar closed, plus the symbol's numbers, the spread, the session and the
analysis outputs. Building the context the same way live and in a backtest keeps one logic
for both runtimes (spec D3.4).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from app.analysis.bars import TF_SECONDS, Bars
from app.analysis.levels import LevelSet
from app.analysis.structure import Structure
from app.analysis.symbol import SymbolAnalysis
from app.analysis.volatility import Volatility
from app.calendar.models import CalendarEvent
from app.core.clock import BrokerClock


def closed_by(bars: Bars, close_time: int) -> Bars:
    """The bars that had fully closed at `close_time` (UTC seconds)."""
    if not len(bars):
        return bars
    closes = bars.time + bars.seconds
    stop = int(np.searchsorted(closes, close_time, side="right"))
    return bars.slice(0, stop)


@dataclass(frozen=True)
class MarketContext:
    symbol: str
    timeframe: str
    bars: Mapping[str, Bars]
    clock: BrokerClock
    now: float
    digits: int = 5
    point: float = 1e-5
    spread: float = math.nan  # live spread in price
    session: str = ""
    broker_symbol: str = ""
    levels: LevelSet | None = None
    structures: Mapping[str, Structure] = field(default_factory=dict)
    volatility: Volatility | None = None
    events: tuple[CalendarEvent, ...] = ()

    @property
    def entry(self) -> Bars:
        return self.bars.get(self.timeframe) or Bars.empty(self.symbol, self.timeframe)

    @property
    def bar_time(self) -> int:
        """UTC open time of the newest closed entry bar (0 without bars)."""
        entry = self.entry
        return int(entry.time[-1]) if len(entry) else 0

    @property
    def close_time(self) -> int:
        return self.bar_time + TF_SECONDS[self.timeframe] if self.bar_time else 0

    def history(self, timeframe: str) -> int:
        bars = self.bars.get(timeframe)
        return len(bars) if bars is not None else 0


def build_context(
    analysis: SymbolAnalysis,
    timeframe: str,
    *,
    clock: BrokerClock,
    now: float,
    point: float,
    spread: float = math.nan,
) -> MarketContext:
    """The context for a strategy that trades `timeframe`, from the newest analysis."""
    entry = analysis.bars.get(timeframe) or Bars.empty(analysis.symbol, timeframe)
    close_time = int(entry.time[-1]) + TF_SECONDS[timeframe] if len(entry) else 0
    bars: dict[str, Bars] = {timeframe: entry}
    for name, series in analysis.bars.items():
        if name != timeframe and TF_SECONDS.get(name, 0) > TF_SECONDS[timeframe]:
            bars[name] = closed_by(series, close_time)
    return MarketContext(
        symbol=analysis.symbol,
        timeframe=timeframe,
        bars=bars,
        clock=clock,
        now=now,
        digits=analysis.digits,
        point=point,
        spread=spread,
        session=analysis.session,
        broker_symbol=analysis.broker_symbol,
        levels=analysis.levels,
        structures=dict(analysis.structures),
        volatility=analysis.volatility,
        events=analysis.events,
    )
