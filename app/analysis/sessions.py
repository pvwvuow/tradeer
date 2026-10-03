"""Trading sessions (spec C3): Asia, London and New York in UTC, with their own summer time.

Tokyo 09:00-18:00 (no summer time), London 08:00-17:00 and New York 08:00-17:00 local time.
The FX week runs from Sunday 17:00 to Friday 17:00 New York time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

import numpy as np

from app.analysis.bars import Bars
from app.core.clock import DAY, HOUR, eu_dst_active, us_dst_active


class Session(StrEnum):
    ASIA = "Asia"
    LONDON = "London"
    NEW_YORK = "New York"


SESSIONS: tuple[Session, ...] = (Session.ASIA, Session.LONDON, Session.NEW_YORK)
STATS_DAYS = 20


@dataclass(frozen=True)
class SessionWindow:
    session: Session
    start: int
    end: int

    def contains(self, utc_seconds: float) -> bool:
        return self.start <= utc_seconds < self.end


def _midnight(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


def session_windows(day: date) -> tuple[SessionWindow, ...]:
    """The three sessions that open on this UTC date."""
    midnight = _midnight(day)
    noon = midnight + 12 * HOUR
    london = 8 - (1 if eu_dst_active(noon) else 0)
    new_york = 13 - (1 if us_dst_active(noon) else 0)
    return (
        SessionWindow(Session.ASIA, midnight, midnight + 9 * HOUR),
        SessionWindow(Session.LONDON, midnight + london * HOUR, midnight + (london + 9) * HOUR),
        SessionWindow(
            Session.NEW_YORK,
            midnight + new_york * HOUR,
            midnight + (new_york + 9) * HOUR,
        ),
    )


def _new_york_hour(utc_seconds: float) -> tuple[int, float]:
    """(weekday, hour) in New York local time."""
    offset = -4 if us_dst_active(utc_seconds) else -5
    local = datetime.fromtimestamp(utc_seconds + offset * HOUR, UTC)
    return local.weekday(), local.hour + local.minute / 60.0


def market_open(utc_seconds: float) -> bool:
    """FX is open from Sunday 17:00 to Friday 17:00 New York time."""
    weekday, hour = _new_york_hour(utc_seconds)
    if weekday == 5:
        return False
    if weekday == 4:
        return hour < 17
    if weekday == 6:
        return hour >= 17
    return True


def active_sessions(utc_seconds: float) -> tuple[Session, ...]:
    if not market_open(utc_seconds):
        return ()
    day = datetime.fromtimestamp(utc_seconds, UTC).date()
    windows = (*session_windows(day - timedelta(days=1)), *session_windows(day))
    found = {window.session for window in windows if window.contains(utc_seconds)}
    return tuple(session for session in SESSIONS if session in found)


def session_label(utc_seconds: float) -> str:
    if not market_open(utc_seconds):
        return "Market closed (weekend)"
    active = active_sessions(utc_seconds)
    if not active:
        return "Between sessions"
    if len(active) > 1:
        return " + ".join(session.value for session in active) + " overlap"
    return f"{active[0].value} session"


def next_change(utc_seconds: float) -> tuple[str, int]:
    """The next session open or close and the seconds until it, for the status bar clock."""
    day = datetime.fromtimestamp(utc_seconds, UTC).date()
    events: list[tuple[int, str]] = []
    for offset in range(-1, 4):
        for window in session_windows(day + timedelta(days=offset)):
            if market_open(window.start + 1):
                events.append((window.start, f"{window.session.value} opens"))
            if market_open(window.end - 1):
                events.append((window.end, f"{window.session.value} closes"))
    upcoming = sorted(event for event in events if event[0] > utc_seconds)
    if not upcoming:
        return "", 0
    moment, text = upcoming[0]
    return text, int(moment - utc_seconds)


def duration_text(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    days, rest = divmod(seconds, DAY)
    hours, rest = divmod(rest, HOUR)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


@dataclass(frozen=True)
class SessionRange:
    session: Session
    day: date
    high: float
    low: float

    @property
    def size(self) -> float:
        return self.high - self.low


@dataclass(frozen=True)
class SessionStat:
    session: Session
    average_range: float
    days: int
    today: SessionRange | None


def session_ranges(bars: Bars, day: date) -> list[SessionRange]:
    """High and low of each session that opened on `day`, from intraday bars."""
    found: list[SessionRange] = []
    for window in session_windows(day):
        mask = (bars.time >= window.start) & (bars.time < window.end)
        if np.any(mask):
            high = float(np.max(bars.high[mask]))
            low = float(np.min(bars.low[mask]))
            found.append(SessionRange(window.session, day, high, low))
    return found


def session_stats(bars: Bars, now: float, days: int = STATS_DAYS) -> list[SessionStat]:
    """Average range of each session over the last trading days, and today's range so far."""
    today = datetime.fromtimestamp(now, UTC).date()
    history: dict[Session, list[float]] = {session: [] for session in SESSIONS}
    current: dict[Session, SessionRange] = {}
    for back in range(days * 2):
        day = today - timedelta(days=back)
        if day.weekday() >= 5:
            continue
        for item in session_ranges(bars, day):
            if back == 0:
                current[item.session] = item
            elif len(history[item.session]) < days:
                history[item.session].append(item.size)
    stats: list[SessionStat] = []
    for session in SESSIONS:
        sizes: Sequence[float] = history[session]
        average = float(np.mean(sizes)) if sizes else float("nan")
        stats.append(SessionStat(session, average, len(sizes), current.get(session)))
    return stats
