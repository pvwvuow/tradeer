"""The AI Lab agent's read-only tools, part 1 (docs/AI_LAB_AGENT.md section 3).

Each tool answers in short plain text: the agent reads it, the user sees only the step
line. No tool returns the account number, a login, a name or a key, and none can change
anything.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.ai.agent import Tool
from app.analytics.trades import TradeRecord
from app.core.strategy_settings import StrategySettings
from app.strategies.registry import STRATEGIES, create_strategy

DAY = 86_400
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
GROUPS = ("strategy", "symbol", "session", "weekday", "hour", "exit_reason", "mode")
TOO_FEW = 30


def _no_runs() -> Sequence[tuple[str, Mapping[str, Any]]]:
    return ()


def _no_balance() -> float:
    return math.nan


def _no_text() -> str:
    return ""


@dataclass(frozen=True)
class LabData:
    trades: Callable[[], Sequence[TradeRecord]]
    settings: Callable[[], StrategySettings]
    mode: Callable[[], str] = field(default=_no_text)
    balance: Callable[[], float] = field(default=_no_balance)
    currency: Callable[[], str] = field(default=_no_text)
    backtests: Callable[[], Sequence[tuple[str, Mapping[str, Any]]]] = field(default=_no_runs)
    now: Callable[[], float] = field(default=time.time)


def _int(args: Mapping[str, Any], name: str, default: int, low: int, high: int) -> int:
    value = args.get(name, default)
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high)


def _text(args: Mapping[str, Any], name: str) -> str:
    value = args.get(name)
    return value.strip() if isinstance(value, str) else ""


def _utc(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M")


def _r(value: float | None) -> str:
    return f"{value:+.2f}R" if value is not None else "?R"


def recent(trades: Sequence[TradeRecord], now: float, days: int) -> list[TradeRecord]:
    """Closed in the last `days` days (0 = all)."""
    if days <= 0:
        return list(trades)
    start = now - days * DAY
    return [trade for trade in trades if trade.close_time >= start]


def summary(trades: Sequence[TradeRecord]) -> str:
    """Trades, win rate, net, average R and profit factor in one line."""
    if not trades:
        return "0 trades"
    wins = [trade for trade in trades if trade.win]
    net = sum(trade.net_profit for trade in trades)
    gained = sum(trade.net_profit for trade in trades if trade.net_profit > 0)
    lost = -sum(trade.net_profit for trade in trades if trade.net_profit < 0)
    rs = [trade.r_multiple for trade in trades if trade.r_multiple is not None]
    text = f"{len(trades)} trades, win rate {100 * len(wins) / len(trades):.0f}%, net {net:+.2f}"
    if rs:
        text += f", average {sum(rs) / len(rs):+.2f}R"
    if lost > 0:
        text += f", profit factor {gained / lost:.2f}"
    if len(trades) < TOO_FEW:
        text += " (too few to judge)"
    return text


def group_key(trade: TradeRecord, by: str) -> str:
    moment = datetime.fromtimestamp(trade.open_time, UTC)
    if by == "weekday":
        return WEEKDAYS[moment.weekday()]
    if by == "hour":
        return f"{moment.hour:02d} UTC"
    value = getattr(trade, by, "")
    return str(value) or "none"


def overview(data: LabData, args: Mapping[str, Any]) -> str:
    now = data.now()
    trades = list(data.trades())
    settings = data.settings()
    balance = data.balance()
    lines = [
        f"Now: {_utc(now)} UTC. Mode: {data.mode() or 'unknown'}.",
        f"Balance: {balance:,.2f} {data.currency()}" if math.isfinite(balance) else "Balance: ?",
        f"Enabled strategies: {', '.join(settings.enabled()) or 'none'}.",
        f"All time: {summary(trades)}.",
        f"Last 7 days: {summary(recent(trades, now, 7))}.",
        f"Last 30 days: {summary(recent(trades, now, 30))}.",
    ]
    return "\n".join(lines)


def trade_rows(data: LabData, args: Mapping[str, Any]) -> str:
    days = _int(args, "days", 30, 0, 3650)
    limit = _int(args, "limit", 40, 1, 200)
    strategy, symbol = _text(args, "strategy"), _text(args, "symbol").upper()
    mode, result = _text(args, "mode").lower(), _text(args, "result").lower()
    found = [
        trade
        for trade in recent(data.trades(), data.now(), days)
        if (not strategy or trade.strategy == strategy)
        and (not symbol or trade.symbol.upper() == symbol)
        and (not mode or trade.mode == mode)
        and (result not in ("win", "loss") or (trade.win if result == "win" else trade.loss))
    ]
    found.sort(key=lambda trade: trade.close_time, reverse=True)
    head = f"{summary(found)}. Newest first, at most {limit}:"
    rows = ["closed UTC | symbol | side | strategy | mode | R | net | exit | reason"]
    for trade in found[:limit]:
        rows.append(
            " | ".join(
                (
                    _utc(trade.close_time),
                    trade.symbol,
                    trade.direction,
                    trade.strategy,
                    trade.mode,
                    _r(trade.r_multiple),
                    f"{trade.net_profit:+.2f}",
                    trade.exit_reason or "?",
                    trade.reason[:80],
                ),
            ),
        )
    return "\n".join([head, *rows])


def stats(data: LabData, args: Mapping[str, Any]) -> str:
    by = _text(args, "group_by") or "strategy"
    if by not in GROUPS:
        return f"group_by must be one of: {', '.join(GROUPS)}."
    days = _int(args, "days", 90, 0, 3650)
    strategy = _text(args, "strategy")
    trades = [
        trade
        for trade in recent(data.trades(), data.now(), days)
        if not strategy or trade.strategy == strategy
    ]
    groups: dict[str, list[TradeRecord]] = defaultdict(list)
    for trade in trades:
        groups[group_key(trade, by)].append(trade)
    scope = f"last {days} days" if days else "all time"
    lines = [f"By {by}, {scope}: {summary(trades)}."]
    for key in sorted(groups, key=lambda name: -len(groups[name])):
        lines.append(f"- {key}: {summary(groups[key])}")
    return "\n".join(lines)


def schema_lines(model: type[BaseModel], values: Mapping[str, Any]) -> list[str]:
    """One line per setting: the value now, the default, the limits and what it does."""
    properties = model.model_json_schema().get("properties", {})
    lines: list[str] = []
    for name, info in properties.items():
        if not isinstance(info, dict):
            continue
        low = info.get("minimum", info.get("exclusiveMinimum", ""))
        high = info.get("maximum", info.get("exclusiveMaximum", ""))
        limits = f", {low} to {high}" if low != "" or high != "" else ""
        now = values.get(name, info.get("default"))
        about = info.get("description", "")
        lines.append(f"  {name} = {now} (default {info.get('default')}{limits}) {about}")
    return lines


def strategy_settings(data: LabData, args: Mapping[str, Any]) -> str:
    wanted = _text(args, "strategy")
    settings = data.settings()
    names = [wanted] if wanted else list(STRATEGIES)
    if wanted and wanted not in STRATEGIES:
        return f"Unknown strategy {wanted}. Strategies: {', '.join(STRATEGIES)}."
    lines: list[str] = []
    for name in names:
        strategy = create_strategy(name)
        entry = settings.entry(name)
        state = "on" if entry.enabled else "off"
        lines.append(f"{name} {strategy.version} ({state}): {strategy.description}")
        lines += schema_lines(strategy.params_model, entry.params)
    return "\n".join(lines)


def filter_settings(data: LabData, args: Mapping[str, Any]) -> str:
    filters = data.settings().filters
    lines = ["Signal filters (every strategy):"]
    lines += schema_lines(type(filters), filters.model_dump(mode="json"))
    return "\n".join(lines)


def backtests(data: LabData, args: Mapping[str, Any]) -> str:
    limit = _int(args, "limit", 10, 1, 50)
    runs = list(data.backtests())[:limit]
    if not runs:
        return "No saved backtests."
    lines: list[str] = []
    for label, metrics in runs:
        text = json.dumps(dict(metrics), default=str, sort_keys=True)
        lines.append(f"- {label}: {text[:400]}")
    return "\n".join(lines)


def lab_tools(data: LabData) -> list[Tool]:
    def bind(run: Callable[[LabData, Mapping[str, Any]], str]) -> Callable[..., str]:
        def call(args: Mapping[str, Any]) -> str:
            return run(data, args)

        return call

    return [
        Tool(
            "overview",
            "mode, balance, enabled strategies and the results of all time, 7 and 30 days",
            "",
            bind(overview),
            "Read the overview",
        ),
        Tool(
            "trades",
            "closed trades, newest first, with R, net, exit reason and the signal reason",
            "strategy?, symbol?, mode? (live|paper), result? (win|loss), days=30, limit=40",
            bind(trade_rows),
            "Read the trades",
        ),
        Tool(
            "stats",
            "win rate, net, average R and profit factor per group",
            f"group_by ({'|'.join(GROUPS)}), days=90, strategy?",
            bind(stats),
            "Worked out the statistics",
        ),
        Tool(
            "strategy_settings",
            "each strategy: on or off, its rules and every parameter with its limits",
            "strategy?",
            bind(strategy_settings),
            "Read the strategy settings",
        ),
        Tool(
            "filter_settings",
            "the signal filters every strategy passes (spread, sessions, news, losses)",
            "",
            bind(filter_settings),
            "Read the signal filters",
        ),
        Tool(
            "backtests",
            "the saved backtest runs and their metrics",
            "limit=10",
            bind(backtests),
            "Read the saved backtests",
        ),
    ]
