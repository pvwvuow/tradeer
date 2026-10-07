"""Daily and weekly reports (spec C12): P/L, trades, win rate, best and worst trade, rejected
signals by reason, costs, errors and warnings, health issues and anomalies (high slippage,
requotes), and the strategy lab review (each strategy's numbers with its strong and weak
spots). Saved in `daily_reports` (synced) and as a Markdown file on this PC, and optionally
sent to Telegram.

A daily report covers one broker trading day; a weekly report the seven broker days from a
Monday. Both are made once, when the period is over (`due_reports`).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.analytics.lab import review_lines, review_strategies
from app.analytics.stats import compute_stats
from app.analytics.trades import TradeRecord
from app.storage.ids import stable_id
from app.storage.repositories import Store
from app.storage.signal_store import iso_time

REJECTED_STATES = ("FILTERED_OUT", "RISK_REJECTED")
REQUOTE_CODES = (10004, 10020, 10021)  # requote, price changed, off quotes
HIGH_SLIPPAGE_POINTS = 5.0
MANY_REQUOTES = 5
REPORT_FOLDER = "reports"
DAY = 86_400


@dataclass(frozen=True)
class Period:
    kind: str  # "daily" or "weekly"
    first: date  # the broker day (daily) or the Monday (weekly)
    start: float  # UTC seconds
    end: float

    @property
    def label(self) -> str:
        if self.kind == "weekly":
            last = self.first + timedelta(days=6)
            return f"Week {self.first.isoformat()} to {last.isoformat()}"
        return f"Day {self.first.isoformat()}"


def day_period(day: date, offset_seconds: float = 0.0) -> Period:
    """One broker day: midnight in broker time, in UTC seconds."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() - offset_seconds
    return Period("daily", day, start, start + DAY)


def week_period(monday: date, offset_seconds: float = 0.0) -> Period:
    day = day_period(monday, offset_seconds)
    return Period("weekly", monday, day.start, day.start + 7 * DAY)


def due_reports(
    now: float,
    offset_seconds: float,
    done: Callable[[str, date], bool],
) -> list[Period]:
    """The reports of yesterday (broker day) and of last week that were not made yet."""
    today = datetime.fromtimestamp(now + offset_seconds, UTC).date()
    yesterday = today - timedelta(days=1)
    found: list[Period] = []
    if yesterday.weekday() < 5 and not done("daily", yesterday):
        found.append(day_period(yesterday, offset_seconds))
    monday = today - timedelta(days=today.weekday() + 7)
    if not done("weekly", monday):
        found.append(week_period(monday, offset_seconds))
    return found


@dataclass(frozen=True)
class Report:
    period: Period
    account: str
    summary: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    @property
    def id(self) -> str:
        return report_id(self.account, self.period.kind, self.period.first)


def report_id(account: str, kind: str, first: date) -> str:
    return stable_id("report", account, kind, first.isoformat())


def _trade_line(trade: TradeRecord) -> str:
    return f"{trade.symbol} {trade.direction} {trade.net_profit:+,.2f} ({trade.strategy})"


def build_report(
    period: Period,
    account: str,
    trades: Sequence[TradeRecord],
    *,
    rejected: Sequence[tuple[str, int]] = (),
    log_counts: Sequence[tuple[str, int]] = (),
    top_errors: Sequence[str] = (),
    health_issues: Sequence[str] = (),
    requotes: int = 0,
    currency: str = "",
) -> Report:
    """Pure: the numbers and the text of one report."""
    inside = [t for t in trades if period.start <= t.close_time < period.end]
    stats = compute_stats(inside, minimum_trades=0)
    best = max(inside, key=lambda t: t.net_profit, default=None)
    worst = min(inside, key=lambda t: t.net_profit, default=None)
    slipped = [t for t in inside if t.slippage is not None and t.slippage >= HIGH_SLIPPAGE_POINTS]
    anomalies: list[str] = []
    if slipped:
        limit = f"{HIGH_SLIPPAGE_POINTS:g}"
        anomalies.append(f"{len(slipped)} fill(s) with {limit}+ points of slippage")
    if requotes >= MANY_REQUOTES:
        anomalies.append(f"{requotes} requotes or price changes from the broker")
    costs = -(stats.commission + stats.swap + stats.fee)
    reviews = review_strategies(inside)
    summary: dict[str, Any] = {
        "kind": period.kind,
        "label": period.label,
        "start": iso_time(period.start),
        "end": iso_time(period.end),
        "net_profit": stats.net_profit,
        "trades": stats.trades,
        "wins": stats.wins,
        "win_rate": round(stats.win_rate, 4),
        "profit_factor": stats.profit_factor,
        "expectancy_r": stats.expectancy_r,
        "best_trade": _trade_line(best) if best is not None and best.win else "",
        "worst_trade": _trade_line(worst) if worst is not None and worst.loss else "",
        "costs": round(costs, 2),
        "bot_trades": sum(1 for t in inside if t.source == "bot"),
        "manual_trades": sum(1 for t in inside if t.source != "bot"),
        "rejected": dict(rejected),
        "logs": dict(log_counts),
        "top_errors": list(top_errors),
        "health_issues": list(health_issues),
        "anomalies": anomalies,
        "strategies": [review.as_dict() for review in reviews],
    }
    unit = f" {currency}" if currency else ""
    lines = [
        f"# {period.label} report",
        "",
        f"- Net result: {stats.net_profit:+,.2f}{unit} from {stats.trades} closed trade(s)"
        + (f", win rate {stats.win_rate * 100:.0f}%" if stats.trades else ""),
        f"- Costs paid: {costs:,.2f}{unit} (commission, swap and fees)",
    ]
    if summary["best_trade"]:
        lines.append(f"- Best trade: {summary['best_trade']}")
    if summary["worst_trade"]:
        lines.append(f"- Worst trade: {summary['worst_trade']}")
    if rejected:
        reasons = "; ".join(f"{reason} ({count})" for reason, count in rejected[:8])
        lines.append(f"- Rejected signals: {reasons}")
    else:
        lines.append("- Rejected signals: none")
    counts = ", ".join(f"{count} {level.lower()}" for level, count in log_counts) or "none"
    lines.append(f"- Log warnings and errors: {counts}")
    lines.extend(f"  - {text}" for text in top_errors[:5])
    lines.append(f"- Health issues: {'; '.join(health_issues) if health_issues else 'none'}")
    lines.append(f"- Anomalies: {'; '.join(anomalies) if anomalies else 'none'}")
    if reviews:
        lines.append("- By strategy (best expectancy first; + strong, - weak):")
        lines.extend(review_lines(reviews, currency))
    return Report(period, account, summary, "\n".join(lines) + "\n")


class ReportRepository:
    """Reads what a report needs from the database and saves the result."""

    def __init__(self, store: Store, folder: Path | None = None) -> None:
        self.store = store
        self.folder = folder

    def done(self, account: str, kind: str, first: date) -> bool:
        return self.store.get("daily_reports", report_id(account, kind, first)) is not None

    def inputs(self, period: Period) -> dict[str, Any]:
        db = self.store.db
        start, end = iso_time(period.start), iso_time(period.end)
        placeholders = ", ".join("?" for _ in REJECTED_STATES)
        rejected = db.query(
            "SELECT COALESCE(reject_reason, state) AS reason, COUNT(*) AS n FROM signals "
            f"WHERE bar_time >= ? AND bar_time < ? AND state IN ({placeholders}) "
            "GROUP BY reason ORDER BY n DESC",
            (start, end, *REJECTED_STATES),
        )
        levels = db.query(
            "SELECT level, COUNT(*) AS n FROM app_logs WHERE time >= ? AND time < ? "
            "AND level IN ('WARNING', 'ERROR', 'CRITICAL') GROUP BY level ORDER BY level",
            (start, end),
        )
        errors = db.query(
            "SELECT message, COUNT(*) AS n FROM app_logs WHERE time >= ? AND time < ? "
            "AND level IN ('ERROR', 'CRITICAL') GROUP BY message ORDER BY n DESC LIMIT 5",
            (start, end),
        )
        health = db.query(
            "SELECT name, status FROM health_checks WHERE time >= ? AND time < ? "
            "AND status NOT IN ('ok', 'OK', 'unknown') GROUP BY name, status",
            (start, end),
        )
        codes = ", ".join(str(code) for code in REQUOTE_CODES)
        requotes = db.scalar(
            "SELECT COUNT(*) FROM mt5_requests WHERE created_at >= ? AND created_at < ? "
            f"AND retcode IN ({codes})",
            (start, end),
        )
        return {
            "rejected": [(str(r["reason"] or "unknown"), int(r["n"])) for r in rejected],
            "log_counts": [(str(r["level"]), int(r["n"])) for r in levels],
            "top_errors": [f"{str(r['message'])[:160]} ({int(r['n'])}x)" for r in errors],
            "health_issues": [f"{r['name']}: {r['status']}" for r in health],
            "requotes": int(requotes or 0),
        }

    def save(self, report: Report) -> Path | None:
        self.store.upsert(
            "daily_reports",
            {
                "id": report.id,
                "account_id": report.account or None,
                "report_date": report.period.first.isoformat(),
                "summary_json": {**report.summary, "text": report.text},
            },
        )
        if self.folder is None:
            return None
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{report.period.kind}_{report.period.first.isoformat()}.md"
        path.write_text(report.text, encoding="utf-8")
        return path

    def recent(self, limit: int = 60) -> list[dict[str, Any]]:
        rows = self.store.db.query(
            "SELECT report_date, summary_json FROM daily_reports "
            "ORDER BY report_date DESC, updated_at DESC LIMIT ?",
            (limit,),
        )
        found: list[dict[str, Any]] = []
        for row in rows:
            try:
                summary = json.loads(row["summary_json"] or "{}")
            except ValueError:
                summary = {}
            if isinstance(summary, dict):
                found.append({"report_date": str(row["report_date"]), **summary})
        return found
