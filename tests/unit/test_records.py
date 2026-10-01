import json
from datetime import UTC, datetime
from types import SimpleNamespace, TracebackType
from typing import Any

from app.observability.categories import LogCategory
from app.observability.context import SESSION_ID, trace
from app.observability.levels import LevelRegistry, LogLevel
from app.observability.masking import MASK, SecretMasker
from app.observability.records import (
    DROP_KEY,
    ENTRY_KEY,
    JSON_KEY,
    LINE_KEY,
    RecordPatcher,
    format_line,
    utc_iso,
)

TOKEN = "ghp" + "_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


def make_record(
    message: str = "hello",
    level: str = "INFO",
    extra: dict[str, Any] | None = None,
    exception: ExcInfo | None = None,
) -> dict[str, Any]:
    """The shape of a loguru record, as far as the patcher uses it."""
    return {
        "time": datetime(2026, 10, 1, 9, 30, 0, 123456, tzinfo=UTC),
        "level": SimpleNamespace(name=level, no=int(LogLevel[level])),
        "message": message,
        "name": "app.tests.sample",
        "module": "sample",
        "function": "do_work",
        "line": 42,
        "thread": SimpleNamespace(name="MainThread", id=1),
        "process": SimpleNamespace(name="MainProcess", id=1),
        "extra": dict(extra or {}),
        "exception": exception,
    }


def raised(error: BaseException) -> ExcInfo:
    try:
        raise error
    except BaseException as caught:
        return type(caught), caught, caught.__traceback__


def test_patcher_builds_a_masked_entry_with_context() -> None:
    masker = SecretMasker()
    masker.register("correct-horse-battery")
    patcher = RecordPatcher(LevelRegistry(), masker)
    record = make_record(
        f"login with correct-horse-battery and token={TOKEN}",
        extra={"category": "mt5", "attempt": 2, "api_key": "k" * 10, "ticket": 77},
    )
    with trace(symbol="EURUSD", strategy="trend_pullback") as trace_id:
        patcher(record)
    entry = record["extra"][ENTRY_KEY]
    assert entry["message"] == f"login with {MASK} and token={MASK}"
    assert record["message"] == entry["message"]
    assert entry["time"] == "2026-10-01T09:30:00.123Z"
    assert entry["level"] == "INFO"
    assert entry["category"] == "mt5"
    assert entry["module"] == "app.tests.sample"
    assert (entry["function"], entry["line"], entry["thread"]) == ("do_work", 42, "MainThread")
    assert entry["session_id"] == SESSION_ID
    assert entry["trace_id"] == trace_id
    assert (entry["symbol"], entry["strategy"], entry["ticket"]) == ("EURUSD", "trend_pullback", 77)
    assert entry["context"] == {"attempt": 2, "api_key": MASK}
    assert json.loads(record["extra"][JSON_KEY]) == entry
    assert "correct-horse-battery" not in record["extra"][LINE_KEY]


def test_explicit_fields_win_over_the_ambient_trace() -> None:
    patcher = RecordPatcher(LevelRegistry(), SecretMasker())
    record = make_record(extra={"trace_id": "explicit", "symbol": "XAUUSD"})
    with trace(symbol="EURUSD"):
        patcher(record)
    entry = record["extra"][ENTRY_KEY]
    assert (entry["trace_id"], entry["symbol"]) == ("explicit", "XAUUSD")


def test_records_below_the_category_level_are_dropped_early() -> None:
    registry = LevelRegistry()
    patcher = RecordPatcher(registry, SecretMasker())
    debug = make_record("details", level="DEBUG", extra={"category": "risk"})
    patcher(debug)
    assert debug["extra"][DROP_KEY] is True
    assert ENTRY_KEY not in debug["extra"]
    registry.enable_debug(5)
    again = make_record("details", level="DEBUG", extra={"category": "risk"})
    patcher(again)
    assert DROP_KEY not in again["extra"]


def test_exceptions_become_a_masked_stack_and_are_removed_from_the_record() -> None:
    patcher = RecordPatcher(LevelRegistry(), SecretMasker())
    record = make_record("failed", level="ERROR", exception=raised(ValueError(f"token={TOKEN}")))
    patcher(record)
    entry = record["extra"][ENTRY_KEY]
    assert record["exception"] is None
    assert entry["exception_type"] == "ValueError"
    assert "Traceback" in entry["stack_trace"]
    assert TOKEN not in entry["stack_trace"]
    assert TOKEN not in record["extra"][LINE_KEY]
    assert "ValueError" in record["extra"][LINE_KEY]


def test_unknown_categories_are_logged_as_app() -> None:
    patcher = RecordPatcher(LevelRegistry(), SecretMasker())
    record = make_record(extra={"category": "made_up"})
    patcher(record)
    assert record["extra"]["category"] == LogCategory.APP.value


def test_a_broken_record_never_leaks_its_text() -> None:
    patcher = RecordPatcher(LevelRegistry(), SecretMasker())
    record = make_record(f"token={TOKEN}")
    del record["thread"]
    patcher(record)
    entry = record["extra"][ENTRY_KEY]
    assert TOKEN not in record["extra"][JSON_KEY]
    assert "could not be processed (KeyError)" in entry["message"]


def test_lines_are_readable_and_json_stays_on_one_line() -> None:
    patcher = RecordPatcher(LevelRegistry(), SecretMasker())
    record = make_record("first line\nsecond line", extra={"symbol": "EURUSD"})
    patcher(record)
    assert "\n" not in record["extra"][JSON_KEY]
    line = format_line(record["extra"][ENTRY_KEY])
    assert line.startswith("2026-10-01 09:30:00.123Z | INFO     | app         | first line")
    assert "symbol=EURUSD" in line


def test_utc_iso_handles_aware_and_naive_times() -> None:
    aware = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)
    assert utc_iso(aware) == "2026-01-02T03:04:05.678Z"
    naive = datetime(2026, 1, 2, 3, 4, 5)
    assert utc_iso(naive) == utc_iso(naive.astimezone())
