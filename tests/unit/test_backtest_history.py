"""Backtest history from MT5 (spec C8): chunked `copy_rates_range`, server time to UTC, enough
warm-up bars, a disk cache, and an honest note when MT5 has too little history."""

import tempfile
from pathlib import Path

import numpy as np

from app.backtest.history import CHUNK_BARS, load_history, merge, warmup_seconds
from app.core.clock import BrokerClock, DstScheme
from app.mt5.gateway import MT5Gateway
from app.mt5.market_data import BAR_COUNTS
from tests.unit.risk_helpers import connected
from tests.unit.signal_helpers import MORNING

DAY = 86_400
CLOCK = BrokerClock(2.0, DstScheme.FIXED, measured=True)  # the fake's server runs at UTC+2


def gateway_for(fake):  # type: ignore[no-untyped-def]
    gateway = MT5Gateway(lambda: fake, idle_seconds=0.05)
    gateway.start()
    return gateway


def test_bars_are_utc_with_the_warm_up_and_cached() -> None:
    fake = connected()
    fake.server_offset_hours = 2
    start, end = float(MORNING - 10 * DAY), float(MORNING - DAY)
    gateway = gateway_for(fake)
    try:
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            history, report = load_history(
                gateway,
                "EURUSD",
                "EURUSD.m",
                start,
                end,
                CLOCK,
                cache=cache,
            )
            m5 = history.bars["M5"]
            assert np.all(m5.server_time - m5.time == 2 * 3600)
            assert m5.time[-1] + 300 <= end and m5.time[0] <= start - BAR_COUNTS["M5"] * 300
            daily = history.bars["D1"]
            assert int(np.count_nonzero(daily.time + DAY <= start)) >= BAR_COUNTS["D1"]
            assert not report.notes and history.spec.name == "EURUSD.m"
            assert sorted(path.name for path in cache.iterdir())[0] == "EURUSD.m_D1.npz"
            calls = fake.calls.count("copy_rates_range")
            assert calls >= 5
            again, _ = load_history(gateway, "EURUSD", "EURUSD.m", start, end, CLOCK, cache=cache)
            assert fake.calls.count("copy_rates_range") == calls  # all from the cache
            assert np.array_equal(again.bars["H1"].close, history.bars["H1"].close)
    finally:
        gateway.stop()


def test_big_requests_are_split_in_chunks() -> None:
    fake = connected()
    fake.rates_request_limit = CHUNK_BARS
    gateway = gateway_for(fake)
    try:
        start, end = float(MORNING - 5 * DAY), float(MORNING)
        history, _ = load_history(gateway, "EURUSD", "EURUSD.m", start, end, CLOCK)
        span = warmup_seconds("M5") + 5 * DAY
        assert len(history.bars["M5"]) > span // 300 * 0.9
    finally:
        gateway.stop()


def test_short_history_is_reported() -> None:
    fake = connected()
    fake.history_start = int(MORNING - 60 * DAY)
    gateway = gateway_for(fake)
    notes: list[str] = []
    try:
        _, report = load_history(
            gateway,
            "EURUSD",
            "EURUSD.m",
            float(MORNING - 10 * DAY),
            float(MORNING),
            CLOCK,
            note=notes.append,
        )
    finally:
        gateway.stop()
    assert any(text.startswith("D1: MT5 has bars from") for text in report.notes)
    assert any("Max bars in chart" in text for text in notes)


def test_merge_sorts_and_keeps_the_newest_copy() -> None:
    def part(times, close):  # type: ignore[no-untyped-def]
        size = len(times)
        return {
            "time": np.asarray(times, dtype=np.int64),
            "open": np.full(size, close),
            "high": np.full(size, close),
            "low": np.full(size, close),
            "close": np.full(size, close),
            "tick_volume": np.ones(size, dtype=np.int64),
            "spread": np.ones(size, dtype=np.int64),
        }

    found = merge([part([300, 0], 1.0), part([300, 600], 2.0)])
    assert found["time"].tolist() == [0, 300, 600]
    assert found["close"].tolist() == [1.0, 2.0, 2.0]
