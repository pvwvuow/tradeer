"""CSV export of the trades and the breakdowns (spec C11 "Export CSV")."""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import asdict, fields
from datetime import UTC, datetime
from pathlib import Path

from app.analytics.breakdowns import GroupStats, label
from app.analytics.trades import TradeRecord

TIME_FIELDS = ("open_time", "close_time")
GROUP_COLUMNS = ("trades", "wins", "win_rate", "net_profit", "expectancy_r", "profit_factor")


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M:%S") if seconds else ""


def trades_csv(trades: Sequence[TradeRecord]) -> str:
    names = [f.name for f in fields(TradeRecord)]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(names)
    for trade in trades:
        data = asdict(trade)
        writer.writerow(
            [_iso(data[name]) if name in TIME_FIELDS else _cell(data[name]) for name in names],
        )
    return buffer.getvalue()


def groups_csv(title: str, groups: Sequence[GroupStats]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([title, *GROUP_COLUMNS])
    for item in groups:
        writer.writerow(
            [
                label(item.key),
                item.trades,
                item.wins,
                f"{item.win_rate:.4f}",
                f"{item.net_profit:.2f}",
                _cell(item.expectancy_r),
                _cell(item.profit_factor),
            ],
        )
    return buffer.getvalue()


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path
