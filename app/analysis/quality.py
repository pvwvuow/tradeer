"""Data sanity checks (spec C2). Bad data means: skip the evaluation and log why.

Errors stop the analysis of a symbol (stale prices, broken bars, a spike on the newest bars,
a broker time jump). Warnings are shown but do not stop it (missing bars, zero volume).
Weekend gaps are normal and never reported.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from app.analysis import indicators
from app.analysis.bars import Bars
from app.analysis.sessions import market_open
from app.core.clock import DAY, OffsetChange

SPIKE_ATR = 10.0
RECENT_BARS = 3
CHECK_BARS = 200
STALE_TICK_SECONDS = 300
JUMP_MEMORY_SECONDS = DAY
JUMP_BLOCK_SECONDS = 3600
DAILY_BREAK_SECONDS = 75 * 60


class Severity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    code: str
    message: str

    def text(self) -> str:
        return f"{self.severity.value}: {self.message}"


@dataclass(frozen=True)
class QualityReport:
    issues: tuple[Issue, ...]

    @property
    def ok(self) -> bool:
        return not any(issue.severity is Severity.ERROR for issue in self.issues)

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity is Severity.WARNING]

    def text(self) -> str:
        return "; ".join(issue.message for issue in self.issues) or "data OK"


def _open_seconds(start: int, end: int) -> int:
    """How long the FX market was open between two times (weekends and holidays excluded)."""
    probes = range(start, end, 300)
    return 300 * sum(1 for probe in probes if market_open(float(probe)))


def check_bars(bars: Bars, now: float) -> list[Issue]:
    issues: list[Issue] = []
    name = f"{bars.symbol} {bars.timeframe}"
    if not len(bars):
        return [Issue(Severity.ERROR, "no_bars", f"{name}: no closed bars")]
    recent = bars.tail(CHECK_BARS)
    prices = np.vstack([recent.open, recent.high, recent.low, recent.close])
    if not np.all(np.isfinite(prices)) or np.any(prices <= 0):
        issues.append(Issue(Severity.ERROR, "bad_price", f"{name}: missing or zero prices"))
        return issues
    if np.any(recent.high < recent.low) or np.any(np.diff(recent.time) <= 0):
        issues.append(Issue(Severity.ERROR, "broken_bars", f"{name}: bars out of order or broken"))
        return issues
    step = bars.seconds
    gaps = np.diff(recent.time)
    limit = max(2 * step, DAILY_BREAK_SECONDS) if step < DAY else 4 * DAY
    missing = 0
    for index in np.flatnonzero(gaps > limit):
        start, end = int(recent.time[index]), int(recent.time[index + 1])
        open_seconds = _open_seconds(start + step, end)
        if open_seconds > limit:
            missing += open_seconds // step
    if missing:
        issues.append(
            Issue(Severity.WARNING, "missing_bars", f"{name}: about {missing} bars missing"),
        )
    zero = int(np.count_nonzero(recent.volume[-50:] == 0))
    if zero:
        issues.append(Issue(Severity.WARNING, "zero_volume", f"{name}: {zero} bars with no ticks"))
    atr_line = indicators.atr(recent.high, recent.low, recent.close)
    if len(recent) > RECENT_BARS + 15:
        reference = indicators.last(atr_line[:-RECENT_BARS])
        newest = recent.tail(RECENT_BARS)
        sizes = newest.high - newest.low
        if reference > 0 and float(np.max(sizes)) > SPIKE_ATR * reference:
            issues.append(
                Issue(
                    Severity.ERROR,
                    "spike",
                    f"{name}: a bar {float(np.max(sizes)) / reference:.0f}x the ATR (spike)",
                ),
            )
    newest_close = int(bars.time[-1]) + step
    late = step < DAY and now - newest_close > max(3 * step, 900)
    if late and market_open(now) and market_open(float(newest_close)):
        minutes = (now - newest_close) / 60.0
        message = f"{name}: newest closed bar is {minutes:.0f} min old"
        issues.append(Issue(Severity.WARNING, "late_bars", message))
    return issues


def check_tick(
    symbol: str,
    tick_utc: float | None,
    now: float,
    last_bar_close: float | None = None,
) -> list[Issue]:
    if not market_open(now):
        return []
    if tick_utc is None:
        return [Issue(Severity.ERROR, "no_tick", f"{symbol}: no current price")]
    age = now - tick_utc
    if age <= STALE_TICK_SECONDS:
        return []
    minutes = age / 60.0
    if last_bar_close is None or now - last_bar_close > 2 * STALE_TICK_SECONDS:
        # Neither prices nor bars move: this symbol is not trading now (daily break, holiday).
        message = f"{symbol}: not trading right now (no price for {minutes:.0f} min)"
        return [Issue(Severity.WARNING, "symbol_closed", message)]
    message = f"{symbol}: bars move but the price is {minutes:.0f} min old (stale)"
    return [Issue(Severity.ERROR, "stale_tick", message)]


def check_clock(changes: Sequence[OffsetChange], now: float) -> list[Issue]:
    """A time jump stops the analysis for an hour, then stays a warning for a day."""
    recent = [change for change in changes if now - change.utc_time < JUMP_MEMORY_SECONDS]
    issues: list[Issue] = []
    for change in recent[-1:]:
        blocking = now - change.utc_time < JUMP_BLOCK_SECONDS
        severity = Severity.ERROR if blocking else Severity.WARNING
        issues.append(Issue(severity, "time_jump", change.text()))
    return issues


def quality_report(
    bars: Sequence[Bars],
    *,
    symbol: str,
    tick_utc: float | None,
    now: float,
    clock_changes: Sequence[OffsetChange] = (),
    clock_measured: bool = True,
) -> QualityReport:
    issues: list[Issue] = []
    for item in bars:
        issues += check_bars(item, now)
    newest = [float(item.time[-1] + item.seconds) for item in bars if len(item)]
    issues += check_tick(symbol, tick_utc, now, max(newest) if newest else None)
    issues += check_clock(clock_changes, now)
    if not clock_measured:
        issues.append(
            Issue(
                Severity.WARNING,
                "clock_assumed",
                "broker time not measured yet, assuming UTC+2/+3 (New York close)",
            ),
        )
    return QualityReport(tuple(issues))
