"""History for a backtest (spec C8): every closed bar of one symbol from MT5, cached on disk.

Bars come from `copy_rates_range` in chunks of at most `CHUNK_BARS` (a real terminal refused
100,000 bars in one request), in broker server time, and are converted to UTC with the
broker clock, exactly like the live cache. Each timeframe starts early enough to hold as many
bars as the live analysis keeps (`BAR_COUNTS`) at the test's first bar, so indicators are warm
from the start. MT5 only has as much history as its "Max bars in chart" setting allows; when
it has less than asked for, the loader says so instead of quietly testing a shorter period.

The cache (`<data>/backtest_cache/<symbol>_<tf>.npz`) keeps downloaded bars, so the next
test of the same symbol only reads what is new.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar

import numpy as np

from app.analysis.bars import ANALYSIS_TIMEFRAMES, TF_SECONDS, Bars
from app.backtest.engine import History
from app.core.clock import BrokerClock
from app.mt5 import api
from app.mt5.api import TIMEFRAMES, MT5Api
from app.mt5.errors import error_from_last
from app.mt5.market_data import (
    BAR_COUNTS,
    COLUMNS,
    Columns,
    _empty_columns,
    _to_columns,
    read_symbol,
)
from app.mt5.models import SymbolSpec
from app.mt5.symbols import resolve_symbol

CHUNK_BARS = 20_000
REQUEST_TIMEOUT_SECONDS = 120.0
DAY = 86_400
# Calendar days per bar of trading time: markets close at weekends and on holidays.
CALENDAR_FACTOR = 1.5
CACHE_FOLDER = "backtest_cache"

Note = Callable[[str], None]
T = TypeVar("T")


class Gateway(Protocol):
    """`MT5Gateway.run`: call `work` with the MT5 package in the MT5 thread."""

    def run(
        self,
        name: str,
        work: Callable[[MT5Api], T],
        *,
        timeout: float | None = None,
        arguments: Mapping[str, Any] | None = None,
    ) -> T: ...


class DirectGateway:
    """For code that already runs in the MT5 thread (the `--backtest` command)."""

    def __init__(self, mt5: MT5Api) -> None:
        self._mt5 = mt5

    def run(
        self,
        name: str,
        work: Callable[[MT5Api], T],
        *,
        timeout: float | None = None,
        arguments: Mapping[str, Any] | None = None,
    ) -> T:
        return work(self._mt5)


@dataclass(frozen=True)
class LoadReport:
    first: Mapping[str, int]  # timeframe -> first UTC bar open time (0 = none)
    counts: Mapping[str, int]
    notes: tuple[str, ...]


def warmup_seconds(timeframe: str) -> int:
    """How far before the test start this timeframe's bars must begin."""
    return int(BAR_COUNTS[timeframe] * TF_SECONDS[timeframe] * CALENDAR_FACTOR) + 4 * DAY


def read_range(mt5: MT5Api, symbol: str, timeframe: str, start: int, end: int) -> Columns:
    """Runs in the gateway thread. Closed bars opening in [start, end] (server seconds)."""
    step = TF_SECONDS[timeframe]
    parts: list[Columns] = []
    cursor = start - start % step
    while cursor <= end:
        stop = min(end, cursor + (CHUNK_BARS - 1) * step)
        rates = mt5.copy_rates_range(
            symbol,
            TIMEFRAMES[timeframe],
            datetime.fromtimestamp(cursor, UTC),
            datetime.fromtimestamp(stop, UTC),
        )
        if rates is None:
            code, detail = mt5.last_error()
            if code != api.RES_S_OK:
                raise error_from_last((code, detail))
        else:
            parts.append(_to_columns(rates))
        cursor = stop + step
    return merge(parts)


def merge(parts: list[Columns]) -> Columns:
    """One series from overlapping parts: sorted by time, the later part wins a duplicate."""
    found = [part for part in parts if len(part["time"])]
    if not found:
        return _empty_columns()
    joined = {name: np.concatenate([part[name] for part in found]) for name in COLUMNS}
    # Keep the last occurrence of each time (the newest download).
    order = np.argsort(joined["time"], kind="stable")
    times = joined["time"][order]
    keep = np.r_[times[1:] != times[:-1], True]
    return {name: joined[name][order][keep] for name in COLUMNS}


def _cache_file(folder: Path, symbol: str, timeframe: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", symbol)
    return folder / f"{safe}_{timeframe}.npz"


def load_cache(folder: Path | None, symbol: str, timeframe: str) -> Columns:
    if folder is None:
        return _empty_columns()
    path = _cache_file(folder, symbol, timeframe)
    try:
        with np.load(path) as data:
            return {name: np.asarray(data[name]) for name in COLUMNS}
    except (OSError, KeyError, ValueError):
        return _empty_columns()


def save_cache(folder: Path | None, symbol: str, timeframe: str, columns: Columns) -> None:
    if folder is None:
        return
    folder.mkdir(parents=True, exist_ok=True)
    path = _cache_file(folder, symbol, timeframe)
    temporary = path.with_name(path.name + ".tmp.npz")
    arrays: dict[str, Any] = dict(columns)
    np.savez_compressed(temporary, **arrays)
    temporary.replace(path)


def to_bars(name: str, timeframe: str, columns: Columns, clock: BrokerClock) -> Bars:
    server = columns["time"].astype(np.int64)
    return Bars.build(
        name,
        timeframe,
        time=clock.to_utc_array(server),
        open=columns["open"],
        high=columns["high"],
        low=columns["low"],
        close=columns["close"],
        volume=columns["tick_volume"],
        spread=columns["spread"],
        server_time=server,
    )


def _day(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d")


def load_history(
    gateway: Gateway,
    name: str,
    broker_symbol: str,
    start: float,
    end: float,
    clock: BrokerClock,
    *,
    cache: Path | None = None,
    note: Note | None = None,
) -> tuple[History, LoadReport]:
    """The bars from `start` (UTC) minus the warm-up to `end`, and what MT5 could not give."""
    say = note or (lambda text: None)
    spec: SymbolSpec | None = gateway.run(
        "symbol_info",
        lambda mt5: read_symbol(mt5, broker_symbol),
        timeout=REQUEST_TIMEOUT_SECONDS,
        arguments={"symbol": broker_symbol},
    )
    if spec is None:
        raise ValueError(f"MT5 does not know the symbol {broker_symbol}")
    offset = clock.offset_at(end) * 3600
    server_end = int(end + offset)
    found: dict[str, Bars] = {}
    firsts: dict[str, int] = {}
    counts: dict[str, int] = {}
    notes: list[str] = []
    for timeframe in ANALYSIS_TIMEFRAMES:
        want = int(start + clock.offset_at(start) * 3600) - warmup_seconds(timeframe)
        cached = load_cache(cache, broker_symbol, timeframe)
        parts = [cached]
        times = cached["time"]
        step = TF_SECONDS[timeframe]
        if not len(times) or int(times[0]) > want + 7 * DAY:
            say(f"Downloading {broker_symbol} {timeframe} from {_day(want)}")
            parts.append(_read(gateway, broker_symbol, timeframe, want, server_end))
        elif int(times[-1]) + step < server_end - step:
            say(f"Downloading new {broker_symbol} {timeframe} bars")
            parts.append(_read(gateway, broker_symbol, timeframe, int(times[-1]), server_end))
        merged = merge(parts)
        save_cache(cache, broker_symbol, timeframe, merged)
        inside = (merged["time"] >= want) & (merged["time"] <= server_end - step)
        columns = {key: values[inside] for key, values in merged.items()}
        bars = to_bars(name, timeframe, columns, clock)
        found[timeframe] = bars
        counts[timeframe] = len(bars)
        firsts[timeframe] = int(bars.time[0]) if len(bars) else 0
        before = int(np.count_nonzero(bars.time + step <= start))
        if before < BAR_COUNTS[timeframe]:
            first = _day(float(bars.time[0])) if len(bars) else "none"
            notes.append(
                f"{timeframe}: MT5 has bars from {first} only ({before} before the start, the "
                f"live analysis keeps {BAR_COUNTS[timeframe]}); the first days of the test run "
                "on short history. Raise Tools > Options > Charts > Max bars in chart.",
            )
    for text in notes:
        say(text)
    history = History(name, broker_symbol, spec, clock, found)
    return history, LoadReport(firsts, counts, tuple(notes))


def resolve_broker_symbol(gateway: Gateway, name: str) -> str:
    """The broker's name for `name` ("EURUSD" -> "EURUSD.m"); ValueError if not offered."""

    def work(mt5: MT5Api) -> list[str]:
        return [str(getattr(item, "name", "")) for item in (mt5.symbols_get() or ())]

    names: list[str] = gateway.run("symbols_get", work, timeout=REQUEST_TIMEOUT_SECONDS)
    found = resolve_symbol(name, names)
    if found is None:
        raise ValueError(f"{name} is not offered by this broker; add it in MT5's Market Watch")
    return found


def _read(gateway: Gateway, symbol: str, timeframe: str, start: int, end: int) -> Columns:
    arguments: dict[str, Any] = {"symbol": symbol, "timeframe": timeframe}
    found: Columns = gateway.run(
        "copy_rates_range",
        lambda mt5: read_range(mt5, symbol, timeframe, start, end),
        timeout=REQUEST_TIMEOUT_SECONDS,
        arguments=arguments,
    )
    return found
