"""The history import trusts the measured broker clock over an old quote (0.50.5)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.clock import BrokerClock, DstScheme
from app.mt5.history_sync import import_clock

OCTOBER = datetime(2026, 10, 13, 7, 29, tzinfo=UTC).timestamp()


def test_the_measured_clock_wins_over_an_hour_old_quote() -> None:
    known = BrokerClock(2.0, DstScheme.US, measured=True)
    clock, offset = import_clock(2.0, known, OCTOBER)  # the old quote read UTC+2
    assert clock.scheme is DstScheme.US
    assert clock.winter_hours == 2.0
    assert offset == 3.0


def test_without_a_measured_clock_the_quote_decides() -> None:
    clock, offset = import_clock(3.0, None, OCTOBER)
    assert clock.scheme is DstScheme.US
    assert offset == 3.0
    assumed = BrokerClock.assumed()
    clock, offset = import_clock(3.0, assumed, OCTOBER)  # assumed, not measured
    assert clock.scheme is DstScheme.US
    assert offset == 3.0


def test_nothing_known_saves_server_time() -> None:
    clock, offset = import_clock(None, None, OCTOBER)
    assert clock.scheme is DstScheme.FIXED
    assert offset is None
