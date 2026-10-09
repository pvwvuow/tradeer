"""The data of the AI Lab's chart cards (docs/NOCURVE_V2.md 20e4), drawn only from the app's
own records: closed trades, backtest runs and MT5 bars. Nothing is invented: with too few
records a builder returns an empty shape and the card says so.

- `trade_stats`: net result in percent of the start balance, the deepest drawdown, the
  number of trades and the mean R (the stats card).
- `equity_curve`: the balance after each closed trade (the equity card, current vs AI).
- `r_histogram`: how the R results spread (the distribution card).
- `monte_carlo_fan`: the same trades in 40 random orders (the fan card), compounded as the
  risk manager sizes them: each result is a share of the balance it was taken with.
- `trade_table`: the newest trades as rows (the table card).
- `candles` and `markers`: the last bars of a symbol with the entries on them.

Pure functions over plain sequences, so they run in a worker thread and in the tests.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import numpy as np

FAN_PATHS = 40
FAN_SEED = 7
R_WIDTH = 0.5
R_LOW = -3.0
R_HIGH = 5.0
TABLE_ROWS = 20
CANDLES = 60
MIN_FOR_FAN = 5


class TradeLike(Protocol):
    @property
    def symbol(self) -> str: ...
    @property
    def strategy(self) -> str: ...
    @property
    def direction(self) -> str: ...
    @property
    def open_time(self) -> float: ...
    @property
    def close_time(self) -> float: ...
    @property
    def open_price(self) -> float: ...
    @property
    def net_profit(self) -> float: ...
    @property
    def r_multiple(self) -> float | None: ...


def ordered(trades: Sequence[TradeLike]) -> list[TradeLike]:
    return sorted(trades, key=lambda trade: (trade.close_time, trade.open_time))


def equity_curve(trades: Sequence[TradeLike], start_balance: float) -> list[float]:
    """The start balance, then the balance after each closed trade (oldest first)."""
    balance = start_balance
    found = [balance]
    for trade in ordered(trades):
        balance += trade.net_profit
        found.append(balance)
    return found


def max_drawdown_percent(values: Sequence[float]) -> float:
    """The deepest fall from a peak, in percent of that peak (0 without a fall)."""
    peak = -math.inf
    deepest = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            deepest = max(deepest, (peak - value) / peak * 100.0)
    return deepest


@dataclass(frozen=True)
class TradeStats:
    trades: int
    net: float
    net_percent: float | None  # None without a start balance
    max_drawdown_percent: float
    expectancy_r: float | None  # None without R values
    with_r: int


def trade_stats(trades: Sequence[TradeLike], start_balance: float) -> TradeStats:
    curve = equity_curve(trades, start_balance) if start_balance > 0 else []
    net = sum(trade.net_profit for trade in trades)
    values = [trade.r_multiple for trade in trades if trade.r_multiple is not None]
    finite = [value for value in values if math.isfinite(value)]
    return TradeStats(
        trades=len(trades),
        net=net,
        net_percent=net / start_balance * 100.0 if start_balance > 0 else None,
        max_drawdown_percent=max_drawdown_percent(curve),
        expectancy_r=sum(finite) / len(finite) if finite else None,
        with_r=len(finite),
    )


@dataclass(frozen=True)
class Histogram:
    edges: tuple[float, ...]  # len(counts) + 1; the first and last bins hold the tails
    counts: tuple[int, ...]
    total: int
    mean: float | None
    median: float | None
    losing: int  # results below 0 R


def r_histogram(
    values: Sequence[float | None],
    width: float = R_WIDTH,
    low: float = R_LOW,
    high: float = R_HIGH,
) -> Histogram:
    """R results in bins of `width` from `low` to `high`; the outer bins take the tails."""
    found = [float(value) for value in values if value is not None and math.isfinite(value)]
    bins = max(1, round((high - low) / width))
    edges = tuple(round(low + index * width, 6) for index in range(bins + 1))
    counts = [0] * bins
    for value in found:
        index = math.floor((value - low) / width)
        counts[min(max(index, 0), bins - 1)] += 1
    return Histogram(
        edges=edges,
        counts=tuple(counts),
        total=len(found),
        mean=sum(found) / len(found) if found else None,
        median=statistics.median(found) if found else None,
        losing=sum(1 for value in found if value < 0),
    )


def trade_returns(trades: Sequence[TradeLike], start_balance: float) -> list[float]:
    """Each result as a share of the balance just before it (oldest first)."""
    balance = start_balance
    found: list[float] = []
    for trade in ordered(trades):
        found.append(trade.net_profit / balance if balance > 0 else 0.0)
        balance += trade.net_profit
    return found


@dataclass(frozen=True)
class Fan:
    paths: tuple[tuple[float, ...], ...]  # each path in percent from the start (0 first)
    low: tuple[float, ...]  # the 5th percentile per step
    middle: tuple[float, ...]  # the median per step
    high: tuple[float, ...]  # the 95th percentile per step
    real: tuple[float, ...]  # the real order
    trades: int
    drawdown_median: float  # the median of the paths' deepest drawdown, percent
    drawdown_worst: float

    @property
    def empty(self) -> bool:
        return not self.paths


def monte_carlo_fan(
    returns: Sequence[float],
    paths: int = FAN_PATHS,
    seed: int = FAN_SEED,
) -> Fan:
    """The trades in `paths` random orders (each trade once), compounded, in percent."""
    count = len(returns)
    if count < MIN_FOR_FAN or paths < 1:
        return Fan((), (), (), (), (), count, 0.0, 0.0)
    values = np.asarray(returns, dtype=np.float64)
    rng = np.random.default_rng(seed)
    picks = np.argsort(rng.random((paths, count)), axis=1)
    grown = np.cumprod(1.0 + values[picks], axis=1)
    start = np.ones((paths, 1))
    curves = np.concatenate([start, grown], axis=1)
    percent = (curves - 1.0) * 100.0
    peaks = np.maximum.accumulate(curves, axis=1)
    depth = (1.0 - curves / peaks).max(axis=1) * 100.0
    real = np.concatenate([[1.0], np.cumprod(1.0 + values)])
    return Fan(
        paths=tuple(tuple(float(v) for v in row) for row in percent),
        low=tuple(float(v) for v in np.percentile(percent, 5, axis=0)),
        middle=tuple(float(v) for v in np.percentile(percent, 50, axis=0)),
        high=tuple(float(v) for v in np.percentile(percent, 95, axis=0)),
        real=tuple(float(v) for v in (real - 1.0) * 100.0),
        trades=count,
        drawdown_median=float(np.median(depth)),
        drawdown_worst=float(depth.max()),
    )


def _r(value: float | None) -> str:
    return f"{value:+.2f}" if value is not None and math.isfinite(value) else "n/a"


def trade_table(trades: Sequence[TradeLike], limit: int = TABLE_ROWS) -> list[list[str]]:
    """Newest first: closed (UTC), symbol, strategy, side, R, net."""
    rows: list[list[str]] = []
    for trade in list(reversed(ordered(trades)))[:limit]:
        when = datetime.fromtimestamp(trade.close_time, UTC).strftime("%m-%d %H:%M")
        rows.append(
            [
                when,
                trade.symbol,
                trade.strategy,
                trade.direction.upper(),
                _r(trade.r_multiple),
                f"{trade.net_profit:+,.2f}",
            ],
        )
    return rows


@dataclass(frozen=True)
class Candle:
    time: float  # the bar's open, UTC seconds
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Marker:
    index: int  # the candle the trade opened in
    buy: bool
    price: float
    win: bool


def candles(
    times: Sequence[float],
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    count: int = CANDLES,
) -> list[Candle]:
    """The last `count` bars (oldest first)."""
    size = min(len(times), len(opens), len(highs), len(lows), len(closes))
    first = max(size - count, 0)
    return [
        Candle(float(times[i]), float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i]))
        for i in range(first, size)
    ]


def markers(
    bars: Sequence[Candle],
    trades: Sequence[TradeLike],
    symbol: str,
    seconds: float,
) -> list[Marker]:
    """The trades of `symbol` opened inside the shown bars, on the bar they opened in."""
    if not bars:
        return []
    found: list[Marker] = []
    opens = [bar.time for bar in bars]
    end = bars[-1].time + seconds
    wanted = symbol.replace("/", "").upper()
    for trade in trades:
        name = trade.symbol.replace("/", "").upper()
        if not name.startswith(wanted) or not opens[0] <= trade.open_time < end:
            continue
        index = bisect.bisect_right(opens, trade.open_time) - 1  # bars can have gaps
        win = trade.net_profit > 0
        found.append(Marker(index, trade.direction == "buy", trade.open_price, win))
    return found
