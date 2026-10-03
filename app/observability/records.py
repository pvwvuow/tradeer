"""Turn a loguru record into a masked, JSON-ready entry. Pure: loguru is never imported here.

The patcher runs in the thread that logs, before any sink sees the record. It drops records
below the category level, masks the message, the fields and the stack trace, and pre-renders
the JSON line and the readable line, so every sink only ever receives masked text.
"""

from __future__ import annotations

import json
import traceback
from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

from app.observability.categories import DEFAULT_CATEGORY, LogCategory, parse_category
from app.observability.context import CONTEXT_FIELDS, snapshot
from app.observability.levels import LevelRegistry
from app.observability.masking import SecretMasker

CATEGORY_KEY = "category"
DROP_KEY = "_drop"
ENTRY_KEY = "_entry"
JSON_KEY = "_json"
LINE_KEY = "_line"

# Columns of the `app_logs` table (spec E2) that are stored at the top level of an entry.
TOP_LEVEL_FIELDS: tuple[str, ...] = ("session_id", "trace_id", *CONTEXT_FIELDS, "error_code")
LINE_TAGS: tuple[str, ...] = ("trace_id", "symbol", "strategy", "ticket")
FALLBACK_MESSAGE = (
    "A log record could not be processed ({error}); its text was dropped so that no secret "
    "can leak."
)


def utc_iso(moment: datetime) -> str:
    aware = moment if moment.tzinfo is not None else moment.astimezone()
    text = aware.astimezone(UTC).isoformat(timespec="milliseconds")
    return text.replace("+00:00", "Z")


def qualified_name(exc_type: type[BaseException]) -> str:
    if exc_type.__module__ in ("builtins", "__main__"):
        return exc_type.__qualname__
    return f"{exc_type.__module__}.{exc_type.__qualname__}"


def format_exception(
    exc_type: type[BaseException],
    exc_value: BaseException | None,
    exc_tb: TracebackType | None,
) -> str:
    if exc_value is None:
        return exc_type.__name__
    return "".join(traceback.format_exception(exc_type, exc_value, exc_tb))


def build_entry(
    record: Mapping[str, Any],
    category: LogCategory,
    masker: SecretMasker,
) -> dict[str, Any]:
    extra: Mapping[str, Any] = record["extra"]
    entry: dict[str, Any] = {
        "time": utc_iso(record["time"]),
        "level": record["level"].name,
        "category": category.value,
        "message": masker.mask(str(record["message"])),
        "module": record.get("name") or record.get("module"),
        "function": record.get("function"),
        "line": record.get("line"),
        "thread": record["thread"].name,
    }
    context = snapshot()
    for field in TOP_LEVEL_FIELDS:
        value = extra.get(field, context.get(field))
        if value is not None:
            entry[field] = masker.mask_value(value, field)
    others = {
        str(key): value
        for key, value in extra.items()
        if key not in TOP_LEVEL_FIELDS and key != CATEGORY_KEY and not str(key).startswith("_")
    }
    if others:
        entry["context"] = masker.mask_value(others)
    exception = record.get("exception")
    if exception:
        exc_type, exc_value, exc_tb = exception
        if exc_type is not None:
            entry["exception_type"] = qualified_name(exc_type)
            entry["stack_trace"] = masker.mask(format_exception(exc_type, exc_value, exc_tb))
    return entry


def fallback_entry(
    record: Mapping[str, Any],
    category: LogCategory,
    error: BaseException,
) -> dict[str, Any]:
    """Used when a record cannot be processed. The original text is dropped, never leaked."""
    level = record.get("level")
    moment = record.get("time")
    return {
        "time": utc_iso(moment if isinstance(moment, datetime) else datetime.now(UTC)),
        "level": getattr(level, "name", "ERROR"),
        "category": category.value,
        "message": FALLBACK_MESSAGE.format(error=type(error).__name__),
        "module": str(record.get("name") or record.get("module") or ""),
        "function": str(record.get("function") or ""),
        "line": record.get("line") if isinstance(record.get("line"), int) else 0,
    }


def format_line(entry: Mapping[str, Any]) -> str:
    """The human-readable line for `logs/all.log` and the console."""
    stamp = str(entry["time"]).replace("T", " ")
    text = f"{stamp} | {entry['level']:<8} | {entry['category']:<11} | {entry['message']}"
    tags = " ".join(f"{key}={entry[key]}" for key in LINE_TAGS if key in entry)
    if tags:
        text = f"{text} [{tags}]"
    stack = entry.get("stack_trace")
    if stack:
        text = f"{text}\n{str(stack).rstrip()}"
    return text


def render_json(entry: Mapping[str, Any]) -> str:
    return json.dumps(entry, ensure_ascii=False, separators=(",", ":"), default=str)


class RecordPatcher:
    """The loguru patcher: category levels, masking and pre-rendered outputs."""

    def __init__(self, registry: LevelRegistry, masker: SecretMasker) -> None:
        self._registry = registry
        self._masker = masker

    def __call__(self, record: MutableMapping[str, Any]) -> None:
        extra = record["extra"]
        category = parse_category(extra.get(CATEGORY_KEY, DEFAULT_CATEGORY))
        if not self._registry.accepts(category, int(record["level"].no)):
            extra[DROP_KEY] = True
            return
        try:
            entry = build_entry(record, category, self._masker)
            rendered = render_json(entry)
        except Exception as error:
            entry = fallback_entry(record, category, error)
            rendered = render_json(entry)
        record["message"] = entry["message"]
        # The exception is already rendered (masked) into the entry. Clearing it means no
        # sink can ever print the raw, unmasked traceback.
        record["exception"] = None
        record["extra"] = {
            CATEGORY_KEY: category.value,
            ENTRY_KEY: entry,
            JSON_KEY: rendered,
            LINE_KEY: format_line(entry),
        }
