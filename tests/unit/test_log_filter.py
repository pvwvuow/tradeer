import json
from datetime import UTC, datetime

from app.observability.levels import LogLevel
from app.ui.log_filter import LogFilter, detail_text, display_time, make_matcher, row_values

ENTRIES = [
    {"level": "INFO", "category": "mt5", "message": "Connected", "symbol": "EURUSD"},
    {"level": "WARNING", "category": "risk", "message": "Spread high", "trace_id": "abc123def456"},
    {
        "level": "ERROR",
        "category": "execution",
        "message": "Order failed",
        "exception_type": "TimeoutError",
        "trace_id": "abc123def456",
    },
    {"level": "DEBUG", "category": "mt5", "message": "tick"},
]


def visible(log_filter: LogFilter) -> list[str]:
    matcher, _ = make_matcher(log_filter)
    return [entry["message"] for entry in ENTRIES if matcher(entry)]


def test_filters_by_category_level_and_trace() -> None:
    assert visible(LogFilter()) == ["Connected", "Spread high", "Order failed", "tick"]
    assert visible(LogFilter(category="mt5")) == ["Connected", "tick"]
    assert visible(LogFilter(min_level=LogLevel.WARNING)) == ["Spread high", "Order failed"]
    assert visible(LogFilter(trace_id="abc123def456")) == ["Spread high", "Order failed"]


def test_text_search_covers_message_symbol_and_exception_fields() -> None:
    assert visible(LogFilter(text="eurusd")) == ["Connected"]
    assert visible(LogFilter(text="timeout")) == ["Order failed"]
    assert visible(LogFilter(text="  ")) == ["Connected", "Spread high", "Order failed", "tick"]


def test_regex_search_and_invalid_patterns() -> None:
    assert visible(LogFilter(text="^(connected|tick)", regex=True)) == ["Connected", "tick"]
    matcher, error = make_matcher(LogFilter(text="order (", regex=True))
    assert error is not None
    assert [entry["message"] for entry in ENTRIES if matcher(entry)] == []
    matcher, error = make_matcher(LogFilter(text="failed (", regex=True))
    assert error is not None
    ENTRIES.append({"level": "INFO", "category": "app", "message": "failed (twice)"})
    try:
        assert [entry["message"] for entry in ENTRIES if matcher(entry)] == ["failed (twice)"]
    finally:
        ENTRIES.pop()


def test_rows_show_local_time_first_line_and_short_trace() -> None:
    utc_time = "2026-10-01T09:30:00.250Z"
    expected = datetime(2026, 10, 1, 9, 30, 0, 250000, tzinfo=UTC).astimezone()
    entry = {
        "time": utc_time,
        "level": "ERROR",
        "category": "app",
        "message": "first\nsecond",
        "exception_type": "ValueError",
        "trace_id": "0123456789abcdef",
    }
    assert row_values(entry) == (
        expected.strftime("%H:%M:%S.250"),
        "ERROR",
        "app",
        "first … [ValueError]",
        "01234567",
    )
    assert display_time("not a time") == "not a time"
    assert json.loads(detail_text(entry)) == entry
