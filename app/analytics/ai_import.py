"""AI suggestions back into the app (spec C13): read the JSON an AI answered, check it
against each strategy's parameter schema, show the difference, compare it in a backtest
and record it as a new config version.

Nothing here calls an AI or trades. The suggestion is only text the user pasted; it becomes
settings only after a backtest against the current values and only in Paper or
Analysis-only mode (Auto on a real account needs the Go-Live gate of Phase 13b).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.backtest.metrics import Metrics
from app.backtest.service import strategy_params
from app.core.strategy_settings import StrategyEntry, StrategySettings
from app.domain.modes import OperatingMode
from app.storage.ids import new_id
from app.storage.signal_store import config_id, iso_time
from app.strategies.registry import STRATEGIES, create_strategy

CREATED_BY = "ai_suggestion"
ACTION = "activate_ai_suggestion"
MIN_TRADES = 30
DRAWDOWN_SLACK = 1.25  # the suggestion may go this much deeper (plus 1 point) and still pass
FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
NO_CHANGES = (
    'No changes found. Paste the JSON block of the answer: {"changes": [{"strategy": ..., '
    '"params": {...}, "reason": ...}]}. An empty list means the AI saw nothing to change.'
)
ONE_TEST = (
    "One symbol and one period: a better backtest can still be luck. Watch it in Paper "
    "before trusting it."
)


@dataclass(frozen=True)
class ParamChange:
    name: str
    old: Any
    new: Any


@dataclass(frozen=True)
class StrategyChange:
    strategy: str
    changes: tuple[ParamChange, ...]
    params: Mapping[str, Any]  # every parameter after the change (validated)
    old_params: Mapping[str, Any]  # every parameter now
    reason: str
    expected_impact: str = ""


@dataclass(frozen=True)
class Suggestion:
    changes: tuple[StrategyChange, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return bool(self.changes) and not self.problems

    @property
    def strategies(self) -> list[str]:
        return [change.strategy for change in self.changes]


def _payload(text: str) -> Any:
    """The JSON of the answer: a fenced block if there is one, else the outermost object."""
    found = FENCE.search(text)
    body = (found.group(1) if found else text).strip()
    if not body:
        raise ValueError("the text is empty")
    try:
        return json.loads(body)
    except ValueError:
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(body[start : end + 1])


def _errors(error: ValidationError) -> str:
    parts: list[str] = []
    for detail in error.errors()[:3]:
        where = ".".join(str(part) for part in detail["loc"]) or "params"
        parts.append(f"{where}: {detail['msg']}")
    return "; ".join(parts)


def _change(
    number: int,
    item: Any,
    settings: StrategySettings,
    seen: set[str],
) -> tuple[StrategyChange | None, str]:
    where = f"Change {number}"
    if not isinstance(item, dict):
        return None, f"{where}: expected an object with strategy, params and reason"
    name = str(item.get("strategy") or "")
    if name not in STRATEGIES:
        known = ", ".join(STRATEGIES)
        return None, f"{where}: unknown strategy {name or '(missing)'} (known: {known})"
    where = f"{where} ({name})"
    if name in seen:
        return None, f"{where}: the strategy appears twice; put its changes in one entry"
    params = item.get("params")
    if not isinstance(params, dict) or not params:
        return None, f"{where}: params must be an object with at least one parameter"
    reason = str(item.get("reason") or "").strip()
    if not reason:
        return None, f"{where}: every change needs a reason"
    model = STRATEGIES[name].params_model
    unknown = sorted(str(key) for key in params if key not in model.model_fields)
    if unknown:
        return None, f"{where}: unknown parameter(s) {', '.join(unknown)}"
    old = strategy_params(settings, name)
    try:
        validated = model.model_validate({**old, **params})
    except ValidationError as error:
        return None, f"{where}: {_errors(error)}"
    new = dict(validated.model_dump(mode="json"))
    diffs = tuple(
        ParamChange(key, old.get(key), value) for key, value in new.items() if value != old.get(key)
    )
    if not diffs:
        return None, f"{where}: the suggested values are the current ones"
    impact = str(item.get("expected_impact") or "").strip()
    return StrategyChange(name, diffs, new, old, reason, impact), ""


def parse_suggestion(text: str, settings: StrategySettings) -> Suggestion:
    """Every change that passes the schema, and a plain problem for each one that does not.

    Accepted shapes: {"changes": [...]}, a bare list of changes, or one change object.
    """
    try:
        payload = _payload(text)
    except ValueError as error:
        return Suggestion(problems=(f"Not valid JSON: {error}",))
    items = payload.get("changes") if isinstance(payload, dict) else payload
    if isinstance(payload, dict) and items is None and "strategy" in payload:
        items = [payload]
    if not isinstance(items, list) or not items:
        return Suggestion(problems=(NO_CHANGES,))
    changes: list[StrategyChange] = []
    problems: list[str] = []
    seen: set[str] = set()
    for number, item in enumerate(items, start=1):
        found, problem = _change(number, item, settings, seen)
        if found is None:
            problems.append(problem)
            continue
        changes.append(found)
        seen.add(found.strategy)
    return Suggestion(tuple(changes), tuple(problems))


def diff_rows(suggestion: Suggestion) -> list[list[str]]:
    """Strategy, parameter, now, suggested: one row per changed parameter."""
    return [
        [change.strategy, item.name, _value(item.old), _value(item.new)]
        for change in suggestion.changes
        for item in change.changes
    ]


def _value(value: Any) -> str:
    if value is None:
        return "(none)"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def apply_suggestion(settings: StrategySettings, suggestion: Suggestion) -> StrategySettings:
    """The settings with the suggested params; each strategy stays on or off as it was."""
    updated = settings
    for change in suggestion.changes:
        enabled = settings.entry(change.strategy).enabled
        entry = StrategyEntry(enabled=enabled, params=dict(change.params))
        updated = updated.with_entry(change.strategy, entry)
    return updated


def _hash(strategy: str, params: Mapping[str, Any]) -> str:
    return create_strategy(strategy, dict(params)).params_hash


def config_rows(suggestion: Suggestion, account: str | None) -> list[dict[str, Any]]:
    """`strategy_configs` rows: a new version per strategy, linked to the one it replaces."""
    rows: list[dict[str, Any]] = []
    for change in suggestion.changes:
        version = STRATEGIES[change.strategy].version
        new_hash = _hash(change.strategy, change.params)
        old_hash = _hash(change.strategy, change.old_params)
        notes = change.reason
        if change.expected_impact:
            notes += f" Expected impact: {change.expected_impact}"
        rows.append(
            {
                "id": config_id(change.strategy, version, new_hash),
                "account_id": account,
                "strategy": change.strategy,
                "version": version,
                "params_json": dict(change.params),
                "params_hash": new_hash,
                "created_by": CREATED_BY,
                "parent_config_id": config_id(change.strategy, version, old_hash),
                "notes": notes,
                "is_active": True,
            },
        )
    return rows


def audit_row(
    suggestion: Suggestion,
    account: str | None,
    now: float,
    verdict: Verdict | None = None,
) -> dict[str, Any]:
    """The `audit_log` row of an activation: the params before and after, and why."""
    after: dict[str, Any] = {
        change.strategy: {
            "params": dict(change.params),
            "reason": change.reason,
            "expected_impact": change.expected_impact,
        }
        for change in suggestion.changes
    }
    if verdict is not None:
        after["backtest"] = {"better": verdict.better, "lines": list(verdict.lines)}
    return {
        "id": new_id(),
        "account_id": account,
        "time": iso_time(now),
        "source": CREATED_BY,
        "action": ACTION,
        "before_json": {change.strategy: dict(change.old_params) for change in suggestion.changes},
        "after_json": after,
    }


def activation_block(mode: OperatingMode) -> str:
    """Why a suggestion cannot be activated in this mode, or "" when it can."""
    if not mode.places_real_orders:
        return ""
    return (
        f"The operating mode is {mode.label}, which places real orders. Switch to Paper first: "
        "AI suggestions never go straight to real money (Auto on a real account will need "
        "the Go-Live gate)."
    )


@dataclass(frozen=True)
class RunSummary:
    trades: int
    win_rate: float
    net_profit: float
    expectancy_r: float | None
    profit_factor: float | None
    max_drawdown_percent: float

    @classmethod
    def from_metrics(cls, metrics: Metrics) -> RunSummary:
        return cls(
            trades=metrics.trades,
            win_rate=metrics.win_rate,
            net_profit=metrics.net_profit,
            expectancy_r=metrics.expectancy_r,
            profit_factor=metrics.profit_factor,
            max_drawdown_percent=metrics.max_drawdown.depth_percent,
        )


@dataclass(frozen=True)
class Verdict:
    better: bool
    lines: tuple[str, ...]


def _number(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    return f"{value:,.{digits}f}{suffix}"


def summary_rows(current: RunSummary, proposed: RunSummary) -> list[list[str]]:
    """Metric, current, suggested."""
    return [
        ["Trades", str(current.trades), str(proposed.trades)],
        [
            "Win rate",
            _number(current.win_rate * 100, 1, "%"),
            _number(proposed.win_rate * 100, 1, "%"),
        ],
        ["Net profit", _number(current.net_profit), _number(proposed.net_profit)],
        ["Expectancy R", _number(current.expectancy_r, 3), _number(proposed.expectancy_r, 3)],
        ["Profit factor", _number(current.profit_factor), _number(proposed.profit_factor)],
        [
            "Max drawdown",
            _number(current.max_drawdown_percent, 2, "%"),
            _number(proposed.max_drawdown_percent, 2, "%"),
        ],
    ]


def compare_runs(
    current: RunSummary,
    proposed: RunSummary,
    minimum_trades: int = MIN_TRADES,
) -> Verdict:
    """Better means: enough trades, a higher expectancy and a drawdown not much deeper."""
    lines: list[str] = []
    better = True
    if proposed.trades < minimum_trades:
        better = False
        lines.append(
            f"Only {proposed.trades} trades with the suggestion: too few to judge "
            f"(at least {minimum_trades}).",
        )
    new_r, now_r = proposed.expectancy_r, current.expectancy_r
    improved = new_r is not None and (new_r > now_r if now_r is not None else new_r > 0)
    word = "higher" if improved else "not higher"
    lines.append(
        f"Expectancy {_number(new_r, 3)} R per trade vs {_number(now_r, 3)} R now: {word}.",
    )
    better = better and improved
    limit = current.max_drawdown_percent * DRAWDOWN_SLACK + 1.0
    deeper = proposed.max_drawdown_percent > limit
    lines.append(
        f"Max drawdown {proposed.max_drawdown_percent:.2f}% vs "
        f"{current.max_drawdown_percent:.2f}% now: {'much deeper' if deeper else 'acceptable'}.",
    )
    better = better and not deeper
    head = (
        "The suggestion beat the current settings in this backtest."
        if better
        else "The suggestion did not clearly beat the current settings."
    )
    return Verdict(better, (head, *lines, ONE_TEST))
