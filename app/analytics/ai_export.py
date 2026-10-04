"""The export for an outside AI (spec C13): every closed trade as CSV and JSON, and one
`report.md` with an analysis prompt, the statistics, breakdowns, probability calibration,
MFE/MAE, costs, rejected signals, recent backtests and each strategy's parameter schema.

The user gives these files to the AI of their choice. Its answer comes back through
`app.analytics.ai_import`, which accepts only the JSON shape the prompt asks for.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.analytics import charts
from app.analytics.breakdowns import KEYS, breakdown, label, probability_bucket
from app.analytics.export import trades_csv, write_text
from app.analytics.stats import clean, compute_stats, mean, stat_rows
from app.analytics.trades import Database, TradeFilter, TradeRecord
from app.backtest.service import strategy_params
from app.core.strategy_settings import StrategySettings
from app.domain.modes import OperatingMode
from app.strategies.registry import STRATEGIES

DAY = 86_400
CSV_NAME = "trades_full.csv"
JSON_NAME = "trades_full.json"
REPORT_NAME = "report.md"
REJECTED_LIMIT = 5000
BACKTEST_LIMIT = 5
PROMPT = """# Trading review

You are reviewing an automated MetaTrader 5 trading system. The files are this report,
trades_full.csv and trades_full.json (the same closed trades). Be a skeptical reviewer:

1. Where does it lose money? Strategies, symbols, sessions, hours, weekdays, setups.
2. Is any edge real, or mostly luck: few trades, one good month, big outliers?
3. Are the predicted win probabilities calibrated (see "Probability calibration")?
4. Do costs (spread, commission, swap, slippage) eat the edge?
5. Which parameter changes would help, and what could go wrong with them?

Then end your answer with ONE JSON block in exactly this shape. The app reads only this
block and rejects anything outside the schemas under "Strategy parameters":

```json
{"changes": [{"strategy": "trend_pullback", "params": {"min_adx_h1": 25},
  "reason": "why, with numbers from this report", "expected_impact": "what should change"}]}
```

Rules: only the strategy and parameter names listed below, values inside their limits,
few parameters per change. If the data is too thin to change anything, say so and return
{"changes": []}. The app backtests every suggestion against the current settings and
activates it only in Paper mode.
"""


@dataclass(frozen=True)
class ExportData:
    trades: Sequence[TradeRecord]
    settings: StrategySettings
    mode: OperatingMode
    start_balance: float = 0.0
    currency: str = ""
    backtests: Sequence[tuple[str, Mapping[str, Any]]] = ()
    rejected: Sequence[tuple[str, int]] = ()
    scope: str = "all closed trades"


def select_trades(
    trades: Sequence[TradeRecord],
    now: float,
    *,
    strategy: str = "",
    mode: str = "",
    days: int = 0,
) -> list[TradeRecord]:
    """The trades to export: closed in the last `days` days (0 = all), one strategy or mode."""
    start = now - days * DAY if days > 0 else None
    return TradeFilter(start=start, strategy=strategy, mode=mode).apply(trades)


def _iso(seconds: float) -> str:
    if not seconds:
        return ""
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M:%S")


def trades_json(trades: Sequence[TradeRecord]) -> str:
    """Every field of every trade, with the times also as UTC text."""
    rows: list[dict[str, Any]] = []
    for trade in trades:
        row: dict[str, Any] = clean(asdict(trade))
        row["open_time_utc"] = _iso(trade.open_time)
        row["close_time_utc"] = _iso(trade.close_time)
        rows.append(row)
    return json.dumps(rows, indent=1, ensure_ascii=False)


def rejected_counts(db: Database, limit: int = REJECTED_LIMIT) -> list[tuple[str, int]]:
    """Why the newest signals were rejected, most common first (the text before any ':')."""
    rows = db.query(
        "SELECT reject_reason FROM signals WHERE reject_reason IS NOT NULL "
        "AND reject_reason != '' ORDER BY bar_time DESC LIMIT ?",
        (limit,),
    )
    counts: dict[str, int] = {}
    for row in rows:
        reason = str(row["reject_reason"]).split(":")[0].strip()[:80] or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _number(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    return f"{value:,.{digits}f}{suffix}"


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines += ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows]
    return lines


def calibration(trades: Sequence[TradeRecord]) -> list[tuple[str, int, float, float]]:
    """Per probability bucket: trades, mean predicted probability, actual win rate."""
    buckets: dict[str, list[TradeRecord]] = {}
    for trade in trades:
        if trade.probability is not None:
            buckets.setdefault(probability_bucket(trade.probability), []).append(trade)
    found: list[tuple[str, int, float, float]] = []
    for key, items in sorted(buckets.items()):
        predicted = mean([t.probability for t in items if t.probability is not None]) or 0.0
        actual = sum(1 for t in items if t.win) / len(items)
        found.append((key, len(items), predicted, actual))
    return found


def _data_section(data: ExportData, now: float) -> list[str]:
    trades = data.trades
    first = min((t.close_time for t in trades), default=0.0)
    last = max((t.close_time for t in trades), default=0.0)
    period = f"{_iso(first)[:10]} to {_iso(last)[:10]}" if trades else "no trades"
    return [
        "## Data",
        "",
        f"- Generated: {_iso(now)} UTC",
        f"- Scope: {data.scope}",
        f"- Closed trades: {len(trades)} ({period})",
        f"- Account currency: {data.currency or 'unknown'}",
        f"- Operating mode now: {data.mode.label}",
        "",
    ]


def _stats_section(data: ExportData) -> list[str]:
    stats = compute_stats(data.trades, data.start_balance)
    lines = ["## Statistics", ""]
    lines += [f"- {name}: {value}" for name, value in stat_rows(stats, data.currency)]
    lines += [f"- Warning: {text}" for text in stats.warnings]
    return [*lines, ""]


def _breakdown_section(trades: Sequence[TradeRecord]) -> list[str]:
    lines = ["## Breakdowns", ""]
    headers = ("Group", "Trades", "Win rate", "Net", "Expectancy R", "Profit factor")
    for name in KEYS:
        groups = breakdown(trades, name)
        if not groups:
            continue
        rows = [
            [
                label(item.key),
                str(item.trades),
                f"{item.win_rate * 100:.0f}%",
                f"{item.net_profit:+,.2f}",
                _number(item.expectancy_r, 3),
                _number(item.profit_factor),
            ]
            for item in groups
        ]
        lines += [f"### By {name}", "", *_table(headers, rows), ""]
    if len(lines) == 2:
        lines += ["No closed trades.", ""]
    return lines


def _calibration_section(trades: Sequence[TradeRecord]) -> list[str]:
    lines = ["## Probability calibration", ""]
    found = calibration(trades)
    if not found:
        return [*lines, "No trade has a predicted win probability yet.", ""]
    rows = [
        [key, str(count), f"{predicted * 100:.1f}%", f"{actual * 100:.1f}%"]
        for key, count, predicted, actual in found
    ]
    headers = ("Predicted bucket", "Trades", "Mean predicted", "Actual win rate")
    return [*lines, *_table(headers, rows), ""]


def _excursion_section(trades: Sequence[TradeRecord]) -> list[str]:
    excursions = charts.excursions(trades).lines
    costs = charts.cost_analysis(trades).lines
    lines = ["## Best and worst moves (MFE/MAE)", ""]
    lines += [f"- {text}" for text in excursions] or ["- No MFE/MAE recorded."]
    lines += ["", "## Costs", ""]
    lines += [f"- {text}" for text in costs] or ["- No costs recorded."]
    return [*lines, ""]


def _rejected_section(rejected: Sequence[tuple[str, int]]) -> list[str]:
    lines = ["## Rejected signals (newest first, by reason)", ""]
    if not rejected:
        return [*lines, "None recorded.", ""]
    return [*lines, *(f"- {reason}: {count}" for reason, count in rejected[:20]), ""]


def _backtest_section(data: ExportData) -> list[str]:
    lines = ["## Live and paper vs backtest", ""]
    for mode in ("live", "paper"):
        items = [t for t in data.trades if t.mode == mode]
        if items:
            value = mean([t.r_multiple for t in items if t.r_multiple is not None])
            lines.append(f"- {mode.title()}: {len(items)} trades, {_number(value, 3)} R per trade")
    runs = list(data.backtests)[:BACKTEST_LIMIT]
    if not runs:
        lines.append("- No saved backtest.")
    for name, metrics in runs:
        drawdown = metrics.get("max_drawdown") or {}
        depth = drawdown.get("depth_percent") if isinstance(drawdown, Mapping) else None
        lines.append(
            f"- Backtest {name}: {metrics.get('trades', 0)} trades, "
            f"{_number(metrics.get('expectancy_r'), 3)} R per trade, profit factor "
            f"{_number(metrics.get('profit_factor'))}, max drawdown {_number(depth, 2, '%')}",
        )
    return [*lines, ""]


def _params_section(settings: StrategySettings) -> list[str]:
    lines = ["## Strategy parameters", ""]
    for name, kind in STRATEGIES.items():
        state = "on" if settings.entry(name).enabled else "off"
        current = json.dumps(strategy_params(settings, name), sort_keys=True)
        schema = json.dumps(kind.params_model.model_json_schema(), sort_keys=True)
        lines += [
            f"### {name} ({kind.title}, version {kind.version}, {state})",
            "",
            f"Current params: `{current}`",
            "",
            "Schema (JSON Schema, the limits every suggestion must keep):",
            "",
            "```json",
            schema,
            "```",
            "",
        ]
    return lines


def build_report(data: ExportData, now: float) -> str:
    lines = [
        PROMPT,
        *_data_section(data, now),
        *_stats_section(data),
        *_breakdown_section(data.trades),
        *_calibration_section(data.trades),
        *_excursion_section(data.trades),
        *_rejected_section(data.rejected),
        *_backtest_section(data),
        *_params_section(data.settings),
        "## Not included yet",
        "",
        "- Counterfactuals (what the rejected or skipped signals would have done) are not "
        "computed yet, so do not assume they were profitable.",
        "",
    ]
    return "\n".join(lines)


def write_export(folder: Path, data: ExportData, now: float) -> list[Path]:
    """trades_full.csv, trades_full.json and report.md in `folder`."""
    return [
        write_text(folder / CSV_NAME, trades_csv(data.trades)),
        write_text(folder / JSON_NAME, trades_json(data.trades)),
        write_text(folder / REPORT_NAME, build_report(data, now)),
    ]
