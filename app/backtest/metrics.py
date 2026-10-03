"""Backtest statistics (spec C8): equity and drawdown curves, win rate, profit factor,
expectancy in R and money, average win and loss, max drawdown (depth and duration),
Sharpe and Sortino, streaks, and breakdowns by month, session, symbol, weekday and strategy.

Pure functions on the trade list and the equity curve; every number is hand-checked in the
unit tests. Fewer than 100 trades always gives a warning: such numbers are mostly luck.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import numpy.typing as npt

from app.backtest.engine import BacktestResult, BacktestTrade

MIN_TRADES = 100
TRADING_DAYS = 252
DAY = 86_400
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
FEW_TRADES = (
    "Fewer than {minimum} trades ({count}): these numbers are mostly luck. Test a longer "
    "period or more symbols before trusting them."
)

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Drawdown:
    depth: float  # money, positive
    depth_percent: float  # of the peak before it
    duration_seconds: float  # longest time below a previous peak
    peak_time: float
    trough_time: float
    recovered: bool


@dataclass(frozen=True)
class Group:
    """The trades of one month, session, symbol, weekday or strategy."""

    key: str
    trades: int
    win_rate: float
    net_profit: float
    expectancy_r: float | None
    profit_factor: float | None


@dataclass(frozen=True)
class Metrics:
    trades: int
    wins: int
    losses: int
    win_rate: float  # 0..1
    net_profit: float
    gross_profit: float
    gross_loss: float  # negative
    profit_factor: float | None  # None without losing trades
    expectancy_money: float
    expectancy_r: float | None
    average_win: float
    average_loss: float  # negative
    average_win_r: float | None
    average_loss_r: float | None
    payoff_ratio: float | None
    return_percent: float
    max_drawdown: Drawdown
    sharpe: float | None
    sortino: float | None
    longest_losing_streak: int
    longest_winning_streak: int
    commission: float
    swap: float
    start_balance: float
    end_balance: float
    by_month: tuple[Group, ...] = ()
    by_session: tuple[Group, ...] = ()
    by_symbol: tuple[Group, ...] = ()
    by_weekday: tuple[Group, ...] = ()
    by_strategy: tuple[Group, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, Any]:
        found: dict[str, Any] = _clean(asdict(self))
        return found


def _clean(value: Any) -> Any:
    """JSON-safe: no NaN or infinity (they become None)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(item) for item in value]
    return value


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def profit_factor(trades: Sequence[BacktestTrade]) -> float | None:
    gross_profit = sum(t.net_profit for t in trades if t.net_profit > 0)
    gross_loss = sum(t.net_profit for t in trades if t.net_profit < 0)
    return gross_profit / -gross_loss if gross_loss < 0 else None


def expectancy_r(trades: Sequence[BacktestTrade]) -> float | None:
    return _mean([t.r_multiple for t in trades if t.r_multiple is not None])


def streaks(trades: Sequence[BacktestTrade]) -> tuple[int, int]:
    """The longest runs of losing and of winning trades (break-even ends both)."""
    losing = winning = longest_losing = longest_winning = 0
    for trade in trades:
        losing = losing + 1 if trade.net_profit < 0 else 0
        winning = winning + 1 if trade.net_profit > 0 else 0
        longest_losing = max(longest_losing, losing)
        longest_winning = max(longest_winning, winning)
    return longest_losing, longest_winning


def drawdown_curve(equity: FloatArray) -> FloatArray:
    """Equity below its running peak, in percent (0 or negative) per point."""
    if not len(equity):
        return np.zeros(0, dtype=np.float64)
    peak = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        found = np.where(peak > 0, (equity / peak - 1.0) * 100.0, 0.0)
    return found.astype(np.float64)


def max_drawdown(times: FloatArray, equity: FloatArray) -> Drawdown:
    if not len(equity):
        return Drawdown(0.0, 0.0, 0.0, 0.0, 0.0, True)
    peak = np.maximum.accumulate(equity)
    depth = peak - equity
    trough = int(np.argmax(depth))
    peak_index = int(np.flatnonzero(equity[: trough + 1] == peak[trough])[0])
    recovered = bool(np.any(equity[trough:] >= peak[trough])) if depth[trough] > 0 else True
    top = float(peak[trough])
    # The longest stretch below a previous peak, from that peak to the recovery (or the end).
    longest = 0.0
    start: float | None = None
    for index in range(len(equity)):
        below = equity[index] < peak[index]
        if below and start is None:
            start = float(times[index - 1]) if index else float(times[index])
        elif not below and start is not None:
            longest = max(longest, float(times[index]) - start)
            start = None
    if start is not None:
        longest = max(longest, float(times[-1]) - start)
    return Drawdown(
        depth=float(depth[trough]),
        depth_percent=float(depth[trough] / top * 100.0) if top > 0 else 0.0,
        duration_seconds=longest,
        peak_time=float(times[peak_index]),
        trough_time=float(times[trough]),
        recovered=recovered,
    )


def daily_returns(times: FloatArray, equity: FloatArray, start_balance: float) -> FloatArray:
    """Returns of the equity at each UTC day's last point (days with bars only)."""
    if not len(equity):
        return np.zeros(0, dtype=np.float64)
    days = (times // DAY).astype(np.int64)
    last = np.flatnonzero(np.r_[days[1:] != days[:-1], True])
    closes = np.r_[start_balance, equity[last]]
    with np.errstate(divide="ignore", invalid="ignore"):
        found = np.diff(closes) / closes[:-1]
    finite: FloatArray = found[np.isfinite(found)].astype(np.float64)
    return finite


def sharpe_sortino(returns: FloatArray) -> tuple[float | None, float | None]:
    """Annualized (252 days), with no risk-free rate."""
    if len(returns) < 2:
        return None, None
    mean = float(np.mean(returns))
    spread = float(np.std(returns, ddof=1))
    sharpe = mean / spread * math.sqrt(TRADING_DAYS) if spread > 0 else None
    downside = np.minimum(returns, 0.0)
    down = float(np.sqrt(np.mean(downside**2)))
    sortino = mean / down * math.sqrt(TRADING_DAYS) if down > 0 else None
    return sharpe, sortino


def group(
    trades: Sequence[BacktestTrade],
    key: Callable[[BacktestTrade], str],
) -> tuple[Group, ...]:
    buckets: dict[str, list[BacktestTrade]] = {}
    for trade in trades:
        buckets.setdefault(key(trade), []).append(trade)
    return tuple(
        Group(
            key=name,
            trades=len(items),
            win_rate=sum(1 for t in items if t.net_profit > 0) / len(items),
            net_profit=round(sum(t.net_profit for t in items), 2),
            expectancy_r=expectancy_r(items),
            profit_factor=profit_factor(items),
        )
        for name, items in sorted(buckets.items())
    )


def _utc(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def _weekday(trade: BacktestTrade) -> str:
    day = _utc(trade.open_time).weekday()
    return f"{day + 1} {WEEKDAYS[day]}"  # sorts Monday first


def compute_metrics(
    trades: Sequence[BacktestTrade],
    times: FloatArray,
    equity: FloatArray,
    start_balance: float,
    *,
    minimum_trades: int = MIN_TRADES,
) -> Metrics:
    ordered = sorted(trades, key=lambda t: (t.close_time, t.open_time))
    wins = [t for t in ordered if t.net_profit > 0]
    losses = [t for t in ordered if t.net_profit < 0]
    count = len(ordered)
    net = sum(t.net_profit for t in ordered)
    gross_profit = sum(t.net_profit for t in wins)
    gross_loss = sum(t.net_profit for t in losses)
    average_win = gross_profit / len(wins) if wins else 0.0
    average_loss = gross_loss / len(losses) if losses else 0.0
    win_r = _mean([t.r_multiple for t in wins if t.r_multiple is not None])
    loss_r = _mean([t.r_multiple for t in losses if t.r_multiple is not None])
    end_balance = start_balance + net
    losing, winning = streaks(ordered)
    sharpe, sortino = sharpe_sortino(daily_returns(times, equity, start_balance))
    warnings: list[str] = []
    if count < minimum_trades:
        warnings.append(FEW_TRADES.format(minimum=minimum_trades, count=count))
    return Metrics(
        trades=count,
        wins=len(wins),
        losses=len(losses),
        win_rate=len(wins) / count if count else 0.0,
        net_profit=round(net, 2),
        gross_profit=round(gross_profit, 2),
        gross_loss=round(gross_loss, 2),
        profit_factor=profit_factor(ordered),
        expectancy_money=net / count if count else 0.0,
        expectancy_r=expectancy_r(ordered),
        average_win=average_win,
        average_loss=average_loss,
        average_win_r=win_r,
        average_loss_r=loss_r,
        payoff_ratio=average_win / -average_loss if wins and losses else None,
        return_percent=net / start_balance * 100.0 if start_balance > 0 else 0.0,
        max_drawdown=max_drawdown(times, equity),
        sharpe=sharpe,
        sortino=sortino,
        longest_losing_streak=losing,
        longest_winning_streak=winning,
        commission=round(sum(t.commission for t in ordered), 2),
        swap=round(sum(t.swap for t in ordered), 2),
        start_balance=start_balance,
        end_balance=round(end_balance, 2),
        by_month=group(ordered, lambda t: _utc(t.close_time).strftime("%Y-%m")),
        by_session=group(ordered, lambda t: t.session or "none"),
        by_symbol=group(ordered, lambda t: t.symbol),
        by_weekday=group(ordered, _weekday),
        by_strategy=group(ordered, lambda t: t.strategy or "unknown"),
        warnings=tuple(warnings),
    )


def result_metrics(result: BacktestResult, *, minimum_trades: int = MIN_TRADES) -> Metrics:
    return compute_metrics(
        result.trades,
        result.times,
        result.equity,
        result.start_balance,
        minimum_trades=minimum_trades,
    )


def summary_lines(metrics: Metrics, currency: str = "") -> list[str]:
    """The headline numbers as short plain lines (CLI and logs)."""
    unit = f" {currency}" if currency else ""

    def number(value: float | None, digits: int = 2) -> str:
        return "n/a" if value is None or not math.isfinite(value) else f"{value:.{digits}f}"

    dd = metrics.max_drawdown
    return [
        f"Trades {metrics.trades}: {metrics.wins} won, {metrics.losses} lost "
        f"(win rate {metrics.win_rate * 100:.1f}%)",
        f"Net profit {metrics.net_profit:+,.2f}{unit} ({metrics.return_percent:+.2f}%), "
        f"profit factor {number(metrics.profit_factor)}",
        f"Expectancy {number(metrics.expectancy_r, 3)} R per trade "
        f"({metrics.expectancy_money:+,.2f}{unit})",
        f"Max drawdown {dd.depth:,.2f}{unit} ({dd.depth_percent:.2f}%), longest "
        f"{dd.duration_seconds / DAY:.1f} days below a peak",
        f"Sharpe {number(metrics.sharpe)}, Sortino {number(metrics.sortino)}, longest losing "
        f"streak {metrics.longest_losing_streak}",
        *metrics.warnings,
    ]


def groups_json(groups: Sequence[Group]) -> list[Mapping[str, Any]]:
    return [_clean(asdict(item)) for item in groups]
