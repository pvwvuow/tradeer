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


def test_a_card_waits_while_mt5_still_loads_the_history() -> None:
    reason = "the newest M5 bar is 2 h older than the price"
    with watch() as (watcher, fake, clock, logs):
        # Right after a connect a real terminal answered with XAUUSD bars two hours old.
        fake.stale_history["XAUUSD.m"] = 2 * 3600
        first = watcher.cycle()
        assert sorted(first.analyses) == ["EURUSD", "GBPUSD"]
        assert first.loading == {"XAUUSD": reason}
        assert "MT5 is loading bars for XAUUSD" in first.message
        assert ("INFO", f"XAUUSD: waiting for MT5 to load the newest bars ({reason})") in logs
        clock.now += 2
        fake.stale_history.clear()
        second = watcher.cycle()
        assert sorted(second.analyses) == sorted(SYMBOLS) and second.loading == {}
        assert ("INFO", "XAUUSD: MT5 has loaded the newest bars after 2 s") in logs
        gold, euro = second.analyses["XAUUSD"], second.analyses["EURUSD"]
        assert gold.bar_time == euro.bar_time  # judged on the fresh bars
        assert euro is first.analyses["EURUSD"]  # the other cards were not redone


def test_a_warning_says_what_to_do_when_the_history_does_not_arrive() -> None:
    with watch(["XAUUSD"]) as (watcher, fake, clock, logs):
        fake.stale_history["XAUUSD.m"] = 3 * 3600
        watcher.cycle()
        clock.now += 59
        watcher.cycle()
        assert not [message for level, message in logs if level == "WARNING"]
        clock.now += 2
        watcher.cycle()
        snapshot = watcher.cycle()
        warnings = [message for level, message in logs if level == "WARNING"]
        assert len(warnings) == 1, warnings
        assert "XAUUSD: MT5 has not loaded the newest bars after 60 s" in warnings[0]
        assert "Open a XAUUSD.m chart in MT5" in warnings[0]
        assert snapshot.analyses == {} and "XAUUSD" in snapshot.loading


def test_a_closed_market_has_no_new_bars_to_wait_for() -> None:
    with watch(["EURUSD"]) as (watcher, fake, clock, logs):
        clock.now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC).timestamp()  # a Saturday
        fake.stale_history["EURUSD.m"] = 3 * 3600
        snapshot = watcher.cycle()
        assert snapshot.loading == {} and "EURUSD" in snapshot.analyses


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


class RecordingHook:
    def __init__(self) -> None:
        self.analysed: list[str] = []
        self.cycles = 0

    def on_analysis(self, analysis: object, **options: object) -> None:
        self.analysed.append(getattr(analysis, "symbol", ""))
        assert options["now"] == START and "clock" in options and "spread" in options

    def on_cycle(self, now: float | None = None) -> None:
        self.cycles += 1


def test_the_signal_hook_runs_after_each_new_analysis() -> None:
    hook = RecordingHook()
    with watch(signals=hook) as (watcher, fake, clock, logs):
        watcher.cycle()
        assert sorted(hook.analysed) == sorted(SYMBOLS)
        watcher.cycle()  # no new closed bar: strategies do not run again
        assert sorted(hook.analysed) == sorted(SYMBOLS)
        assert hook.cycles == 2


class SymbolsHook:
    def __init__(self) -> None:
        self.analysed: list[str] = []

    def on_analysis(self, analysis: object, **options: object) -> None:
        self.analysed.append(getattr(analysis, "symbol", ""))

    def on_cycle(self, now: float | None = None) -> None:
        return None


def test_the_cards_say_market_closed_when_the_week_ends() -> None:
    hook = SymbolsHook()
    with watch(["EURUSD"], signals=hook) as (watcher, fake, clock, logs):
        clock.now = datetime(2026, 10, 2, 20, 57, 30, tzinfo=UTC).timestamp()  # Friday
        first = watcher.cycle().analyses["EURUSD"]
        assert "New York session" in first.card.headline
        clock.now += 4 * 60  # 21:01:30 UTC: the market closed; no tick, so no new bar
        fake.stale_history["EURUSD.m"] = 4 * 60
        closed = watcher.cycle().analyses["EURUSD"]
        assert "Market closed (weekend)" in closed.card.headline
        assert closed.bar_time == first.bar_time
        assert hook.analysed == ["EURUSD"]  # strategies did not run again on the same bar
        clock.now += 30
        assert watcher.cycle().analyses["EURUSD"] is closed  # redone once, not every poll
        assert sum(1 for _, text in logs if "Market closed (weekend)" in text) == 1
