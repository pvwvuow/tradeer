"""Health check results in the synced `health_checks` table (spec E2, E3)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.observability.health import HealthCheck
from app.storage.ids import new_id
from app.storage.repositories import Store
from app.storage.signal_store import epoch, iso_time

RECENT_LIMIT = 50


@dataclass(frozen=True)
class SavedCheck:
    time: float  # UTC seconds
    name: str
    status: str
    value: float | None
    text: str


def _no_account() -> str | None:
    return None


class HealthRepository:
    def __init__(self, store: Store, account: Callable[[], str | None] = _no_account) -> None:
        self.store = store
        self.account = account

    def record(self, checks: Sequence[HealthCheck], at: float) -> int:
        account = self.account()
        moment = iso_time(at)
        for check in checks:
            row = {
                "id": new_id(),
                "account_id": account,
                "time": moment,
                "name": check.name,
                "status": check.status.value,
                "value": check.value,
                "details_json": {"title": check.title, "text": check.text, "fix": check.fix},
            }
            self.store.upsert("health_checks", row)
        return len(checks)

    def recent(self, limit: int = RECENT_LIMIT, problems_only: bool = False) -> list[SavedCheck]:
        where = "WHERE status IN ('warning', 'critical') " if problems_only else ""
        rows = self.store.db.query(
            f"SELECT time, name, status, value, details_json FROM health_checks {where}"
            "ORDER BY time DESC LIMIT ?",
            (limit,),
        )
        return [_saved(row) for row in rows]


def _saved(row: Mapping[str, Any]) -> SavedCheck:
    details = row.get("details_json")
    text = ""
    if isinstance(details, str):
        try:
            parsed = json.loads(details)
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict):
            text = str(parsed.get("text", ""))
    value = row.get("value")
    return SavedCheck(
        epoch(row.get("time")),
        str(row.get("name") or ""),
        str(row.get("status") or ""),
        float(value) if value is not None else None,
        text,
    )
