from datetime import UTC, date, datetime

import numpy as np

from app.core.clock import (
    BrokerClock,
    DstScheme,
    eu_dst_window,
    guess_scheme,
    measure_offset,
    us_dst_window,
)


def utc(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=UTC).timestamp())


def test_us_and_eu_summer_time_dates() -> None:
    assert us_dst_window(2024) == (utc(2024, 3, 10, 7), utc(2024, 11, 3, 6))
    assert us_dst_window(2026) == (utc(2026, 3, 8, 7), utc(2026, 11, 1, 6))
    assert us_dst_window(2005) == (utc(2005, 4, 3, 7), utc(2005, 10, 30, 6))
    assert eu_dst_window(2024) == (utc(2024, 3, 31, 1), utc(2024, 10, 27, 1))
    assert eu_dst_window(2026) == (utc(2026, 3, 29, 1), utc(2026, 10, 25, 1))


def test_measure_offset_needs_a_fresh_tick() -> None:
    now = utc(2026, 10, 1, 10)
    assert measure_offset(now + 3 * 3600 - 20, now) == 3.0
    assert measure_offset(now + 5.5 * 3600, now) == 5.5
    assert measure_offset(now + 3 * 3600 - 2000, now) is None


def test_guess_scheme_prefers_new_york_close_time() -> None:
    summer, winter = utc(2026, 7, 1), utc(2026, 1, 15)
    assert guess_scheme(3.0, summer) == (DstScheme.US, 2.0)
    assert guess_scheme(2.0, winter) == (DstScheme.US, 2.0)
    assert guess_scheme(1.0, winter) == (DstScheme.EU, 1.0)
    assert guess_scheme(0.0, summer) == (DstScheme.FIXED, 0.0)


def test_server_time_converts_to_utc_in_summer_and_winter() -> None:
    clock = BrokerClock(2.0, DstScheme.US, measured=True)
    summer, winter = utc(2024, 9, 10, 14), utc(2024, 1, 10, 14)
    assert clock.offset_at(summer) == 3.0
    assert clock.offset_at(winter) == 2.0
    assert clock.to_utc(summer + 3 * 3600) == summer
    assert clock.to_utc(winter + 2 * 3600) == winter
    servers = np.array([summer + 3 * 3600, winter + 2 * 3600], dtype=np.int64)
    assert clock.to_utc_array(servers).tolist() == [summer, winter]


def test_the_trading_day_starts_at_new_york_five_pm() -> None:
    clock = BrokerClock(2.0, DstScheme.US, measured=True)
    assert clock.broker_date(utc(2024, 9, 10, 20, 59)) == date(2024, 9, 10)
    assert clock.broker_date(utc(2024, 9, 10, 21, 30)) == date(2024, 9, 11)
    assert clock.day_start_utc(utc(2024, 9, 10, 22, 30)) == utc(2024, 9, 10, 21)


def test_the_clock_changes_after_two_agreeing_ticks() -> None:
    clock = BrokerClock.assumed()
    now = utc(2026, 10, 1, 10)
    assert clock.observe(now + 3 * 3600, now) is None
    assert not clock.measured
    assert clock.observe(now + 3 * 3600 + 2, now + 2) is None
    assert clock.measured and clock.scheme is DstScheme.US
    assert clock.changes == []


def test_a_summer_time_switch_is_not_a_time_jump_but_a_shift_is() -> None:
    clock = BrokerClock(2.0, DstScheme.US, measured=True)
    winter = utc(2026, 12, 1, 10)
    assert clock.observe(winter + 2 * 3600, winter) is None
    assert clock.observe(winter + 2 * 3600, winter + 1) is None
    assert clock.changes == []
    clock.observe(winter, winter + 2)
    change = clock.observe(winter + 3, winter + 3)
    assert change is not None and (change.old_hours, change.new_hours) == (2.0, 0.0)
    assert clock.scheme is DstScheme.FIXED
    assert "jumped" in change.text()


def test_the_clock_round_trips_through_a_dict() -> None:
    clock = BrokerClock(2.0, DstScheme.US, measured=True)
    again = BrokerClock.from_dict(clock.to_dict())
    assert (again.winter_hours, again.scheme, again.measured) == (2.0, DstScheme.US, True)
    assert BrokerClock.from_dict({"scheme": "nonsense"}).scheme is DstScheme.US
    assert "assumed" in BrokerClock.assumed().text()
