"""The analysis loop (spec C2, C3, D3.3): thread `market-analysis`, closed-bar driven.

Every 2 seconds one cheap MT5 request reads each watched symbol's price and newest closed M5
bar. Only when a new M5 bar has closed is a symbol analysed again: its bars on every
timeframe are brought up to date and `analyze_symbol` builds a fresh card. Correlation and
currency strength follow every new H1 bar. Listeners receive a `MarketSnapshot` (plain data,
safe to hand to the UI thread).
"""

from __future__ import annotations

import contextlib
import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from app.analysis.bars import ANALYSIS_TIMEFRAMES
from app.analysis.correlation import CorrelationMatrix, correlation_matrix
from app.analysis.currency_strength import STRENGTH_PAIRS, CurrencyStrength, currency_strength
from app.analysis.symbol import SymbolAnalysis, Tick, analyze_symbol
from app.calendar.models import CalendarEvent
from app.core.clock import HOUR, BrokerClock
from app.mt5.errors import MT5Error
from app.mt5.market_data import MarketData
from app.mt5.models import Quote, SymbolSpec

POLL_SECONDS = 2.0
CALENDAR_SECONDS = 180.0
THREAD_NAME = "market-analysis"

Log = Callable[[str, str], None]
Listener = Callable[["MarketSnapshot"], None]


@dataclass(frozen=True)
class MarketSnapshot:
    state: str  # waiting, running, error
    message: str
    analyses: Mapping[str, SymbolAnalysis] = field(default_factory=dict)
    quotes: Mapping[str, Quote] = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    correlation: CorrelationMatrix | None = None
    strength: tuple[CurrencyStrength, ...] = ()
    events: tuple[CalendarEvent, ...] = ()
    clock_text: str = ""
    updated_at: float = 0.0


def _quiet(level: str, message: str) -> None:
    return None


class MarketWatch:
    def __init__(
        self,
        market: MarketData,
        *,
        symbols: Callable[[], Sequence[str]],
        connected: Callable[[], bool],
        events: Callable[[float], Sequence[CalendarEvent]] | None = None,
        refresh_calendar: Callable[[], None] | None = None,
        load_clock: Callable[[], BrokerClock | None] | None = None,
        save_clock: Callable[[BrokerClock], None] | None = None,
        log: Log = _quiet,
        heartbeat: Callable[[], None] | None = None,
        utc_now: Callable[[], float] = time.time,
        interval: float = POLL_SECONDS,
        calendar_interval: float = CALENDAR_SECONDS,
    ) -> None:
        self.market = market
        self._symbols = symbols
        self._connected = connected
        self._events = events or (lambda now: ())
        self._refresh_calendar = refresh_calendar
        self._load_clock = load_clock
        self._save_clock = save_clock
        self._log = log
        self._heartbeat = heartbeat
        self._now = utc_now
        self._interval = interval
        self._calendar_interval = calendar_interval
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._dirty = threading.Event()
        self._was_connected = False
        self._watched: tuple[str, ...] = ()
        self._mapping: dict[str, str] = {}
        self._specs: dict[str, SymbolSpec] = {}
        self._pairs: dict[str, str] = {}
        self._last_bar: dict[str, int | None] = {}
        self._analyses: dict[str, SymbolAnalysis] = {}
        self._quotes: dict[str, Quote] = {}
        self._missing: tuple[str, ...] = ()
        self._correlation: CorrelationMatrix | None = None
        self._strength: tuple[CurrencyStrength, ...] = ()
        self._strength_hour: int | None = None
        self._calendar_at: float | None = None
        self._snapshot = MarketSnapshot("waiting", "Waiting for the MT5 connection")

    # Public API --------------------------------------------------------------------------
    @property
    def clock(self) -> BrokerClock:
        return self.market.clock

    @property
    def snapshot(self) -> MarketSnapshot:
        with self._lock:
            return self._snapshot

    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def refresh(self) -> None:
        """Analyse every symbol again on the next cycle (watchlist or calendar changed)."""
        self._dirty.set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=THREAD_NAME, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)

    def cycle(self) -> MarketSnapshot:
        """One poll. Runs in the analysis thread (tests call it directly)."""
        self._beat()
        now = self._now()
        if not self._connected():
            if self._was_connected:
                self._log("INFO", "Market analysis paused: MT5 is not connected")
                self._reset()
            self._was_connected = False
            return self._publish(MarketSnapshot("waiting", "Waiting for the MT5 connection"))
        try:
            if not self._was_connected:
                self._on_connected()
            self._was_connected = True
            self._update_calendar(now)
            self._update_symbols(now)
            self._update_cross_market(now)
        except MT5Error as error:
            self._log("WARNING", f"Market analysis: MT5 request failed: {error}")
            return self._publish(self._build("error", f"MT5 request failed: {error.title}", now))
        return self._publish(self._build("running", self._status_text(), now))

    # Steps -------------------------------------------------------------------------------
    def _on_connected(self) -> None:
        self.market.forget()
        self._mapping.clear()
        self._specs.clear()
        self._pairs.clear()
        self._last_bar.clear()
        self._strength_hour = None
        if self._load_clock is not None:
            saved = self._load_clock()
            if saved is not None:
                self.market.clock = saved
                self._log("INFO", f"Broker time loaded: {saved.text()}")

    def _reset(self) -> None:
        self._analyses.clear()
        self._watched = ()

    def _update_calendar(self, now: float) -> None:
        if self._refresh_calendar is None:
            return
        if self._calendar_at is not None and now - self._calendar_at < self._calendar_interval:
            return
        self._calendar_at = now
        try:
            self._refresh_calendar()
        except Exception as error:
            self._log("WARNING", f"Calendar refresh failed: {type(error).__name__}: {error}")

    def _resolve(self) -> None:
        wanted = tuple(self._symbols())
        if wanted == self._watched and self._mapping:
            return
        self._watched = wanted
        found = self.market.resolve([*wanted, *STRENGTH_PAIRS])
        self._mapping = {name: found[name] or "" for name in wanted if found.get(name)}
        self._pairs = {pair: found[pair] or "" for pair in STRENGTH_PAIRS if found.get(pair)}
        self._missing = tuple(name for name in wanted if not found.get(name))
        for name in self._missing:
            self._log("WARNING", f"{name} is not offered by this broker; add it in Market Watch")
        for broker in self._mapping.values():
            spec = self.market.spec(broker)
            if spec is not None:
                self._specs[broker] = spec
        self._analyses = {name: item for name, item in self._analyses.items() if name in wanted}
        self._last_bar.clear()
        self._strength_hour = None
        self._log("INFO", f"Watching {', '.join(f'{k}={v}' for k, v in self._mapping.items())}")

    def _update_symbols(self, now: float) -> None:
        self._resolve()
        if not self._mapping:
            return
        force = self._dirty.is_set()
        self._dirty.clear()
        poll = self.market.poll(list(self._mapping.values()))
        self._quotes = {b: q for b, q in poll.quotes.items() if q is not None and q.valid}
        for quote in self._quotes.values():
            self._observe(quote, now)
        events = list(self._events(now))
        for name, broker in self._mapping.items():
            newest = poll.newest_m5.get(broker)
            if not force and name in self._analyses and newest == self._last_bar.get(broker):
                continue
            for timeframe in ANALYSIS_TIMEFRAMES:
                self.market.update(broker, timeframe)
            self._last_bar[broker] = newest
            self._analyses[name] = self._analyze(name, broker, now, events)

    def _observe(self, quote: Quote, now: float) -> None:
        clock = self.market.clock
        was_measured = clock.measured
        change = clock.observe(quote.server_time, now)
        if change is not None:
            self._log("WARNING", change.text())
        if change is not None or (clock.measured and not was_measured):
            self._log("INFO", f"Broker time: {clock.text()}")
            if self._save_clock is not None:
                self._save_clock(clock)

    def _analyze(
        self,
        name: str,
        broker: str,
        now: float,
        events: list[CalendarEvent],
    ) -> SymbolAnalysis:
        spec = self._specs.get(broker)
        digits = spec.digits if spec is not None else 5
        point = spec.point if spec is not None and spec.point > 0 else 10.0**-digits
        quote = self._quotes.get(broker)
        tick = None
        if quote is not None:
            utc = float(self.market.clock.to_utc(quote.server_time))
            tick = Tick(quote.bid, quote.ask, utc, point, digits)
        bars = {tf: self.market.bars(broker, tf, name) for tf in ANALYSIS_TIMEFRAMES}
        analysis = analyze_symbol(
            name,
            bars,
            now=now,
            clock=self.market.clock,
            tick=tick,
            digits=digits,
            broker_symbol=broker,
            events=events,
        )
        if not analysis.quality.ok:
            self._log("WARNING", f"{name}: evaluation skipped, {analysis.quality.text()}")
        else:
            self._log("INFO", f"{analysis.card.headline}. Why: {analysis.card.reason}.")
        return analysis

    def _update_cross_market(self, now: float) -> None:
        server_hour = int((now + self.market.clock.offset_at(now) * HOUR) // HOUR)
        if self._strength_hour == server_hour or not self._mapping:
            return
        self._strength_hour = server_hour
        watched = set(self._mapping.values())
        for broker in self._pairs.values():
            if broker not in watched:
                self.market.update(broker, "H1")
        pairs = {pair: self.market.bars(broker, "H1", pair) for pair, broker in self._pairs.items()}
        self._strength = currency_strength(pairs)
        hourly = {name: self.market.bars(b, "H1", name) for name, b in self._mapping.items()}
        self._correlation = correlation_matrix(hourly)

    # Helpers -----------------------------------------------------------------------------
    def _status_text(self) -> str:
        count = len(self._analyses)
        skipped = [name for name, item in self._analyses.items() if not item.quality.ok]
        text = f"Analysing {count} symbol{'s' if count != 1 else ''} on closed bars"
        if skipped:
            text += f"; data problem on {', '.join(skipped)}"
        if self._missing:
            text += f"; not at this broker: {', '.join(self._missing)}"
        return text

    def _build(self, state: str, message: str, now: float) -> MarketSnapshot:
        names = {broker: name for name, broker in self._mapping.items()}
        events = tuple(event for event in self._events(now) if event.time >= now - 15 * 60)
        return MarketSnapshot(
            state=state,
            message=message,
            analyses=dict(self._analyses),
            quotes={names.get(broker, broker): quote for broker, quote in self._quotes.items()},
            missing=self._missing,
            correlation=self._correlation,
            strength=self._strength,
            events=events,
            clock_text=self.market.clock.text(),
            updated_at=now,
        )

    def _publish(self, snapshot: MarketSnapshot) -> MarketSnapshot:
        with self._lock:
            self._snapshot = snapshot
            listeners = list(self._listeners)
        for listener in listeners:
            with contextlib.suppress(Exception):
                listener(snapshot)
        return snapshot

    def _beat(self) -> None:
        if self._heartbeat is not None:
            with contextlib.suppress(Exception):
                self._heartbeat()

    def _run(self) -> None:
        while not self._stop.is_set():
            started = self._now()
            try:
                self.cycle()
            except Exception as error:
                self._log("ERROR", f"Market analysis cycle failed: {type(error).__name__}: {error}")
            spent = self._now() - started
            wait = self._interval - spent if math.isfinite(spent) else self._interval
            self._stop.wait(max(wait, 0.2))
