"""Broker server time (spec C2): its offset to UTC, daylight saving and the trading day.

MT5 stamps ticks and bars with the broker's clock written as if it were UTC. Most brokers run
on "New York close" time: UTC+2 in winter and UTC+3 while the USA is on daylight saving, so a
broker day starts at 17:00 in New York. The app stores UTC everywhere and converts with this
clock. The daylight-saving rules are written out here because Windows has no tz database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any

import numpy as np
import numpy.typing as npt

HOUR = 3600
DAY = 86_400
OFFSET_TOLERANCE_SECONDS = 180.0
CONFIRMATIONS = 2
# Two agreeing ticks must be this far apart, with the price clock moving like real time, so a
# stale price (market closed) can never confirm an offset.
CONFIRM_GAP_SECONDS = 600.0
# The half-hour offsets real time zones use (whole hours from -12 to +14 are all used).
HALF_HOUR_ZONES = frozenset({-9.5, -3.5, 3.5, 4.5, 5.5, 6.5, 9.5, 10.5})


class DstScheme(StrEnum):
    FIXED = "fixed"
    US = "us"
    EU = "eu"


def _sunday_on_or_after(day: date) -> date:
    return day + timedelta(days=(6 - day.weekday()) % 7)


def _last_sunday(year: int, month: int) -> date:
    first_next = date(year + (month == 12), month % 12 + 1, 1)
    last = first_next - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def _epoch(day: date, hour: int) -> int:
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=UTC).timestamp())


def us_dst_window(year: int) -> tuple[int, int]:
    """US daylight saving in UTC seconds: 2:00 local time, rules of 2007 and of 1987-2006."""
    if year >= 2007:
        start = _sunday_on_or_after(date(year, 3, 8))
        end = _sunday_on_or_after(date(year, 11, 1))
    else:
        start = _sunday_on_or_after(date(year, 4, 1))
        end = _last_sunday(year, 10)
    return _epoch(start, 7), _epoch(end, 6)


def eu_dst_window(year: int) -> tuple[int, int]:
    """EU (and UK) summer time in UTC seconds: last Sunday of March to last Sunday of October."""
    return _epoch(_last_sunday(year, 3), 1), _epoch(_last_sunday(year, 10), 1)


def _year(utc_seconds: float) -> int:
    return datetime.fromtimestamp(utc_seconds, UTC).year


def us_dst_active(utc_seconds: float) -> bool:
    start, end = us_dst_window(_year(utc_seconds))
    return start <= utc_seconds < end


def eu_dst_active(utc_seconds: float) -> bool:
    start, end = eu_dst_window(_year(utc_seconds))
    return start <= utc_seconds < end


def dst_active(scheme: DstScheme, utc_seconds: float) -> bool:
    if scheme is DstScheme.US:
        return us_dst_active(utc_seconds)
    if scheme is DstScheme.EU:
        return eu_dst_active(utc_seconds)
    return False


def measure_offset(
    server_time: float,
    utc_now: float,
    tolerance_seconds: float = OFFSET_TOLERANCE_SECONDS,
) -> float | None:
    """The offset in hours (half-hour steps) shown by a fresh tick, or None if it is stale."""
    difference = server_time - utc_now
    offset = round(difference / 1800.0) / 2.0
    if abs(difference - offset * HOUR) > tolerance_seconds or not real_offset(offset):
        return None
    return offset


def real_offset(hours: float) -> bool:
    """An offset some real time zone uses: a stale price gives impossible ones like -11.5."""
    if hours == int(hours):
        return -12 <= hours <= 14
    return hours in HALF_HOUR_ZONES


def fx_weekend(utc_seconds: float) -> bool:
    """FX is closed from Friday 17:00 to Sunday 17:00 New York time: prices are stale."""
    offset = -4 if us_dst_active(utc_seconds) else -5
    local = datetime.fromtimestamp(utc_seconds + offset * HOUR, UTC)
    weekday, hour = local.weekday(), local.hour
    return weekday == 5 or (weekday == 4 and hour >= 17) or (weekday == 6 and hour < 17)


def guess_scheme(offset_hours: float, utc_seconds: float) -> tuple[DstScheme, float]:
    """The most likely scheme and winter offset for an offset measured at `utc_seconds`.

    UTC+2/+3 on US dates is "New York close" time, used by most brokers. UTC+1/+2 on EU dates
    is Central European time. Anything else is taken as a fixed offset.
    """
    us_summer = us_dst_active(utc_seconds)
    if offset_hours == (3.0 if us_summer else 2.0):
        return DstScheme.US, 2.0
    eu_summer = eu_dst_active(utc_seconds)
    if offset_hours == (2.0 if eu_summer else 1.0):
        return DstScheme.EU, 1.0
    return DstScheme.FIXED, offset_hours


@dataclass(frozen=True)
class OffsetChange:
    """The broker clock moved by itself, outside a daylight-saving date: a time jump."""

    utc_time: int
    old_hours: float
    new_hours: float

    def text(self) -> str:
        moment = datetime.fromtimestamp(self.utc_time, UTC).strftime("%Y-%m-%d %H:%M")
        return (
            f"Broker time jumped from UTC{self.old_hours:+g} to UTC{self.new_hours:+g} "
            f"at {moment} UTC"
        )


@dataclass(frozen=True)
class _Pending:
    offset: float
    count: int
    server: float
    utc: float


class BrokerClock:
    """Converts broker server time to UTC. Not thread-safe: one owner updates it."""

    def __init__(
        self,
        winter_hours: float | None = None,
        scheme: DstScheme = DstScheme.FIXED,
        *,
        measured: bool = False,
    ) -> None:
        self.winter_hours = winter_hours
        self.scheme = scheme
        self.measured = measured and winter_hours is not None
        self.changes: list[OffsetChange] = []
        self._pending: _Pending | None = None

    @classmethod
    def assumed(cls) -> BrokerClock:
        """Before the first fresh tick: assume New York close time (UTC+2/+3)."""
        return cls(2.0, DstScheme.US)

    @property
    def known(self) -> bool:
        return self.winter_hours is not None

    def offset_at(self, utc_seconds: float) -> float:
        base = self.winter_hours if self.winter_hours is not None else 0.0
        return base + (1.0 if dst_active(self.scheme, utc_seconds) else 0.0)

    def observe(self, server_time: float, utc_now: float) -> OffsetChange | None:
        """Feed a tick. The clock changes after two ticks that agree, at least ten minutes
        apart, whose server time moved like real time (a stale price never moves)."""
        offset = measure_offset(server_time, utc_now)
        if offset is None or fx_weekend(utc_now):
            return None
        if self.measured and offset == self.offset_at(utc_now):
            self._pending = None
            return None
        pending = self._pending
        if pending is None or pending.offset != offset:
            self._pending = _Pending(offset, 1, server_time, utc_now)
            return None
        elapsed = utc_now - pending.utc
        if elapsed < CONFIRM_GAP_SECONDS:
            return None
        if abs((server_time - pending.server) - elapsed) > OFFSET_TOLERANCE_SECONDS:
            self._pending = None  # the price did not move with the clock: it is stale
            return None
        if pending.count + 1 < CONFIRMATIONS:
            self._pending = _Pending(offset, pending.count + 1, server_time, utc_now)
            return None
        self._pending = None
        old = self.offset_at(utc_now) if self.measured else None
        self.scheme, self.winter_hours = guess_scheme(offset, utc_now)
        self.measured = True
        if old is None:
            return None
        change = OffsetChange(int(utc_now), old, offset)
        self.changes.append(change)
        return change

    def to_utc(self, server_seconds: float) -> int:
        base = self.winter_hours if self.winter_hours is not None else 0.0
        guess = server_seconds - base * HOUR
        utc = server_seconds - self.offset_at(guess) * HOUR
        return int(server_seconds - self.offset_at(utc) * HOUR)

    def to_utc_array(self, server_seconds: npt.NDArray[np.int64]) -> npt.NDArray[np.int64]:
        values = np.asarray(server_seconds, dtype=np.int64)
        base = int(round((self.winter_hours or 0.0) * HOUR))
        utc = values - base
        if self.scheme is DstScheme.FIXED or not len(values):
            return utc
        window = us_dst_window if self.scheme is DstScheme.US else eu_dst_window
        years = utc.astype("datetime64[s]").astype("datetime64[Y]").astype(np.int64) + 1970
        summer = np.zeros(len(values), dtype=bool)
        for year in np.unique(years):
            start, end = window(int(year))
            # Server time in summer is one hour later than in winter for the same UTC moment.
            shifted = utc - HOUR
            summer |= (years == year) & (shifted >= start) & (shifted < end)
        return np.where(summer, utc - HOUR, utc).astype(np.int64)

    def broker_date(self, utc_seconds: float) -> date:
        """The trading day: the broker's calendar date (the day starts at New York 17:00)."""
        server = utc_seconds + self.offset_at(utc_seconds) * HOUR
        return datetime.fromtimestamp(server, UTC).date()

    def day_start_utc(self, utc_seconds: float) -> int:
        """UTC time at which the current broker day started."""
        day = self.broker_date(utc_seconds)
        midnight = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
        return self.to_utc(midnight)

    def text(self) -> str:
        if self.winter_hours is None:
            return "broker time unknown"
        if self.scheme is DstScheme.FIXED:
            label = f"UTC{self.winter_hours:+g} all year"
        else:
            rules = "US" if self.scheme is DstScheme.US else "EU"
            label = f"UTC{self.winter_hours:+g}/{self.winter_hours + 1:+g} ({rules} summer time)"
        return label if self.measured else f"{label}, assumed until the first fresh price"

    def to_dict(self) -> dict[str, Any]:
        return {
            "winter_hours": self.winter_hours,
            "scheme": self.scheme.value,
            "measured": self.measured,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> BrokerClock:
        try:
            hours = raw.get("winter_hours")
            winter = float(hours) if hours is not None else None
            scheme = DstScheme(str(raw.get("scheme", DstScheme.FIXED.value)))
        except (TypeError, ValueError):
            return cls.assumed()
        if winter is not None and not real_offset(winter):
            return cls.assumed()  # measured from a stale price by an older version
        return cls(winter, scheme, measured=bool(raw.get("measured", False)))
