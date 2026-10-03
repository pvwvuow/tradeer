"""Performance statistics of closed trades (spec C11), every number hand-checked in tests.

The equity curve starts at `start_balance` at the first trade's open time and adds each
trade's net result at its close. Sharpe and Sortino come from the daily returns of that
curve (252 days, no risk-free rate), as in the backtest. The recovery factor is the net
result divided by the largest drawdown in money; the time to recover runs from the bottom of
that drawdown to the first close back at its peak (None while it has not recovered).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from app.analytics.trades import TradeRecord
from app.backtest.metrics import (
    Drawdown,
    daily_returns,
    drawdown_curve,
    max_drawdown,
    sharpe_sortino,
)

FloatArray = npt.NDArray[np.float64]
MIN_TRADES = 30
FEW_TRADES = "Only {count} trades: these numbers are mostly luck until there are {minimum}."


def clean(value: Any) -> Any:
    """JSON-safe: NaN and infinity become None."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [clean(item) for item in value]
    return value


def ordered(trades: Sequence[TradeRecord]) -> list[TradeRecord]:
    return sorted(trades, key=lambda t: (t.close_time, t.open_time, t.id))


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


@dataclass(frozen=True)
class EquityCurve:
    times: FloatArray
    equity: FloatArray
    drawdown_percent: FloatArray


def equity_curve(trades: Sequence[TradeRecord], start_balance: float) -> EquityCurve:
    items = ordered(trades)
    if not items:
        empty = np.zeros(0, dtype=np.float64)
        return EquityCurve(empty, empty, empty)
    first = min(t.open_time or t.close_time for t in items)
    times = np.asarray([first, *(t.close_time for t in items)], dtype=np.float64)
    steps = np.asarray([0.0, *(t.net_profit for t in items)], dtype=np.float64)
    equity = np.asarray(start_balance + np.cumsum(steps), dtype=np.float64)
    return EquityCurve(times, equity, drawdown_curve(equity))


def streaks(trades: Sequence[TradeRecord]) -> tuple[int, int, int]:
    """The longest losing and winning runs, and the current run (+ wins, - losses)."""
    losing = winning = longest_losing = longest_winning = 0
    for trade in trades:
        losing = losing + 1 if trade.loss else 0
        winning = winning + 1 if trade.win else 0
        longest_losing = max(longest_losing, losing)
        longest_winning = max(longest_winning, winning)
    return longest_losing, longest_winning, winning if winning else -losing


def recovery_seconds(times: FloatArray, equity: FloatArray, drawdown: Drawdown) -> float | None:
    """From the bottom of the largest drawdown to the first point back at its peak."""
    if drawdown.depth <= 0:
        return 0.0
    peak = drawdown.trough_time
    top = float(np.max(equity[times <= peak])) if len(equity) else 0.0
    after = np.flatnonzero((times > peak) & (equity >= top - 1e-9))
    if not len(after):
        return None
    return float(times[after[0]]) - peak


@dataclass(frozen=True)
class Stats:
    trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate: float  # 0..1
    net_profit: float
    gross_profit: float
    gross_loss: float  # negative
    profit_factor: float | None
    expectancy_money: float
    expectancy_r: float | None
    average_win: float
    average_loss: float  # negative
    payoff_ratio: float | None
    largest_win: float
    largest_loss: float
    longest_winning_streak: int
    longest_losing_streak: int
    current_streak: int
    sharpe: float | None
    sortino: float | None
    max_drawdown: float  # money, positive
    max_drawdown_percent: float
    drawdown_duration_seconds: float
    recovery_factor: float | None
    time_to_recover_seconds: float | None  # None: not recovered yet
    commission: float
    swap: float
    fee: float
    start_balance: float
    return_percent: float | None
    warnings: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        found: dict[str, Any] = clean(asdict(self))
        return found


def compute_stats(
    trades: Sequence[TradeRecord],
    start_balance: float = 0.0,
    *,
    minimum_trades: int = MIN_TRADES,
) -> Stats:
    items = ordered(trades)
    wins = [t for t in items if t.win]
    losses = [t for t in items if t.loss]
    count = len(items)
    net = sum(t.net_profit for t in items)
    gross_profit = sum(t.net_profit for t in wins)
    gross_loss = sum(t.net_profit for t in losses)
    average_win = gross_profit / len(wins) if wins else 0.0
    average_loss = gross_loss / len(losses) if losses else 0.0
    curve = equity_curve(items, start_balance)
    drawdown = max_drawdown(curve.times, curve.equity)
    has_balance = start_balance > 0
    sharpe: float | None = None
    sortino: float | None = None
    if has_balance:
        sharpe, sortino = sharpe_sortino(daily_returns(curve.times, curve.equity, start_balance))
    losing, winning, current = streaks(items)
    warnings: list[str] = []
    if count < minimum_trades:
        warnings.append(FEW_TRADES.format(count=count, minimum=minimum_trades))
    if not has_balance:
        warnings.append("No start balance: percentages, Sharpe and Sortino are not shown.")
    return Stats(
        trades=count,
        wins=len(wins),
        losses=len(losses),
        breakeven=count - len(wins) - len(losses),
        win_rate=len(wins) / count if count else 0.0,
        net_profit=round(net, 2),
        gross_profit=round(gross_profit, 2),
        gross_loss=round(gross_loss, 2),
        profit_factor=gross_profit / -gross_loss if gross_loss < 0 else None,
        expectancy_money=net / count if count else 0.0,
        expectancy_r=mean([t.r_multiple for t in items if t.r_multiple is not None]),
        average_win=average_win,
        average_loss=average_loss,
        payoff_ratio=average_win / -average_loss if wins and losses else None,
        largest_win=max((t.net_profit for t in wins), default=0.0),
        largest_loss=min((t.net_profit for t in losses), default=0.0),
        longest_winning_streak=winning,
        longest_losing_streak=losing,
        current_streak=current,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=round(drawdown.depth, 2),
        max_drawdown_percent=drawdown.depth_percent if has_balance else 0.0,
        drawdown_duration_seconds=drawdown.duration_seconds,
        recovery_factor=net / drawdown.depth if drawdown.depth > 0 else None,
        time_to_recover_seconds=recovery_seconds(curve.times, curve.equity, drawdown),
        commission=round(sum(t.commission for t in items), 2),
        swap=round(sum(t.swap for t in items), 2),
        fee=round(sum(t.fee for t in items), 2),
        start_balance=start_balance,
        return_percent=net / start_balance * 100.0 if has_balance else None,
        warnings=tuple(warnings),
    )


def stat_rows(stats: Stats, currency: str = "") -> list[tuple[str, str]]:
    """The headline numbers as label/value pairs for tables and reports."""
    unit = f" {currency}" if currency else ""

    def number(value: float | None, digits: int = 2, suffix: str = "") -> str:
        if value is None or not math.isfinite(value):
            return "n/a"
        return f"{value:,.{digits}f}{suffix}"

    def days(seconds: float | None) -> str:
        return "not yet" if seconds is None else f"{seconds / 86_400:.1f} days"

    won, lost = stats.longest_winning_streak, stats.longest_losing_streak
    costs = f"commission {stats.commission:+,.2f}, swap {stats.swap:+,.2f}, fees {stats.fee:+,.2f}"
    return [
        ("Trades", f"{stats.trades} ({stats.wins} won, {stats.losses} lost)"),
        ("Win rate", f"{stats.win_rate * 100:.1f}%"),
        ("Net profit", f"{stats.net_profit:+,.2f}{unit}"),
        ("Return", number(stats.return_percent, 2, "%")),
        ("Profit factor", number(stats.profit_factor)),
        ("Expectancy", f"{stats.expectancy_money:+,.2f}{unit} ({number(stats.expectancy_r, 3)} R)"),
        ("Average win / loss", f"{stats.average_win:,.2f} / {stats.average_loss:,.2f}{unit}"),
        ("Payoff ratio", number(stats.payoff_ratio)),
        ("Largest win / loss", f"{stats.largest_win:+,.2f} / {stats.largest_loss:+,.2f}{unit}"),
        ("Longest streaks", f"{won} won, {lost} lost"),
        ("Sharpe / Sortino", f"{number(stats.sharpe)} / {number(stats.sortino)}"),
        (
            "Max drawdown",
            f"{stats.max_drawdown:,.2f}{unit} ({stats.max_drawdown_percent:.2f}%), "
            f"{days(stats.drawdown_duration_seconds)} below a peak",
        ),
        ("Recovery factor", number(stats.recovery_factor)),
        ("Time to recover", days(stats.time_to_recover_seconds)),
        ("Costs", costs),
    ]
