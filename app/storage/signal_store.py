"""Signals, their decision traces and strategy settings in the database (spec E2).

One transaction writes the `strategy_configs` row, the `signals` row and the
`decision_traces` row, so a signal is never saved without its trace. Ids are stable:
saving the same signal again only updates it (and uploads only what changed).
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.domain.probability import BASELINE_MIN_SAMPLES, ProbabilityEstimate
from app.domain.signals import (
    Direction,
    OrderType,
    Signal,
    SignalRecord,
    SignalState,
    StateChange,
)
from app.observability.decision_trace import DecisionTrace
from app.storage.ids import stable_id
from app.storage.repositories import Store
from app.storage.sqlite_db import iso_utc

RECENT_LIMIT = 200
RESULTS_LIMIT = 500


def config_id(strategy: str, version: str, params_hash: str) -> str:
    return stable_id("strategy_config", strategy, version, params_hash)


def trace_row_id(signal_id: str) -> str:
    return stable_id("trace", signal_id)


def iso_time(seconds: float) -> str:
    return iso_utc(datetime.fromtimestamp(seconds, UTC))


def epoch(text: object) -> float:
    if not isinstance(text, str) or not text:
        return 0.0
    return datetime.fromisoformat(text).timestamp()


def _finite(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return float(value)


def _number(value: object, default: float = math.nan) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return float(value)


def _json(value: object, default: Any) -> Any:
    if not isinstance(value, str) or not value:
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class TradeResult:
    symbol: str
    outcome: str  # win, loss, breakeven
    close_time: float  # UTC seconds


class SignalRepository:
    def __init__(self, store: Store) -> None:
        self.store = store

    # Writes ---------------------------------------------------------------------------
    def save(
        self,
        record: SignalRecord,
        params: Mapping[str, Any],
        account: str | None = None,
    ) -> bool:
        """The config, the signal and its trace in one transaction. False if nothing changed."""
        signal = record.signal
        default = config_id(signal.strategy, signal.strategy_version, signal.params_hash)
        config = {
            "id": signal.config_id or default,
            "account_id": account,
            "strategy": signal.strategy,
            "version": signal.strategy_version,
            "params_json": dict(params),
            "params_hash": signal.params_hash,
            "created_by": "user",
            "is_active": True,
        }
        with self.store.db.transaction() as connection:
            self.store.upsert_in(connection, "strategy_configs", config)
            changed = self.store.upsert_in(connection, "signals", signal_row(record, account))
            trace = trace_row(record, account)
            return self.store.upsert_in(connection, "decision_traces", trace) or changed

    # Reads ----------------------------------------------------------------------------
    def exists(self, signal_id: str) -> bool:
        return self.store.db.scalar("SELECT 1 FROM signals WHERE id = ?", (signal_id,)) is not None

    def recent(self, limit: int = RECENT_LIMIT, account: str | None = None) -> list[SignalRecord]:
        """The newest signals first, with their traces (for the Signals page and restarts)."""
        where = "WHERE s.account_id = ?" if account is not None else ""
        parameters: tuple[Any, ...] = (account, limit) if account is not None else (limit,)
        rows = self.store.db.query(
            "SELECT s.*, d.steps_json AS trace_steps, d.final_decision AS trace_decision "
            "FROM signals s LEFT JOIN decision_traces d ON d.signal_id = s.id "
            f"{where} ORDER BY s.bar_time DESC, s.created_at DESC LIMIT ?",
            parameters,
        )
        records: list[SignalRecord] = []
        for row in rows:
            record = record_from_row(row)
            if record is not None:
                records.append(record)
        return records

    def results(self, strategy: str, limit: int = RESULTS_LIMIT) -> list[TradeResult]:
        """Closed trades that came from this strategy's signals, newest first."""
        rows = self.store.db.query(
            "SELECT t.symbol, t.outcome, t.close_time FROM trades t "
            "JOIN signals s ON s.id = t.signal_id "
            "WHERE s.strategy = ? AND t.outcome IN ('win', 'loss', 'breakeven') "
            "AND t.close_time IS NOT NULL ORDER BY t.close_time DESC LIMIT ?",
            (strategy, limit),
        )
        return [
            TradeResult(str(row["symbol"]), str(row["outcome"]), epoch(row["close_time"]))
            for row in rows
        ]


def signal_row(record: SignalRecord, account: str | None) -> dict[str, Any]:
    signal = record.signal
    estimate = record.probability
    history = [
        {
            "old": change.old.value,
            "new": change.new.value,
            "at": change.at,
            "reason": change.reason,
        }
        for change in signal.history
    ]
    features = {
        "features": dict(signal.features),
        "params_hash": signal.params_hash,
        "digits": signal.digits,
        "created_at": signal.created_at,
        "expires_at": signal.expires_at,
        "probability_samples": estimate.samples,
        "probability_min_samples": estimate.min_samples,
        "history": history,
    }
    return {
        "id": signal.id,
        "account_id": account,
        "bar_time": iso_time(signal.bar_time),
        "symbol": signal.symbol,
        "tf": signal.timeframe,
        "strategy": signal.strategy,
        "strategy_version": signal.strategy_version,
        "config_id": signal.config_id or None,
        "direction": signal.direction.value,
        "order_type": signal.order_type.value,
        "entry": signal.entry,
        "sl": signal.sl,
        "tp": signal.tp,
        "rr": _finite(signal.rr),
        "spread": _finite(record.spread),
        "atr": _finite(record.atr),
        "win_probability": _finite(estimate.value),
        "prob_ci_low": _finite(estimate.low),
        "prob_ci_high": _finite(estimate.high),
        "probability_source": estimate.source,
        "expected_value": _finite(record.expected_value),
        "features_json": features,
        "reason": signal.reason,
        "state": signal.state.value,
        "decision": record.trace.final_decision,
        "reject_reason": record.reject_reason or None,
        "trace_id": record.trace.trace_id,
    }


def trace_row(record: SignalRecord, account: str | None) -> dict[str, Any]:
    trace = record.trace
    return {
        "id": trace_row_id(record.signal.id),
        "account_id": account,
        "signal_id": record.signal.id,
        "trace_id": trace.trace_id,
        "steps_json": trace.steps_json(),
        "final_decision": trace.final_decision,
    }


def _history(raw: object) -> tuple[StateChange, ...]:
    if not isinstance(raw, list):
        return ()
    changes: list[StateChange] = []
    for item in raw:
        try:
            changes.append(
                StateChange(
                    SignalState(item["old"]),
                    SignalState(item["new"]),
                    float(item["at"]),
                    str(item.get("reason", "")),
                ),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(changes)


def record_from_row(row: Mapping[str, Any]) -> SignalRecord | None:
    """A saved signal and its trace, or None when the row cannot be read back."""
    meta = _json(row.get("features_json"), {})
    if not isinstance(meta, dict):
        meta = {}
    try:
        features = meta.get("features", {})
        signal = Signal(
            id=str(row["id"]),
            symbol=str(row["symbol"]),
            timeframe=str(row["tf"]),
            direction=Direction(row["direction"]),
            order_type=OrderType(row["order_type"]),
            entry=float(row["entry"]),
            sl=float(row["sl"]),
            tp=float(row["tp"]),
            reason=str(row.get("reason") or ""),
            strategy=str(row["strategy"]),
            strategy_version=str(row.get("strategy_version") or ""),
            params_hash=str(meta.get("params_hash", "")),
            bar_time=int(epoch(row["bar_time"])),
            created_at=_number(meta.get("created_at"), 0.0),
            expires_at=_number(meta.get("expires_at"), 0.0),
            digits=int(_number(meta.get("digits"), 5.0)),
            config_id=str(row.get("config_id") or ""),
            features=dict(features) if isinstance(features, dict) else {},
            state=SignalState(row["state"]),
            history=_history(meta.get("history")),
        )
    except (KeyError, TypeError, ValueError):
        return None
    steps = _json(row.get("trace_steps"), [])
    trace = DecisionTrace.from_steps(
        signal.id,
        str(row.get("trace_id") or ""),
        steps if isinstance(steps, list) else [],
        str(row.get("trace_decision") or row.get("decision") or ""),
    )
    value = row.get("win_probability")
    source = str(row.get("probability_source") or "none")
    known = value is not None
    estimate = ProbabilityEstimate(
        float(value) if value is not None else None,
        _number(row.get("prob_ci_low"), 0.0) if known else None,
        _number(row.get("prob_ci_high"), 0.0) if known else None,
        int(_number(meta.get("probability_samples"), 0.0)),
        source,
        int(_number(meta.get("probability_min_samples"), BASELINE_MIN_SAMPLES)),
    )
    ev = row.get("expected_value")
    return SignalRecord(
        signal=signal,
        trace=trace,
        probability=estimate,
        expected_value=float(ev) if ev is not None else None,
        spread=_number(row.get("spread")),
        atr=_number(row.get("atr")),
        reject_reason=str(row.get("reject_reason") or ""),
    )
