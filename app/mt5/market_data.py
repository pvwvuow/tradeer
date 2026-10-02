"""Market data for the analysis (spec C2): closed bars only, cached per symbol and timeframe.

Bars come from `copy_rates_from_pos(symbol, timeframe, 1, n)`: position 0 is the bar that is
still forming and is never used. After the first download only the new bars are read and
appended. Bars keep the broker's server time; `bars()` converts it to UTC with the broker
clock every time, so a corrected clock fixes the whole cache at once.

Right after a connect MT5 can answer with the bars it has on disk while it still downloads
the rest (seen on a real account: XAUUSD bars two hours old, fixed two seconds later). The poll
therefore also reads the open time of the forming M5 bar, only to compare it with the live
price (ADR 49). Its prices are never used.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from app.analysis.bars import TF_SECONDS, Bars
from app.core.clock import BrokerClock
from app.mt5 import api
from app.mt5.api import TIMEFRAMES, MT5Api
from app.mt5.errors import error_from_last
from app.mt5.gateway import MT5Gateway
from app.mt5.models import Quote, SymbolSpec
from app.mt5.symbols import resolve_symbol

BAR_COUNTS: dict[str, int] = {"M5": 600, "M15": 600, "H1": 600, "H4": 400, "D1": 300}
REQUEST_TIMEOUT_SECONDS = 30.0
M5_SECONDS = 300
# A live price always sits in the forming M5 bar. More than two bars between them means MT5 is
# still loading the history (one bar of slack covers a price that changed only on the ask).
HISTORY_SLACK_SECONDS = 2 * M5_SECONDS
COLUMNS = ("time", "open", "high", "low", "close", "tick_volume", "spread")
INTEGER_COLUMNS = ("time", "tick_volume", "spread")

Columns = dict[str, npt.NDArray[Any]]


def _empty_columns() -> Columns:
    return {
        name: np.zeros(0, dtype=np.int64 if name in INTEGER_COLUMNS else np.float64)
        for name in COLUMNS
    }


def _to_columns(rates: Any) -> Columns:
    names = set(getattr(getattr(rates, "dtype", None), "names", None) or ())
    if not len(rates) or not {"time", "open", "high", "low", "close"} <= names:
        return _empty_columns()
    found: Columns = {}
    for name in COLUMNS:
        kind = np.int64 if name in INTEGER_COLUMNS else np.float64
        if name in names:
            found[name] = np.array(rates[name], dtype=kind)
        else:
            found[name] = np.zeros(len(rates), dtype=kind)
    return found


def read_closed_rates(mt5: MT5Api, symbol: str, timeframe: str, count: int) -> Columns:
    """Runs in the gateway thread. The newest `count` closed bars, oldest first."""
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAMES[timeframe], 1, count)
    if rates is None:
        code, detail = mt5.last_error()
        if code != api.RES_S_OK:
            raise error_from_last((code, detail))
        return _empty_columns()
    return _to_columns(rates)


def duration_text(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


@dataclass(frozen=True)
class PollResult:
    """One cheap request per cycle: each symbol's tick and newest closed M5 bar.

    `forming_m5` is the open time of the M5 bar that is still forming. It is only compared with
    the live price; its prices are never read.
    """

    quotes: Mapping[str, Quote | None]
    newest_m5: Mapping[str, int | None]
    forming_m5: Mapping[str, int | None] = field(default_factory=dict)

    def loading(self, symbol: str) -> str | None:
        """Why MT5 is still loading this symbol's bars, or None when they reach the price."""
        quote = self.quotes.get(symbol)
        if quote is None or not quote.valid or quote.server_time <= 0:
            return None  # no live price to compare with; the data checks report it
        if symbol not in self.forming_m5:
            return None  # not polled
        forming = self.forming_m5[symbol]
        if forming is None:
            return "no M5 bars yet"
        expected = quote.server_time - quote.server_time % M5_SECONDS
        behind = expected - forming
        if behind <= HISTORY_SLACK_SECONDS:
            return None
        return f"the newest M5 bar is {duration_text(behind)} older than the price"


def read_poll(mt5: MT5Api, symbols: Sequence[str], digits: Mapping[str, int]) -> PollResult:
    quotes: dict[str, Quote | None] = {}
    newest: dict[str, int | None] = {}
    forming: dict[str, int | None] = {}
    for name in symbols:
        tick = mt5.symbol_info_tick(name)
        quotes[name] = Quote.from_mt5(name, tick, digits.get(name, 5)) if tick is not None else None
        # Positions 1 and 0, oldest first: the newest closed bar, then the forming bar.
        rates = mt5.copy_rates_from_pos(name, TIMEFRAMES["M5"], 0, 2)
        times = [int(value) for value in rates["time"]] if rates is not None else []
        newest[name] = times[-2] if len(times) >= 2 else None
        forming[name] = times[-1] if times else None
    return PollResult(quotes, newest, forming)


def read_symbol(mt5: MT5Api, name: str) -> SymbolSpec | None:
    info = mt5.symbol_info(name)
    if info is None:
        return None
    if not bool(getattr(info, "visible", True)):
        # Ticks only arrive for symbols in Market Watch. Selecting one never trades.
        mt5.symbol_select(name, True)
    return SymbolSpec.from_mt5(info)


@dataclass
class _Series:
    columns: Columns

    @property
    def size(self) -> int:
        return len(self.columns["time"])

    @property
    def last_time(self) -> int | None:
        return int(self.columns["time"][-1]) if self.size else None


class MarketData:
    """Owned by the analysis thread; the lock only guards reads from the UI thread."""

    def __init__(
        self,
        gateway: MT5Gateway,
        clock: BrokerClock,
        *,
        counts: Mapping[str, int] | None = None,
        utc_now: Callable[[], float] = time.time,
    ) -> None:
        self._gateway = gateway
        self.clock = clock
        self._counts = dict(BAR_COUNTS if counts is None else counts)
        self._now = utc_now
        self._series: dict[tuple[str, str], _Series] = {}
        self._specs: dict[str, SymbolSpec] = {}
        self._lock = threading.Lock()

    # Symbols -----------------------------------------------------------------------------
    def resolve(self, wanted: Sequence[str]) -> dict[str, str | None]:
        """Map the watchlist names to this broker's names (suffixes such as `.m`)."""

        def work(mt5: MT5Api) -> list[str]:
            return [str(getattr(item, "name", "")) for item in (mt5.symbols_get() or ())]

        names = self._gateway.run("symbols_get", work, timeout=REQUEST_TIMEOUT_SECONDS)
        return {name: resolve_symbol(name, names) for name in wanted}

    def spec(self, broker_symbol: str) -> SymbolSpec | None:
        cached = self._specs.get(broker_symbol)
        if cached is not None:
            return cached
        found = self._gateway.run(
            "symbol_info",
            lambda mt5: read_symbol(mt5, broker_symbol),
            timeout=REQUEST_TIMEOUT_SECONDS,
            arguments={"symbol": broker_symbol},
        )
        if found is not None:
            self._specs[broker_symbol] = found
        return found

    def poll(self, symbols: Sequence[str]) -> PollResult:
        digits = {name: spec.digits for name, spec in self._specs.items()}
        return self._gateway.run(
            "market_poll",
            lambda mt5: read_poll(mt5, symbols, digits),
            timeout=REQUEST_TIMEOUT_SECONDS,
            arguments={"symbols": list(symbols)},
        )

    # Bars --------------------------------------------------------------------------------
    def update(self, broker_symbol: str, timeframe: str) -> int:
        """Download the new closed bars. Returns how many bars were added."""
        limit = self._counts[timeframe]
        key = (broker_symbol, timeframe)
        series = self._series.get(key)
        last = series.last_time if series is not None else None
        count = limit
        if last is not None:
            now = self._now()
            server_now = now + self.clock.offset_at(now) * 3600
            behind = int((server_now - last) // TF_SECONDS[timeframe])
            count = max(3, min(limit, behind + 3))
        fresh = self._gateway.run(
            "copy_rates_from_pos",
            lambda mt5: read_closed_rates(mt5, broker_symbol, timeframe, count),
            timeout=REQUEST_TIMEOUT_SECONDS,
            arguments={"symbol": broker_symbol, "timeframe": timeframe, "count": count},
        )
        merged = merge_columns(series.columns if series is not None else None, fresh, limit)
        with self._lock:
            self._series[key] = _Series(merged)
        if last is None:
            return len(merged["time"])
        return int(np.count_nonzero(merged["time"] > last))

    def last_time(self, broker_symbol: str, timeframe: str) -> int | None:
        """Server time of the newest cached closed bar."""
        series = self._series.get((broker_symbol, timeframe))
        return series.last_time if series is not None else None

    def bars(self, broker_symbol: str, timeframe: str, name: str | None = None) -> Bars:
        """Closed bars with UTC times (`time`) and the broker's times (`server_time`)."""
        with self._lock:
            series = self._series.get((broker_symbol, timeframe))
        symbol = name or broker_symbol
        if series is None or not series.size:
            return Bars.empty(symbol, timeframe)
        columns = series.columns
        server = columns["time"]
        return Bars.build(
            symbol,
            timeframe,
            time=self.clock.to_utc_array(server),
            open=columns["open"],
            high=columns["high"],
            low=columns["low"],
            close=columns["close"],
            volume=columns["tick_volume"],
            spread=columns["spread"],
            server_time=server,
        )

    def forget(self) -> None:
        """Drop every cache, for example after a reconnect to another account."""
        with self._lock:
            self._series.clear()
            self._specs.clear()


def merge_columns(old: Columns | None, fresh: Columns, limit: int) -> Columns:
    """New bars replace old ones from their first time on; keep the newest `limit` bars."""
    if old is None or not len(old["time"]):
        return {name: values[-limit:] for name, values in fresh.items()}
    if not len(fresh["time"]):
        return old
    keep = old["time"] < fresh["time"][0]
    return {name: np.concatenate([old[name][keep], fresh[name]])[-limit:] for name in COLUMNS}
