"""Pure filtering and formatting for the Logs page (no Qt, so it is unit-tested directly)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.observability.levels import LogLevel, level_number
from app.observability.log_reader import entry_time

Matcher = Callable[[Mapping[str, Any]], bool]

SEARCH_FIELDS: tuple[str, ...] = (
    "message",
    "module",
    "function",
    "trace_id",
    "signal_id",
    "trade_id",
    "ticket",
    "symbol",
    "strategy",
    "exception_type",
    "stack_trace",
)
SHORT_TRACE_LENGTH = 8


@dataclass(frozen=True)
class LogFilter:
    category: str | None = None
    min_level: LogLevel = LogLevel.TRACE
    text: str = ""
    regex: bool = False
    trace_id: str | None = None
    symbol: str = ""  # part of the symbol, any case ("eur" finds EURUSD.m)
    strategy: str = ""
    since: float | None = None  # UTC seconds
    until: float | None = None


def searchable_text(entry: Mapping[str, Any]) -> str:
    return " ".join(str(entry[key]) for key in SEARCH_FIELDS if entry.get(key) is not None)


def _part_of(needle: str, value: object) -> bool:
    return not needle or (value is not None and needle in str(value).casefold())


def _in_time(log_filter: LogFilter, entry: Mapping[str, Any]) -> bool:
    if log_filter.since is None and log_filter.until is None:
        return True
    moment = entry_time(entry)
    if moment is None:
        return False
    if log_filter.since is not None and moment < log_filter.since:
        return False
    return log_filter.until is None or moment <= log_filter.until


def make_matcher(log_filter: LogFilter) -> tuple[Matcher, str | None]:
    """A predicate for the filter, plus an error text when the regex is invalid.

    An invalid regex falls back to a plain, case-insensitive text search.
    """
    error: str | None = None
    needle = log_filter.text.strip()
    pattern: re.Pattern[str] | None = None
    if needle and log_filter.regex:
        try:
            pattern = re.compile(needle, re.IGNORECASE)
        except re.error as problem:
            error = f"Invalid pattern ({problem}); searching for the plain text instead."
    folded = needle.casefold()
    symbol = log_filter.symbol.strip().casefold()
    strategy = log_filter.strategy.strip().casefold()

    def matches(entry: Mapping[str, Any]) -> bool:
        if log_filter.category is not None and entry.get("category") != log_filter.category:
            return False
        if level_number(entry.get("level")) < log_filter.min_level:
            return False
        if log_filter.trace_id is not None and entry.get("trace_id") != log_filter.trace_id:
            return False
        if not _part_of(symbol, entry.get("symbol")):
            return False
        if not _part_of(strategy, entry.get("strategy")):
            return False
        if not _in_time(log_filter, entry):
            return False
        if not needle:
            return True
        haystack = searchable_text(entry)
        if pattern is not None:
            return pattern.search(haystack) is not None
        return folded in haystack.casefold()

    return matches, error


def display_time(iso_time: object) -> str:
    """UTC ISO time from a log entry, shown as local HH:MM:SS.mmm."""
    text = str(iso_time)
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return moment.astimezone().strftime("%H:%M:%S.%f")[:-3]


def row_values(entry: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    message = str(entry.get("message", ""))
    first_line, _, rest = message.partition("\n")
    if rest:
        first_line = f"{first_line} …"
    if entry.get("exception_type"):
        first_line = f"{first_line} [{entry['exception_type']}]"
    trace_id = str(entry.get("trace_id") or "")
    return (
        display_time(entry.get("time", "")),
        str(entry.get("level", "")),
        str(entry.get("category", "")),
        first_line,
        trace_id[:SHORT_TRACE_LENGTH],
    )


def detail_text(entry: Mapping[str, Any]) -> str:
    return json.dumps(entry, ensure_ascii=False, indent=2, default=str)
