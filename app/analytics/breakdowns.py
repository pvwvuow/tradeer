"""Breakdowns (spec C11): the trades grouped by symbol, strategy, direction, session, hour,
weekday, month, holding time, probability bucket, config version, bot vs manual and mode."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from app.analytics.stats import clean, mean
from app.analytics.trades import TradeRecord

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
HOLDING = (
    (15 * 60, "1 under 15 min"),
    (3600, "2 15 to 60 min"),
    (4 * 3600, "3 1 to 4 hours"),
    (24 * 3600, "4 4 to 24 hours"),
    (7 * 24 * 3600, "5 1 to 7 days"),
)
PROBABILITY_EDGES = (0.3, 0.4, 0.5, 0.6, 0.7)


@dataclass(frozen=True)
class GroupStats:
    key: str
    trades: int
    wins: int
    win_rate: float
    net_profit: float
    expectancy_r: float | None
    profit_factor: float | None
    average_net: float

    def to_json(self) -> dict[str, Any]:
        found: dict[str, Any] = clean(asdict(self))
        return found


def _utc(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def holding_bucket(seconds: float) -> str:
    for limit, label in HOLDING:
        if seconds < limit:
            return label
    return "6 over 7 days"


def probability_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    low = 0.0
    for edge in PROBABILITY_EDGES:
        if value < edge:
            return f"{low * 100:.0f}-{edge * 100:.0f}%"
        low = edge
    return f"{low * 100:.0f}-100%"


def _weekday(trade: TradeRecord) -> str:
    day = _utc(trade.open_time).weekday()
    return f"{day + 1} {WEEKDAYS[day]}"


KEYS: dict[str, Callable[[TradeRecord], str]] = {
    "Symbol": lambda t: t.symbol or "unknown",
    "Strategy": lambda t: t.strategy,
    "Direction": lambda t: t.direction or "unknown",
    "Session": lambda t: t.session or "none",
    "Hour (UTC)": lambda t: f"{_utc(t.open_time).hour:02d}:00",
    "Weekday": _weekday,
    "Month": lambda t: _utc(t.close_time).strftime("%Y-%m"),
    "Holding time": lambda t: holding_bucket(t.duration),
    "Probability": lambda t: probability_bucket(t.probability),
    "Config": lambda t: t.config[:12] or "none",
    "Bot or manual": lambda t: "bot" if t.source == "bot" else "manual",
    "Mode": lambda t: t.mode,
}


def group_stats(key: str, items: Sequence[TradeRecord]) -> GroupStats:
    wins = [t for t in items if t.win]
    gross_profit = sum(t.net_profit for t in wins)
    gross_loss = sum(t.net_profit for t in items if t.loss)
    net = sum(t.net_profit for t in items)
    return GroupStats(
        key=key,
        trades=len(items),
        wins=len(wins),
        win_rate=len(wins) / len(items) if items else 0.0,
        net_profit=round(net, 2),
        expectancy_r=mean([t.r_multiple for t in items if t.r_multiple is not None]),
        profit_factor=gross_profit / -gross_loss if gross_loss < 0 else None,
        average_net=net / len(items) if items else 0.0,
    )


def breakdown(trades: Sequence[TradeRecord], by: str) -> list[GroupStats]:
    """One row per value of the chosen key, sorted by the key (numbered labels sort right)."""
    key = KEYS[by]
    buckets: dict[str, list[TradeRecord]] = {}
    for trade in trades:
        buckets.setdefault(key(trade), []).append(trade)
    return [group_stats(name, items) for name, items in sorted(buckets.items())]


def label(key: str) -> str:
    """A bucket label without its sort number ("2 15 to 60 min" -> "15 to 60 min")."""
    head, _, rest = key.partition(" ")
    return rest if head.isdigit() and rest else key
