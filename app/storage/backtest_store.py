"""Backtest runs in the database (spec E2 `backtest_runs`): the period, the cost model, the
metrics, the walk-forward and Monte-Carlo results, so runs can be compared later (and by the
AI loop in Phase 13). Synced to Supabase like every business table."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.storage.ids import new_id
from app.storage.repositories import Store
from app.storage.signal_store import epoch

RECENT_RUNS = 50


@dataclass(frozen=True)
class SavedRun:
    id: str
    created_at: float  # UTC seconds
    config_id: str
    period: str
    costs: Mapping[str, Any]
    metrics: Mapping[str, Any]
    walk_forward: Mapping[str, Any] | None
    monte_carlo: Mapping[str, Any] | None

    @property
    def title(self) -> str:
        symbol = self.metrics.get("symbol", "")
        strategies = ", ".join(self.metrics.get("strategies", []))
        return f"{symbol} {strategies} {self.period}".strip()


def _json(value: object) -> Any:
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


class BacktestRepository:
    def __init__(self, store: Store) -> None:
        self.store = store

    def save(
        self,
        account: str | None,
        *,
        config_id: str,
        period: str,
        costs: Mapping[str, Any],
        metrics: Mapping[str, Any],
        walk_forward: Mapping[str, Any] | None = None,
        monte_carlo: Mapping[str, Any] | None = None,
    ) -> str:
        row_id = new_id()
        self.store.upsert(
            "backtest_runs",
            {
                "id": row_id,
                "account_id": account,
                "config_id": config_id,
                "period": period,
                "costs_json": dict(costs),
                "metrics_json": dict(metrics),
                "walk_forward_json": dict(walk_forward) if walk_forward is not None else None,
                "monte_carlo_json": dict(monte_carlo) if monte_carlo is not None else None,
            },
        )
        return row_id

    def recent(self, limit: int = RECENT_RUNS) -> list[SavedRun]:
        rows = self.store.db.query(
            "SELECT * FROM backtest_runs ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        return [
            SavedRun(
                id=str(row["id"]),
                created_at=epoch(row.get("created_at")),
                config_id=str(row.get("config_id") or ""),
                period=str(row.get("period") or ""),
                costs=_json(row.get("costs_json")) or {},
                metrics=_json(row.get("metrics_json")) or {},
                walk_forward=_json(row.get("walk_forward_json")),
                monte_carlo=_json(row.get("monte_carlo_json")),
            )
            for row in rows
        ]
