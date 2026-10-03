"""Everything the app knows about one symbol after a closed bar (spec C3), in one object."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.analysis import indicators
from app.analysis.bars import Bars
from app.analysis.card import AnalysisCard, build_card
from app.analysis.levels import LevelSet, build_levels, period_levels, session_levels
from app.analysis.patterns import Pattern, find_patterns
from app.analysis.quality import QualityReport, quality_report
from app.analysis.sessions import market_open, session_label, session_ranges
from app.analysis.spread import SpreadStatus, spread_status
from app.analysis.structure import Structure, analyze_structure
from app.analysis.trend import TrendMatrix, trend_matrix
from app.analysis.volatility import Volatility, volatility
from app.calendar.models import CalendarEvent, matches_symbol
from app.core.clock import BrokerClock

STRUCTURE_TIMEFRAMES: tuple[str, ...] = ("M15", "H1", "H4")
PATTERN_TIMEFRAMES: tuple[str, ...] = ("H1", "H4")


@dataclass(frozen=True)
class Tick:
    bid: float
    ask: float
    utc_time: float
    point: float
    digits: int

    @property
    def spread_points(self) -> float:
        return (self.ask - self.bid) / self.point if self.point > 0 else math.nan


@dataclass(frozen=True)
class SymbolAnalysis:
    symbol: str
    broker_symbol: str
    bar_time: int
    price: float
    digits: int
    trend: TrendMatrix
    structures: Mapping[str, Structure]
    levels: LevelSet
    volatility: Volatility
    session: str
    spread: SpreadStatus | None
    patterns: tuple[Pattern, ...]
    quality: QualityReport
    events: tuple[CalendarEvent, ...]
    card: AnalysisCard
    bars: Mapping[str, Bars] = field(default_factory=dict)


def _empty(symbol: str, timeframe: str) -> Bars:
    return Bars.empty(symbol, timeframe)


def analyze_symbol(
    symbol: str,
    bars: Mapping[str, Bars],
    *,
    now: float,
    clock: BrokerClock,
    tick: Tick | None,
    digits: int,
    broker_symbol: str = "",
    events: Sequence[CalendarEvent] = (),
) -> SymbolAnalysis:
    """Pure: the same bars, tick and time always give the same analysis."""
    m5 = bars.get("M5") or _empty(symbol, "M5")
    m15 = bars.get("M15") or _empty(symbol, "M15")
    hourly = bars.get("H1") or _empty(symbol, "H1")
    daily = bars.get("D1") or _empty(symbol, "D1")
    tick_fresh = tick is not None and now - tick.utc_time < 300
    fallback = m5.last_close if len(m5) else math.nan
    price = tick.bid if tick is not None and tick_fresh else fallback
    quality = quality_report(
        [m5, m15, hourly],
        symbol=symbol,
        tick_utc=tick.utc_time if tick is not None else None,
        now=now,
        clock_changes=clock.changes,
        clock_measured=clock.measured,
    )
    matrix = trend_matrix(bars)
    structures = {
        timeframe: analyze_structure(bars[timeframe])
        for timeframe in STRUCTURE_TIMEFRAMES
        if timeframe in bars and len(bars[timeframe])
    }
    today_start = clock.day_start_utc(now)
    vol = volatility(daily, hourly, m5.since(today_start))
    atr_h1 = vol.atr_h1
    if not math.isfinite(atr_h1) and len(m15) > 15:
        atr_h1 = indicators.last(indicators.atr(m15.high, m15.low, m15.close)) * 2.0
    broker_days = [datetime.fromtimestamp(int(value), UTC).date() for value in daily.server_time]
    utc_today = datetime.fromtimestamp(now, UTC).date()
    ranges = session_ranges(m15, utc_today) or session_ranges(m15, utc_today - timedelta(days=1))
    swings = [
        swing
        for timeframe in ("H1", "H4")
        if timeframe in structures
        for swing in structures[timeframe].swings[-30:]
    ]
    levels = build_levels(
        price,
        atr_h1,
        swings,
        [*period_levels(daily, broker_days, clock.broker_date(now)), *session_levels(ranges)],
        vol.atr_d1,
    )
    spread = spread_status(tick.spread_points, m5, now) if tick is not None else None
    patterns = tuple(
        pattern
        for timeframe in PATTERN_TIMEFRAMES
        if timeframe in bars
        for pattern in find_patterns(bars[timeframe])
    )
    relevant = tuple(
        event
        for event in sorted(events, key=lambda item: item.time)
        if matches_symbol(event, symbol)
    )
    trading = market_open(now) and not any(
        issue.code == "symbol_closed" for issue in quality.issues
    )
    session = session_label(now)
    card = build_card(
        symbol=symbol,
        digits=digits,
        now=now,
        matrix=matrix,
        structure=structures.get("H1"),
        levels=levels,
        volatility=vol,
        session=session,
        spread=spread,
        patterns=patterns,
        quality=quality,
        events=relevant,
        trading=trading,
    )
    return SymbolAnalysis(
        symbol=symbol,
        broker_symbol=broker_symbol or symbol,
        bar_time=int(m5.time[-1]) if len(m5) else 0,
        price=price,
        digits=digits,
        trend=matrix,
        structures=structures,
        levels=levels,
        volatility=vol,
        session=session,
        spread=spread,
        patterns=patterns,
        quality=quality,
        events=relevant,
        card=card,
        bars=dict(bars),
    )
