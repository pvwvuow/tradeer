from datetime import UTC, date, datetime

from app.analysis.sessions import (
    Session,
    active_sessions,
    duration_text,
    market_open,
    next_change,
    session_label,
    session_ranges,
    session_stats,
    session_windows,
)
from tests.unit.analysis_helpers import bars_from_closes


def utc(*parts: int) -> float:
    return datetime(*parts, tzinfo=UTC).timestamp()


def test_london_and_new_york_move_with_their_own_summer_time() -> None:
    summer = {window.session: window for window in session_windows(date(2026, 7, 1))}
    winter = {window.session: window for window in session_windows(date(2026, 1, 15))}
    assert summer[Session.LONDON].start == utc(2026, 7, 1, 7)
    assert winter[Session.LONDON].start == utc(2026, 1, 15, 8)
    assert summer[Session.NEW_YORK].start == utc(2026, 7, 1, 12)
    assert winter[Session.NEW_YORK].end == utc(2026, 1, 15, 22)
    assert summer[Session.ASIA].end == utc(2026, 7, 1, 9)
    # Between the US and the EU switch dates London is in winter time, New York in summer.
    gap = {window.session: window for window in session_windows(date(2026, 3, 16))}
    assert gap[Session.LONDON].start == utc(2026, 3, 16, 8)
    assert gap[Session.NEW_YORK].start == utc(2026, 3, 16, 12)


def test_the_fx_week_runs_from_sunday_to_friday_new_york_five_pm() -> None:
    assert not market_open(utc(2026, 10, 2, 21, 30))  # Friday 17:30 New York
    assert market_open(utc(2026, 10, 2, 20, 30))
    assert not market_open(utc(2026, 10, 3, 12))
    assert not market_open(utc(2026, 10, 4, 20, 30))
    assert market_open(utc(2026, 10, 4, 21, 30))  # Sunday 17:30 New York


def test_session_labels_and_the_clock() -> None:
    assert session_label(utc(2026, 10, 1, 12, 30)) == "London + New York overlap"
    assert session_label(utc(2026, 10, 1, 6)) == "Asia session"
    assert session_label(utc(2026, 10, 4, 21, 30)) == "Between sessions"
    assert session_label(utc(2026, 10, 3, 9)) == "Market closed (weekend)"
    assert active_sessions(utc(2026, 10, 1, 8)) == (Session.ASIA, Session.LONDON)
    assert next_change(utc(2026, 10, 1, 6, 40)) == ("London opens", 20 * 60)
    assert next_change(utc(2026, 10, 2, 23))[0] == "Asia opens"
    assert duration_text(2 * 3600 + 5 * 60) == "2h 05m"
    assert duration_text(59) == "0m"
    assert duration_text(3 * 86_400 + 3600) == "3d 1h"


def test_session_ranges_and_stats_from_hourly_bars() -> None:
    start = int(utc(2026, 9, 28))
    closes = [1.1 + 0.001 * (hour % 24) for hour in range(24 * 5)]
    bars = bars_from_closes(closes, "H1", start=start, wick=0.0)
    ranges = {item.session: item for item in session_ranges(bars, date(2026, 9, 29))}
    # London summer session: 07:00 to 16:00 UTC, the 07:00 bar opens at the 06:00 close.
    assert round(ranges[Session.LONDON].low, 6) == 1.106
    assert round(ranges[Session.LONDON].high, 6) == 1.115
    stats = {item.session: item for item in session_stats(bars, utc(2026, 10, 2, 23))}
    assert stats[Session.LONDON].days == 4
    assert stats[Session.LONDON].today is not None
    assert round(stats[Session.LONDON].average_range, 6) == 0.009
