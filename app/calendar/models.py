"""Calendar events in UTC, with a stable id so that importing twice never duplicates one."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from app.storage.ids import stable_id

WARN_BEFORE_SECONDS = 60 * 60
WARN_AFTER_SECONDS = 15 * 60


class Impact(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"

    @property
    def rank(self) -> int:
        return {"high": 3, "medium": 2, "low": 1, "none": 0}[self.value]


IMPACT_WORDS: dict[str, Impact] = {
    "high": Impact.HIGH,
    "3": Impact.HIGH,
    "medium": Impact.MEDIUM,
    "moderate": Impact.MEDIUM,
    "2": Impact.MEDIUM,
    "low": Impact.LOW,
    "1": Impact.LOW,
    "none": Impact.NONE,
    "holiday": Impact.NONE,
    "0": Impact.NONE,
    "": Impact.NONE,
}


def parse_impact(value: str) -> Impact | None:
    return IMPACT_WORDS.get(value.strip().casefold())


@dataclass(frozen=True)
class CalendarEvent:
    time: int  # UTC seconds
    currency: str
    impact: Impact
    title: str
    actual: str = ""
    forecast: str = ""
    previous: str = ""
    source: str = "manual"

    @property
    def id(self) -> str:
        key = " ".join(self.title.casefold().split())
        return stable_id("calendar", self.time, self.currency.upper(), key)

    def when(self) -> str:
        return datetime.fromtimestamp(self.time, UTC).strftime("%a %d %b %H:%M UTC")

    def countdown(self, now: float) -> str:
        seconds = self.time - now
        if seconds < 0:
            minutes = int(-seconds // 60)
            return f"{minutes} min ago" if minutes < 120 else "earlier"
        minutes = int(seconds // 60)
        if minutes < 60:
            return f"in {minutes} min"
        hours = seconds / 3600.0
        if hours < 48:
            return f"in {math.floor(hours)}h {int(minutes % 60):02d}m"
        return f"in {math.floor(hours / 24)} days"

    def short(self, now: float) -> str:
        return f"{self.currency} {self.title} {self.countdown(now)}"


def matches_symbol(event: CalendarEvent, symbol: str) -> bool:
    """An event matters for a symbol when its currency is part of the name (EURUSD, XAUUSD)."""
    return bool(event.currency) and event.currency.upper() in symbol.upper()


def upcoming(
    events: Iterable[CalendarEvent],
    now: float,
    *,
    hours: float = 48.0,
    min_impact: Impact = Impact.MEDIUM,
    symbols: Sequence[str] = (),
) -> list[CalendarEvent]:
    """Events from 15 minutes ago up to `hours` ahead, the soonest first, without repeats."""
    seen: set[str] = set()
    found: list[CalendarEvent] = []
    for event in sorted(events, key=lambda item: (item.time, -item.impact.rank)):
        if event.impact.rank < min_impact.rank or event.id in seen:
            continue
        if not now - WARN_AFTER_SECONDS <= event.time <= now + hours * 3600:
            continue
        if symbols and not any(matches_symbol(event, symbol) for symbol in symbols):
            continue
        seen.add(event.id)
        found.append(event)
    return found


def blocking_event(
    events: Iterable[CalendarEvent],
    symbol: str,
    now: float,
) -> CalendarEvent | None:
    """A high-impact event for this symbol within the next hour or the last 15 minutes."""
    for event in sorted(events, key=lambda item: item.time):
        if event.impact is not Impact.HIGH or not matches_symbol(event, symbol):
            continue
        if now - WARN_AFTER_SECONDS <= event.time <= now + WARN_BEFORE_SECONDS:
            return event
    return None
