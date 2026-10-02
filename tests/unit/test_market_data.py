from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import numpy as np
import pytest

from app.core.clock import BrokerClock
from app.mt5.errors import MT5Error
from app.mt5.gateway import MT5Gateway
from app.mt5.market_data import MarketData, merge_columns, read_closed_rates
from tests.fakes.fake_mt5 import FakeMT5

START = datetime(2026, 10, 1, 10, 2, 30, tzinfo=UTC).timestamp()


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


@contextmanager
def market(counts: dict[str, int] | None = None) -> Iterator[tuple[MarketData, FakeMT5, Clock]]:
    clock = Clock()
    fake = FakeMT5(now=clock)
    fake.initialize()
    gateway = MT5Gateway(lambda: fake, idle_seconds=0.05)
    gateway.start()
    try:
        yield MarketData(gateway, BrokerClock.assumed(), counts=counts, utc_now=clock), fake, clock
    finally:
        gateway.stop()


def test_only_closed_bars_are_read_and_times_become_utc() -> None:
    with market() as (data, fake, clock):
        assert data.update("EURUSD.m", "M5") == 600
        bars = data.bars("EURUSD.m", "M5", "EURUSD")
        assert bars.symbol == "EURUSD" and len(bars) == 600
        # The newest closed M5 bar opened at 09:55 UTC (12:55 server time at UTC+3).
        assert int(bars.time[-1]) == int(START) // 300 * 300 - 300
        assert int(bars.server_time[-1]) - int(bars.time[-1]) == 3 * 3600
        assert np.all(np.diff(bars.time) == 300)
        assert "copy_rates_from_pos" in fake.calls and fake.trading_calls == []


def test_later_updates_only_append_the_new_bars() -> None:
    with market() as (data, fake, clock):
        data.update("EURUSD.m", "M5")
        before = data.bars("EURUSD.m", "M5")
        assert data.update("EURUSD.m", "M5") == 0
        clock.now += 600
        assert data.update("EURUSD.m", "M5") == 2
        after = data.bars("EURUSD.m", "M5")
        assert len(after) == 600
        assert int(after.time[-1]) - int(before.time[-1]) == 600
        # Old bars are unchanged: the same time always has the same price.
        shared = np.isin(before.time, after.time)
        assert np.array_equal(before.close[shared], after.close[np.isin(after.time, before.time)])


def test_symbols_are_resolved_and_polled_in_one_request() -> None:
    with market() as (data, fake, clock):
        mapping = data.resolve(["EURUSD", "XAUUSD", "AUDUSD"])
        assert mapping == {"EURUSD": "EURUSD.m", "XAUUSD": "XAUUSD.m", "AUDUSD": None}
        spec = data.spec("XAUUSD.m")
        assert spec is not None and spec.digits == 2
        poll = data.poll(["EURUSD.m", "XAUUSD.m"])
        quote = poll.quotes["XAUUSD.m"]
        assert quote is not None and quote.valid and quote.digits == 2
        assert poll.newest_m5["EURUSD.m"] == int(START + 3 * 3600) // 300 * 300 - 300
        data.forget()
        assert len(data.bars("EURUSD.m", "M5")) == 0


def test_merging_replaces_overlapping_bars() -> None:
    old = {name: np.arange(5, dtype=np.int64) for name in ("time", "open", "high", "low", "close")}
    old.update(tick_volume=np.zeros(5, dtype=np.int64), spread=np.zeros(5, dtype=np.int64))
    fresh = {name: values[3:] + 10 for name, values in old.items()}
    fresh["time"] = np.array([3, 4], dtype=np.int64)
    merged = merge_columns(old, fresh, 4)
    assert merged["time"].tolist() == [1, 2, 3, 4]
    assert merged["close"].tolist() == [1, 2, 13, 14]


def test_a_failed_request_raises_a_clear_error() -> None:
    fake = FakeMT5()
    fake.initialize()
    with pytest.raises(MT5Error):
        read_closed_rates(fake, "MISSING", "M5", 10)
