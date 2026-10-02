"""Phase 5 acceptance: the analysis cards of 3 symbols update on closed bars."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from app.calendar.models import CalendarEvent, Impact
from app.core.clock import BrokerClock, DstScheme
from app.engine.market_watch import MarketSnapshot, MarketWatch
from app.mt5.gateway import MT5Gateway
from app.mt5.market_data import MarketData
from tests.fakes.fake_mt5 import FakeMT5

START = datetime(2026, 10, 1, 10, 2, 30, tzinfo=UTC).timestamp()
SYMBOLS = ["EURUSD", "GBPUSD", "XAUUSD"]


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


@contextmanager
def watch(
    symbols: list[str] | None = None,
    **options: object,
) -> Iterator[tuple[MarketWatch, FakeMT5, Clock, list[tuple[str, str]]]]:
    clock = Clock()
    fake = FakeMT5(now=clock)
    fake.initialize()
    gateway = MT5Gateway(lambda: fake, idle_seconds=0.05)
    gateway.start()
    logs: list[tuple[str, str]] = []
    data = MarketData(gateway, BrokerClock.assumed(), utc_now=clock)
    names = symbols or SYMBOLS
    try:
        watcher = MarketWatch(
            data,
            symbols=lambda: names,
            connected=lambda: fake.broker_connected,
            log=lambda level, message: logs.append((level, message)),
            utc_now=clock,
            **options,  # type: ignore[arg-type]
        )
        yield watcher, fake, clock, logs
    finally:
        gateway.stop()


def test_three_cards_update_on_each_closed_bar() -> None:
    seen: list[MarketSnapshot] = []
    with watch() as (watcher, fake, clock, logs):
        watcher.add_listener(seen.append)
        first = watcher.cycle()
        assert first.state == "running"
        assert sorted(first.analyses) == sorted(SYMBOLS)
        times = {name: item.bar_time for name, item in first.analyses.items()}
        cards = {name: item.card.headline for name, item in first.analyses.items()}
        assert all(headline.startswith(f"{name}: ") for name, headline in cards.items())
        calls = len(fake.calls)
        clock.now += 30  # same bar: nothing is analysed again
        same = watcher.cycle()
        assert {name: item.bar_time for name, item in same.analyses.items()} == times
        assert all(same.analyses[name] is first.analyses[name] for name in SYMBOLS)
        assert len(fake.calls) - calls < 12
        clock.now += 300  # a new M5 bar closed for every symbol
        later = watcher.cycle()
        assert {name: item.bar_time - times[name] for name, item in later.analyses.items()} == {
            name: 300 for name in SYMBOLS
        }
        assert all(later.analyses[name] is not first.analyses[name] for name in SYMBOLS)
        assert len(seen) == 3 and fake.trading_calls == []
        assert sum(1 for level, _ in logs if level == "INFO" and "\u2192" in _) == 6


def test_cross_market_views_and_the_broker_clock() -> None:
    saved: list[BrokerClock] = []
    with watch(save_clock=saved.append) as (watcher, fake, clock, logs):
        watcher.cycle()
        snapshot = watcher.cycle()
        assert snapshot.correlation is not None
        assert snapshot.correlation.symbols == tuple(SYMBOLS)
        assert {item.currency for item in snapshot.strength} >= {"EUR", "USD", "GBP", "JPY"}
        assert watcher.clock.measured and watcher.clock.scheme is DstScheme.US
        assert len(saved) == 1
        assert "UTC+2/+3" in snapshot.clock_text
        assert set(snapshot.quotes) == set(SYMBOLS)


def test_missing_symbols_events_and_disconnects() -> None:
    cpi = CalendarEvent(int(START) + 1800, "USD", Impact.HIGH, "CPI m/m")
    refreshed: list[bool] = []
    with watch(
        ["EURUSD", "AUDUSD"],
        events=lambda now: [cpi],
        refresh_calendar=lambda: refreshed.append(True),
    ) as (watcher, fake, clock, logs):
        snapshot = watcher.cycle()
        assert snapshot.missing == ("AUDUSD",)
        assert "not at this broker: AUDUSD" in snapshot.message
        assert snapshot.analyses["EURUSD"].card.verdict.value == "wait"
        assert snapshot.events == (cpi,)
        assert refreshed == [True]
        watcher.cycle()
        assert refreshed == [True]  # every 3 minutes, not every cycle
        fake.broker_connected = False
        waiting = watcher.cycle()
        assert waiting.state == "waiting" and waiting.analyses == {}
        assert any("paused" in message for _, message in logs)


def test_a_saved_clock_is_used_after_connecting() -> None:
    stored = BrokerClock(2.0, DstScheme.US, measured=True)
    with watch(load_clock=lambda: stored) as (watcher, fake, clock, logs):
        watcher.cycle()
        assert watcher.clock is stored


def test_the_thread_starts_and_stops() -> None:
    with watch(interval=0.05) as (watcher, fake, clock, logs):
        watcher.start()
        watcher.stop()
        assert watcher.snapshot.state in ("running", "waiting")
