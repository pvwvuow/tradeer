"""Comparisons (spec C11): live vs paper vs backtest, config v1 vs v2, period A vs period B.
Two (or more) sets of statistics side by side, with the change of each number."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.analytics.stats import Stats, compute_stats
from app.analytics.trades import TradeRecord

FIELDS: tuple[tuple[str, str], ...] = (
    ("trades", "Trades"),
    ("win_rate", "Win rate"),
    ("net_profit", "Net profit"),
    ("profit_factor", "Profit factor"),
    ("expectancy_r", "Expectancy (R)"),
    ("expectancy_money", "Expectancy (money)"),
    ("payoff_ratio", "Payoff ratio"),
    ("max_drawdown", "Max drawdown"),
    ("sharpe", "Sharpe"),
)


@dataclass(frozen=True)
class Column:
    name: str
    values: dict[str, float | None]


def column(name: str, stats: Stats) -> Column:
    data = stats.to_json()
    return Column(name, {key: _number(data.get(key)) for key, _ in FIELDS})


def backtest_column(name: str, metrics: Mapping[str, Any]) -> Column:
    """A saved backtest run (its `metrics_json`) as a comparison column."""
    drawdown = metrics.get("max_drawdown") or {}
    values = {key: _number(metrics.get(key)) for key, _ in FIELDS}
    values["max_drawdown"] = _number(drawdown.get("depth") if isinstance(drawdown, dict) else None)
    return Column(name, values)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def split(
    trades: Sequence[TradeRecord],
    key: str,
    start_balance: float = 0.0,
) -> list[Column]:
    """One column per mode ("mode"), config ("config") or strategy ("strategy")."""
    groups: dict[str, list[TradeRecord]] = {}
    for trade in trades:
        groups.setdefault(str(getattr(trade, key)) or "none", []).append(trade)
    ordered = sorted(groups.items())
    return [column(name, compute_stats(items, start_balance)) for name, items in ordered]


def periods(
    trades: Sequence[TradeRecord],
    first: tuple[float, float],
    second: tuple[float, float],
    start_balance: float = 0.0,
) -> list[Column]:
    def inside(span: tuple[float, float]) -> list[TradeRecord]:
        return [t for t in trades if span[0] <= t.close_time < span[1]]

    return [
        column("Period A", compute_stats(inside(first), start_balance)),
        column("Period B", compute_stats(inside(second), start_balance)),
    ]


def rows(columns: Sequence[Column]) -> list[list[str]]:
    """Table rows: the label, each column's value, and the change from the first column."""
    found: list[list[str]] = []
    for key, title in FIELDS:
        values = [c.values.get(key) for c in columns]
        cells = [title, *(_show(key, value) for value in values)]
        base = values[0] if values else None
        last = values[-1] if values else None
        if len(values) >= 2 and base is not None and last is not None:
            cells.append(f"{_show(key, last - base, signed=True)}")
        else:
            cells.append("")
        found.append(cells)
    return found


def _show(key: str, value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    if key == "trades":
        return f"{value:+.0f}" if signed else f"{value:.0f}"
    if key == "win_rate":
        return f"{value * 100:+.1f}%" if signed else f"{value * 100:.1f}%"
    return f"{value:+,.2f}" if signed else f"{value:,.2f}"
