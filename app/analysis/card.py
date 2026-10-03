"""The plain-language analysis card per symbol (spec C3). Information only, never a signal.

Example headline: "XAUUSD: H4 uptrend, H1 pullback into support 2,318.20 (0.4 ATR),
volatility high, London session, USD CPI in 3h 05m -> wait".
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.analysis.levels import NEAR_ATR, LevelSet
from app.analysis.patterns import Pattern
from app.analysis.quality import QualityReport
from app.analysis.spread import SpreadStatus
from app.analysis.structure import Structure, Trend
from app.analysis.trend import TREND_THRESHOLD, TrendMatrix
from app.analysis.volatility import Volatility
from app.calendar.models import CalendarEvent, blocking_event

NOT_A_SIGNAL = "Information only, not a trade signal."


class Verdict(StrEnum):
    WAIT = "wait"
    WATCH = "watch"
    UNCLEAR = "no clear direction"
    DATA_PROBLEM = "data problem"


@dataclass(frozen=True)
class AnalysisCard:
    symbol: str
    headline: str
    verdict: Verdict
    reason: str
    lines: tuple[str, ...]
    bias: float

    def text(self) -> str:
        return "\n".join([self.headline, *self.lines, NOT_A_SIGNAL])


_WORDS = {Trend.UP: "uptrend", Trend.DOWN: "downtrend", Trend.RANGE: "no clear trend"}


def _higher(matrix: TrendMatrix) -> tuple[str, Trend] | None:
    for timeframe in ("H4", "D1", "H1"):
        row = matrix.get(timeframe)
        if row is not None and row.enough_data:
            return timeframe, row.direction
    return None


def _context(matrix: TrendMatrix, levels: LevelSet, digits: int) -> str:
    """How H1 relates to the higher timeframe, and the level the price is at."""
    higher = _higher(matrix)
    hourly = matrix.get("H1")
    parts: list[str] = []
    if higher is not None and higher[0] != "H1" and hourly is not None and hourly.enough_data:
        if hourly.direction is Trend.RANGE:
            parts.append("H1 ranging")
        elif higher[1] is Trend.RANGE or hourly.direction is higher[1]:
            parts.append(f"H1 {_WORDS[hourly.direction]}")
        else:
            parts.append("H1 pullback")
    near = levels.near(NEAR_ATR)
    if near:
        level = near[0]
        side = level.side
        moving_down = hourly is not None and hourly.direction is Trend.DOWN
        moving_up = hourly is not None and hourly.direction is Trend.UP
        heading = (side == "support" and moving_down) or (side == "resistance" and moving_up)
        word = "into" if heading else "near"
        where = f"{word} {side} {level.price:,.{digits}f} ({abs(level.distance_atr):.1f} ATR)"
        parts.append(f"{parts.pop()} {where}" if parts else where)
    return ", ".join(parts)


def _verdict(
    matrix: TrendMatrix,
    quality: QualityReport,
    volatility: Volatility,
    spread: SpreadStatus | None,
    blocking: CalendarEvent | None,
    trading: bool,
    now: float,
) -> tuple[Verdict, str]:
    if not quality.ok:
        return Verdict.DATA_PROBLEM, quality.errors[0].message
    if not trading:
        return Verdict.WAIT, "the market is closed for this symbol"
    if blocking is not None:
        return Verdict.WAIT, f"high-impact news: {blocking.short(now)}"
    if spread is not None and spread.status == "very wide":
        return Verdict.WAIT, f"spread very wide ({spread.ratio:.1f}x typical)"
    if volatility.regime == "extreme":
        return Verdict.WAIT, "volatility extreme"
    higher = _higher(matrix)
    if abs(matrix.bias) < TREND_THRESHOLD or higher is None or higher[1] is Trend.RANGE:
        return Verdict.UNCLEAR, f"bias {matrix.bias:+.0f}, timeframes disagree"
    if higher[1] is not matrix.direction:
        return Verdict.UNCLEAR, f"{higher[0]} disagrees with the bias {matrix.bias:+.0f}"
    side = "up" if matrix.direction is Trend.UP else "down"
    return Verdict.WATCH, f"{higher[0]} and the bias ({matrix.bias:+.0f}) point {side}"


def build_card(
    *,
    symbol: str,
    digits: int,
    now: float,
    matrix: TrendMatrix,
    structure: Structure | None,
    levels: LevelSet,
    volatility: Volatility,
    session: str,
    spread: SpreadStatus | None,
    patterns: Sequence[Pattern],
    quality: QualityReport,
    events: Sequence[CalendarEvent],
    trading: bool = True,
) -> AnalysisCard:
    blocking = blocking_event(events, symbol, now)
    verdict, reason = _verdict(matrix, quality, volatility, spread, blocking, trading, now)
    parts: list[str] = []
    higher = _higher(matrix)
    if higher is not None:
        parts.append(f"{higher[0]} {_WORDS[higher[1]]}")
    context = _context(matrix, levels, digits)
    if context:
        parts.append(context)
    if volatility.regime != "unknown":
        parts.append(f"volatility {volatility.regime}")
    parts.append(session)
    soon = [event for event in events if event.time >= now - 15 * 60]
    if blocking is not None:
        parts.append(blocking.short(now))
    elif soon and soon[0].time - now <= 24 * 3600:
        parts.append(soon[0].short(now))
    headline = f"{symbol}: {', '.join(parts)} \u2192 {verdict.value}"
    lines = [f"Why {verdict.value}: {reason}."]
    trend_cells = [f"{row.timeframe} {row.score:+.0f}" for row in matrix.rows if row.enough_data]
    if trend_cells:
        lines.append(f"Trend: {', '.join(trend_cells)}; bias {matrix.bias:+.0f}.")
    if structure is not None and structure.swings:
        last = structure.last_event
        event_text = f", last {last.text()}" if last is not None else ""
        lines.append(
            f"Structure {structure.timeframe}: {structure.labels() or 'first swings'}"
            f" ({_WORDS[structure.trend]}){event_text}.",
        )
    support, resistance = levels.nearest_support, levels.nearest_resistance
    found = [level.text(digits) for level in (support, resistance) if level is not None]
    if found:
        lines.append(f"Levels: {'; '.join(found)}.")
    lines.append(f"{volatility.text()[0].upper()}{volatility.text()[1:]}.")
    if spread is not None and math.isfinite(spread.current_points):
        lines.append(f"{spread.text()[0].upper()}{spread.text()[1:]}.")
    if patterns:
        lines.append(f"Candles: {', '.join(pattern.text() for pattern in patterns)}.")
    if quality.issues:
        lines.append(f"Data: {quality.text()}.")
    return AnalysisCard(symbol, headline, verdict, reason, tuple(lines), matrix.bias)
