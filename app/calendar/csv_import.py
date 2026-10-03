"""Read calendar events from CSV: the MT5 exporter's file or a file the user made.

Columns are found by their header name, in any order. Required: time, currency, impact (or
importance) and title (or event). Optional: actual, forecast, previous. Times are UTC, as
seconds since 1970 or as text like `2026-10-02 12:30`.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.calendar.models import CalendarEvent, parse_impact

ALIASES: dict[str, tuple[str, ...]] = {
    "time": ("time_utc", "time", "datetime", "date_time", "timestamp"),
    "currency": ("currency", "ccy"),
    "impact": ("impact", "importance"),
    "title": ("event", "title", "name"),
    "actual": ("actual",),
    "forecast": ("forecast", "consensus"),
    "previous": ("previous", "prior"),
}
TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M")
MAX_ERRORS = 20


@dataclass
class CsvResult:
    events: list[CalendarEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: int = 0

    def text(self) -> str:
        found = f"{len(self.events)} events read"
        if self.errors:
            found += f", {self.skipped} lines skipped (first problem: {self.errors[0]})"
        return found


def decode(data: bytes) -> str:
    """UTF-8 (with or without BOM) or UTF-16 as Windows tools write it."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def parse_time(text: str) -> int:
    value = text.strip().replace("T", " ").removesuffix("Z").removesuffix(" UTC")
    if value.lstrip("-").isdigit():
        return int(value)
    for pattern in TIME_FORMATS:
        try:
            return int(datetime.strptime(value, pattern).replace(tzinfo=UTC).timestamp())
        except ValueError:
            continue
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"unknown time {text!r}") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp())


def _columns(header: list[str]) -> dict[str, int]:
    names = [name.strip().casefold() for name in header]
    found: dict[str, int] = {}
    for key, aliases in ALIASES.items():
        for alias in aliases:
            if alias in names:
                found[key] = names.index(alias)
                break
    return found


def parse_calendar_csv(text: str, source: str = "csv") -> CsvResult:
    result = CsvResult()
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        result.errors.append("the file is empty")
        return result
    columns = _columns(rows[0])
    missing = [key for key in ("time", "currency", "impact", "title") if key not in columns]
    if missing:
        result.errors.append(f"missing columns: {', '.join(missing)}")
        return result
    for number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue

        def cell(key: str, values: list[str] = row) -> str:
            index = columns.get(key)
            return values[index].strip() if index is not None and index < len(values) else ""

        try:
            impact = parse_impact(cell("impact"))
            if impact is None:
                raise ValueError(f"unknown impact {cell('impact')!r}")
            currency = cell("currency").upper()
            title = cell("title")
            if not currency or not title:
                raise ValueError("currency and title are required")
            event = CalendarEvent(
                time=parse_time(cell("time")),
                currency=currency,
                impact=impact,
                title=title,
                actual=cell("actual"),
                forecast=cell("forecast"),
                previous=cell("previous"),
                source=source,
            )
        except ValueError as error:
            result.skipped += 1
            if len(result.errors) < MAX_ERRORS:
                result.errors.append(f"line {number}: {error}")
            continue
        result.events.append(event)
    return result
