"""Performance metrics in the synced `performance_metrics` table (spec E2, E3)."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.observability.metrics import Metric
from app.storage.ids import new_id
from app.storage.repositories import Store
from app.storage.signal_store import epoch, iso_time

RECENT_LIMIT = 200


@dataclass(frozen=True)
class SavedMetric:
    time: float  # UTC seconds
    name: str
    value: float | None
    unit: str
    status: str


def _no_account() -> str | None:
    return None


class PerfRepository:
    def __init__(self, store: Store, account: Callable[[], str | None] = _no_account) -> None:
        self.store = store
        self.account = account

    def record(self, metrics: Sequence[Metric], at: float) -> int:
        """Save the measured metrics (a metric without a value is skipped)."""
        account = self.account()
        moment = iso_time(at)
        saved = 0
        for metric in metrics:
            if metric.value is None or not math.isfinite(metric.value):
                continue
            row = {
                "id": new_id(),
                "account_id": account,
                "time": moment,
                "name": metric.name,
                "value": metric.value,
                "unit": metric.unit,
                "details_json": {
                    "title": metric.title,
                    "status": metric.status.value,
                    "budget": metric.budget,
                    "text": metric.text,
                },
            }
            self.store.upsert("performance_metrics", row)
            saved += 1
        return saved

    def recent(self, limit: int = RECENT_LIMIT, name: str | None = None) -> list[SavedMetric]:
        where = "WHERE name = ? " if name is not None else ""
        parameters: tuple[Any, ...] = (name, limit) if name is not None else (limit,)
        rows = self.store.db.query(
            f"SELECT time, name, value, unit, details_json FROM performance_metrics {where}"
            "ORDER BY time DESC LIMIT ?",
            parameters,
        )
        return [_saved(row) for row in rows]


def _saved(row: Mapping[str, Any]) -> SavedMetric:
    details = row.get("details_json")
    status = ""
    if isinstance(details, str):
        try:
            parsed = json.loads(details)
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict):
            status = str(parsed.get("status", ""))
    value = row.get("value")
    return SavedMetric(
        epoch(row.get("time")),
        str(row.get("name") or ""),
        float(value) if value is not None else None,
        str(row.get("unit") or ""),
        status,
    )


def recent_traces(store: Store, limit: int) -> list[dict[str, Any]]:
    """The newest decision traces with their steps as plain data (for the debug bundle)."""
    rows = store.db.query(
        "SELECT signal_id, trace_id, steps_json, final_decision, created_at "
        "FROM decision_traces ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    found: list[dict[str, Any]] = []
    for row in rows:
        steps: Any = row.get("steps_json")
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except ValueError:
                steps = []
        found.append(
            {
                "signal_id": row.get("signal_id"),
                "trace_id": row.get("trace_id"),
                "created_at": row.get("created_at"),
                "final_decision": row.get("final_decision"),
                "steps": steps,
            },
        )
    return found
