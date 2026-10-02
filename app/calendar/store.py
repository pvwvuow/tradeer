"""Calendar events in the local database (table `calendar_events`, synced like the others)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from app.calendar.exporter import CalendarFileWatcher
from app.calendar.models import CalendarEvent, Impact, parse_impact
from app.storage.repositories import Store
from app.storage.sqlite_db import iso_utc

LOOKBACK_SECONDS = 2 * 86_400


def _iso(seconds: int) -> str:
    return iso_utc(datetime.fromtimestamp(seconds, UTC))


def _seconds(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def event_row(event: CalendarEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "account_id": None,
        "time": _iso(event.time),
        "currency": event.currency,
        "impact": event.impact.value,
        "title": event.title,
        "actual": event.actual,
        "forecast": event.forecast,
        "previous": event.previous,
        "source": event.source,
    }


def event_from_row(row: dict[str, Any]) -> CalendarEvent:
    return CalendarEvent(
        time=_seconds(str(row["time"])),
        currency=str(row["currency"] or ""),
        impact=parse_impact(str(row["impact"] or "")) or Impact.NONE,
        title=str(row["title"] or ""),
        actual=str(row["actual"] or ""),
        forecast=str(row["forecast"] or ""),
        previous=str(row["previous"] or ""),
        source=str(row["source"] or ""),
    )


class CalendarStore:
    def __init__(self, store: Store) -> None:
        self._store = store

    def save(self, events: Iterable[CalendarEvent]) -> int:
        """Insert or update events. Returns how many were new or changed."""
        changed = 0
        with self._store.db.transaction() as connection:
            for event in events:
                changed += self._store.upsert_in(connection, "calendar_events", event_row(event))
        return changed

    def between(self, start: int, end: int) -> list[CalendarEvent]:
        rows = self._store.db.query(
            "SELECT * FROM calendar_events WHERE time >= ? AND time <= ? ORDER BY time",
            (_iso(start), _iso(end)),
        )
        return [event_from_row(row) for row in rows]

    def recent_and_upcoming(self, now: float, hours: float = 7 * 24) -> list[CalendarEvent]:
        return self.between(int(now) - LOOKBACK_SECONDS, int(now + hours * 3600))


def import_exporter_file(watcher: CalendarFileWatcher, calendar: CalendarStore) -> str:
    """Save the MT5 exporter's events if its file changed. Blocks on the file and database."""
    result = watcher.check()
    if result is None:
        return "The MT5 calendar file did not change."
    saved = calendar.save(result.events)
    return f"MT5 calendar: {result.text()}, {saved} new or changed."
