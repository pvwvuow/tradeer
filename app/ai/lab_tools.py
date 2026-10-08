"""The AI Lab agent's read-only tools (docs/AI_LAB_AGENT.md section 3, Phase 18c).

Each tool answers in short plain text: the agent reads it, the user sees only the step
line. No tool returns the account number, a login, a name or a key, and none can change
anything. Part 2 adds the signals with their decision traces (why a signal was filtered
out) and the saved logs: every log line is masked again and long numbers (tickets,
logins) are hidden before the AI sees it.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.ai.agent import Tool
from app.analytics.trades import TradeRecord
from app.core.strategy_settings import StrategySettings
from app.domain.signals import SignalRecord
from app.observability.log_reader import entry_time
from app.observability.masking import MASKER
from app.strategies.registry import STRATEGIES, create_strategy

DAY = 86_400
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
GROUPS = ("strategy", "symbol", "session", "weekday", "hour", "exit_reason", "mode")
TOO_FEW = 30
HOUR = 3600
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LONG_NUMBER = re.compile(r"\b\d{7,}\b")
DIGITS = re.compile(r"\d+")
MESSAGE_CHARS = 240


def _no_runs() -> Sequence[tuple[str, Mapping[str, Any]]]:
    return ()


def _no_balance() -> float:
    return math.nan


def _no_text() -> str:
    return ""


def _no_signals() -> Sequence[SignalRecord]:
    return ()


def _no_logs(since: float) -> Sequence[Mapping[str, Any]]:
    return ()


@dataclass(frozen=True)
class LabData:
    trades: Callable[[], Sequence[TradeRecord]]
    settings: Callable[[], StrategySettings]
    mode: Callable[[], str] = field(default=_no_text)
    balance: Callable[[], float] = field(default=_no_balance)
    currency: Callable[[], str] = field(default=_no_text)
    backtests: Callable[[], Sequence[tuple[str, Mapping[str, Any]]]] = field(default=_no_runs)
    now: Callable[[], float] = field(default=time.time)
    signals: Callable[[], Sequence[SignalRecord]] = field(default=_no_signals)
    log_entries: Callable[[float], Sequence[Mapping[str, Any]]] = field(default=_no_logs)


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


def private(text: str) -> str:
    """A log text safe for the AI: secrets masked, long numbers (tickets, logins) hidden."""
    return MASKER.mask(LONG_NUMBER.sub("<number>", text))


def failed_checks(record: SignalRecord) -> list[str]:
    return [step.name for step in record.trace.steps if step.passed is False]


def _counts(counter: Counter[str], top: int) -> str:
    return ", ".join(f"{name} {count}" for name, count in counter.most_common(top))


def signal_rows(data: LabData, args: Mapping[str, Any]) -> str:
    days = _int(args, "days", 7, 0, 365)
    limit = _int(args, "limit", 30, 1, 100)
    strategy, symbol = _text(args, "strategy"), _text(args, "symbol").upper()
    state = _text(args, "state").upper()
    start = data.now() - days * DAY if days else 0.0
    found = [
        record
        for record in data.signals()
        if record.signal.created_at >= start
        and (not strategy or record.signal.strategy == strategy)
        and (not symbol or record.signal.symbol.upper() == symbol)
        and (not state or record.signal.state.value == state)
    ]
    if not found:
        return "No signals match."
    found.sort(key=lambda record: record.signal.created_at, reverse=True)
    states = Counter(record.signal.state.value for record in found)
    reasons = Counter(name for record in found for name in failed_checks(record))
    lines = [f"{len(found)} signals: {_counts(states, 12)}."]
    if reasons:
        lines.append(f"Failed checks (how often): {_counts(reasons, 10)}.")
    lines.append(f"Newest first, at most {limit}; id = the first 8 characters:")
    lines.append("bar UTC | id | symbol | side | strategy | state | entry / SL / TP | failed")
    for record in found[:limit]:
        signal = record.signal
        prices = f"{signal.price(signal.entry)} / {signal.price(signal.sl)} / "
        prices += signal.price(signal.tp)
        lines.append(
            " | ".join(
                (
                    _utc(signal.bar_time),
                    signal.id[:8],
                    signal.symbol,
                    signal.direction.value,
                    signal.strategy,
                    signal.state.value,
                    prices,
                    "; ".join(failed_checks(record)) or "-",
                ),
            ),
        )
    return "\n".join(lines)


def signal_trace(data: LabData, args: Mapping[str, Any]) -> str:
    wanted = _text(args, "id").lower()
    if len(wanted) < 4:
        return "Give at least the first 4 characters of the id from the signals tool."
    matches = [record for record in data.signals() if record.signal.id.startswith(wanted)]
    if not matches:
        return f"No signal with an id starting {wanted}."
    if len(matches) > 1:
        return f"{len(matches)} signals start with {wanted}; give more characters."
    record = matches[0]
    signal = record.signal
    lines = [
        f"{signal.summary()} [{signal.strategy} {signal.strategy_version}]",
        f"Bar {_utc(signal.bar_time)} UTC {signal.timeframe}; state {signal.state.value}.",
        f"Strategy reason: {signal.reason}",
    ]
    if record.reject_reason:
        lines.append(f"Not traded because: {record.reject_reason}")
    if record.volume is not None:
        lines.append(f"Size: {record.volume:g} lots")
    lines.append("State changes:")
    lines += [f"  {change.text()}" for change in signal.history]
    lines.append("Decision trace (check = value (limit). detail):")
    lines += [f"  {private(line)}" for line in record.trace.lines()]
    return "\n".join(lines)


def _rank(level: object) -> int:
    name = str(level or "").upper()
    return LEVELS.index(name) if name in LEVELS else 1


def _recent_entries(data: LabData, hours: int) -> list[Mapping[str, Any]]:
    since = data.now() - hours * HOUR
    found: list[Mapping[str, Any]] = []
    for entry in data.log_entries(since):
        moment = entry_time(entry)
        if moment is None or moment >= since:
            found.append(entry)
    return found


def _message(entry: Mapping[str, Any]) -> str:
    text = str(entry.get("message") or "")
    first = text.splitlines()[0] if text else ""
    return private(first[:MESSAGE_CHARS])


def log_lines(data: LabData, args: Mapping[str, Any]) -> str:
    hours = _int(args, "hours", 24, 1, 24 * 14)
    limit = _int(args, "limit", 60, 1, 200)
    level = _text(args, "level").upper() or "WARNING"
    if level not in LEVELS:
        return f"level must be one of: {', '.join(LEVELS)}."
    contains = _text(args, "contains").lower()
    floor = LEVELS.index(level)
    found = [
        entry
        for entry in _recent_entries(data, hours)
        if _rank(entry.get("level")) >= floor
        and (not contains or contains in str(entry.get("message") or "").lower())
    ]
    if not found:
        return f"No log lines at {level} or above in the last {hours} h."
    shown = found[-limit:]
    lines = [f"{len(found)} lines at {level} or above in the last {hours} h; the newest:"]
    for entry in shown:
        moment = str(entry.get("time") or "")[:19].replace("T", " ")
        level_name = str(entry.get("level") or "")
        category = str(entry.get("category") or "")
        lines.append(f"{moment} {level_name} {category}: {_message(entry)}")
    return "\n".join(lines)


def log_summary(data: LabData, args: Mapping[str, Any]) -> str:
    hours = _int(args, "hours", 24, 1, 24 * 14)
    entries = _recent_entries(data, hours)
    if not entries:
        return f"No log lines in the last {hours} h (the app may not have run)."
    levels = Counter(str(entry.get("level") or "?").upper() for entry in entries)
    serious = [entry for entry in entries if _rank(entry.get("level")) >= LEVELS.index("WARNING")]
    repeats = Counter(DIGITS.sub("#", _message(entry)) for entry in serious)
    lines = [f"Last {hours} h: {len(entries)} log lines ({_counts(levels, 5)})."]
    if repeats:
        lines.append("Most frequent warnings and errors (numbers shown as #):")
        lines += [f"  {count} x {text}" for text, count in repeats.most_common(10)]
    else:
        lines.append("No warnings or errors.")
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
        Tool(
            "signals",
            "signals with their state (traded, filtered out, rejected by risk, expired) and "
            "which checks failed; the most common failed checks first",
            "strategy?, symbol?, state? (e.g. FILTERED_OUT), days=7, limit=30",
            bind(signal_rows),
            "Read the signals",
        ),
        Tool(
            "signal",
            "one signal's full decision trace: every check with its value and limit",
            "id (the first 8 characters from the signals tool)",
            bind(signal_trace),
            "Opened a decision trace",
        ),
        Tool(
            "logs",
            "the app's saved log lines, masked, newest last",
            f"level=WARNING ({'|'.join(LEVELS)}), contains?, hours=24, limit=60",
            bind(log_lines),
            "Read the logs",
        ),
        Tool(
            "log_summary",
            "how many log lines per level and the most frequent warnings and errors: the "
            "first thing to check when something seems wrong with the app",
            "hours=24",
            bind(log_summary),
            "Checked the app's health in the logs",
        ),
    ]
