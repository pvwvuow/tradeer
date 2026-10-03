"""Chart data (spec C11): monthly returns, the P/L calendar, the R distribution, MFE/MAE and
the cost analysis. Pure data; the Analytics and Journal pages draw it."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.analytics.stats import mean, ordered
from app.analytics.trades import TradeRecord, utc_day


def monthly_returns(trades: Sequence[TradeRecord], start_balance: float) -> dict[str, float]:
    """Each month's net result in percent of the balance at the start of that month."""
    found: dict[str, float] = {}
    if start_balance <= 0:
        return found
    balance = start_balance
    month_start: dict[str, float] = {}
    nets: dict[str, float] = {}
    for trade in ordered(trades):
        month = datetime.fromtimestamp(trade.close_time, UTC).strftime("%Y-%m")
        month_start.setdefault(month, balance)
        nets[month] = nets.get(month, 0.0) + trade.net_profit
        balance += trade.net_profit
    for month, net in nets.items():
        base = month_start[month]
        found[month] = net / base * 100.0 if base > 0 else math.nan
    return found


@dataclass(frozen=True)
class CalendarDay:
    day: str  # YYYY-MM-DD (UTC)
    net_profit: float
    trades: int
    wins: int


def pl_calendar(trades: Sequence[TradeRecord]) -> dict[str, CalendarDay]:
    days: dict[str, list[TradeRecord]] = {}
    for trade in trades:
        days.setdefault(utc_day(trade.close_time), []).append(trade)
    return {
        day: CalendarDay(
            day,
            round(sum(t.net_profit for t in items), 2),
            len(items),
            sum(1 for t in items if t.win),
        )
        for day, items in sorted(days.items())
    }


def r_distribution(trades: Sequence[TradeRecord], width: float = 0.5) -> list[tuple[float, int]]:
    """(lower edge, count) of R multiples in bins of `width` R, empty bins included."""
    values = [t.r_multiple for t in trades if t.r_multiple is not None]
    if not values:
        return []
    low = math.floor(min(values) / width)
    high = math.floor(max(values) / width)
    counts = {index: 0 for index in range(low, high + 1)}
    for value in values:
        counts[math.floor(value / width)] += 1
    return [(round(index * width, 6), counts[index]) for index in sorted(counts)]


@dataclass(frozen=True)
class ExcursionPoint:
    mae_r: float
    mfe_r: float
    r: float
    win: bool


@dataclass(frozen=True)
class ExcursionSummary:
    points: tuple[ExcursionPoint, ...]
    left_on_table_r: float | None  # winners: mean of best move minus the result
    losers_were_ahead: int  # losers that were 1 R or more in profit first
    winners_near_stop: int  # winners that went 0.8 R or more against the trade first
    lines: tuple[str, ...]


def excursions(trades: Sequence[TradeRecord]) -> ExcursionSummary:
    points = tuple(
        ExcursionPoint(t.mae_r, t.mfe_r, t.r_multiple, t.win)
        for t in trades
        if t.mae_r is not None and t.mfe_r is not None and t.r_multiple is not None
    )
    winners = [p for p in points if p.win]
    losers = [p for p in points if not p.win]
    left = mean([p.mfe_r - p.r for p in winners])
    ahead = sum(1 for p in losers if p.mfe_r >= 1.0)
    near = sum(1 for p in winners if p.mae_r <= -0.8)
    lines: list[str] = []
    if not points:
        lines.append("No trade has its best and worst move recorded yet (bot trades only).")
    else:
        if left is not None:
            lines.append(f"Winners gave back {left:.2f} R on average from their best move.")
        lines.append(f"{ahead} of {len(losers)} losing trades were 1 R or more in profit first.")
        lines.append(f"{near} of {len(winners)} winners came within 0.2 R of their stop loss.")
    return ExcursionSummary(points, left, ahead, near, tuple(lines))


@dataclass(frozen=True)
class CostAnalysis:
    gross_profit: float  # profit of the winning trades before costs
    commission: float  # positive = paid
    swap: float  # positive = paid
    fee: float
    total: float
    percent_of_gross: float | None
    spread_r: float | None  # mean spread in R at the signal (bot trades)
    slippage: float | None  # mean recorded slippage (bot trades)
    lines: tuple[str, ...]


def cost_analysis(trades: Sequence[TradeRecord]) -> CostAnalysis:
    gross = sum(t.profit for t in trades if t.profit > 0)
    commission = -sum(t.commission for t in trades)
    swap = -sum(t.swap for t in trades)
    fee = -sum(t.fee for t in trades)
    total = commission + swap + fee
    percent = total / gross * 100.0 if gross > 0 else None
    spread = mean([t.spread_r for t in trades if t.spread_r is not None])
    slippage = mean([t.slippage for t in trades if t.slippage is not None])
    lines = [
        f"Commission {commission:,.2f}, swap {swap:,.2f}, fees {fee:,.2f}: {total:,.2f} in total",
        "Costs are "
        + (f"{percent:.1f}% of the gross profit" if percent is not None else "not comparable")
        + " (gross = the winning trades before costs).",
    ]
    if spread is not None:
        lines.append(f"The spread was {spread:.3f} R on average when the signal was made.")
    if slippage is not None:
        lines.append(f"Average recorded slippage: {slippage:.2f} points.")
    lines.append("The spread is paid inside the prices, so it is in the gross result already.")
    return CostAnalysis(
        gross,
        commission,
        swap,
        fee,
        total,
        percent,
        spread,
        slippage,
        tuple(lines),
    )
