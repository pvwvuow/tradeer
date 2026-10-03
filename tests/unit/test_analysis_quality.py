from datetime import UTC, datetime

import numpy as np

from app.analysis.bars import Bars
from app.analysis.quality import (
    Issue,
    Severity,
    check_bars,
    check_clock,
    check_tick,
    quality_report,
)
from app.core.clock import OffsetChange
from tests.unit.analysis_helpers import bars_from_closes, trending


def utc(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=UTC).timestamp())


def m5_until(end: int, count: int = 120) -> Bars:
    """M5 bars whose newest one closed at `end`."""
    return bars_from_closes(trending(count, 0.0), "M5", start=end - 300 * count)


def codes(issues: list[Issue]) -> list[str]:
    return [issue.code for issue in issues]


def test_clean_bars_have_no_issues() -> None:
    now = utc(2026, 10, 1, 10, 2)
    assert check_bars(m5_until(utc(2026, 10, 1, 10)), now) == []


def test_zero_volume_missing_bars_and_spikes() -> None:
    now = utc(2026, 10, 1, 10, 2)
    bars = m5_until(utc(2026, 10, 1, 10))
    bars.volume[-3:] = 0
    keep = np.r_[np.arange(0, 60), np.arange(80, len(bars))]
    gappy = Bars.build(
        "EURUSD",
        "M5",
        time=bars.time[keep],
        open=bars.open[keep],
        high=bars.high[keep],
        low=bars.low[keep],
        close=bars.close[keep],
        volume=bars.volume[keep],
    )
    found = check_bars(gappy, now)
    assert codes(found) == ["missing_bars", "zero_volume"]
    assert all(issue.severity is Severity.WARNING for issue in found)
    assert "20 bars missing" in found[0].message
    bars.high[-1] = bars.close[-1] + 0.05
    spike = check_bars(bars, now)
    assert "spike" in codes(spike)
    assert next(issue for issue in spike if issue.code == "spike").severity is Severity.ERROR


def test_a_weekend_gap_is_normal() -> None:
    friday_close = utc(2026, 10, 2, 21)
    before = bars_from_closes(trending(60, 0.0), "M5", start=friday_close - 300 * 60)
    after_start = utc(2026, 10, 4, 21, 5)
    after = bars_from_closes(trending(30, 0.0, seed=2), "M5", start=after_start)
    joined = Bars.build(
        "EURUSD",
        "M5",
        time=np.r_[before.time, after.time],
        open=np.r_[before.open, after.open],
        high=np.r_[before.high, after.high],
        low=np.r_[before.low, after.low],
        close=np.r_[before.close, after.close],
    )
    assert check_bars(joined, after_start + 300 * 30 + 60) == []


def test_stale_prices_and_closed_symbols() -> None:
    now = utc(2026, 10, 1, 10, 2)
    assert check_tick("EURUSD", now - 5, now, now - 120) == []
    stale = check_tick("EURUSD", now - 900, now, now - 120)
    assert codes(stale) == ["stale_tick"] and stale[0].severity is Severity.ERROR
    closed = check_tick("XAUUSD", now - 900, now, now - 1500)
    assert codes(closed) == ["symbol_closed"] and closed[0].severity is Severity.WARNING
    assert codes(check_tick("EURUSD", None, now)) == ["no_tick"]
    assert check_tick("EURUSD", None, utc(2026, 10, 3, 12)) == []  # weekend


def test_a_time_jump_blocks_for_an_hour_then_warns() -> None:
    change = OffsetChange(utc(2026, 10, 1, 9), 3.0, 2.0)
    assert check_clock([change], change.utc_time + 60)[0].severity is Severity.ERROR
    assert check_clock([change], change.utc_time + 7200)[0].severity is Severity.WARNING
    assert check_clock([change], change.utc_time + 2 * 86_400) == []


def test_the_report_is_ok_only_without_errors() -> None:
    now = utc(2026, 10, 1, 10, 2)
    bars = m5_until(utc(2026, 10, 1, 10))
    good = quality_report([bars], symbol="EURUSD", tick_utc=now - 1, now=now)
    assert good.ok and good.text() == "data OK"
    assumed = quality_report(
        [bars],
        symbol="EURUSD",
        tick_utc=now - 1,
        now=now,
        clock_measured=False,
    )
    assert assumed.ok and codes(assumed.warnings) == ["clock_assumed"]
    bad = quality_report([Bars.empty("EURUSD", "M5")], symbol="EURUSD", tick_utc=now, now=now)
    assert not bad.ok and bad.errors[0].code == "no_bars"
