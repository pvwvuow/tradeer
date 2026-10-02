"""Risk data in the database (spec C6, E2): risk events, the saved limit state and the
commission estimate from the imported deal history.

Risk events go to the synced `risk_events` table. The limit state is local only (the
`sync_state` key-value table, one key per account), so a restart can never reset a hit limit.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.storage.ids import new_id
from app.storage.repositories import Store
from app.storage.signal_store import epoch, iso_time

TRADE_DEAL_TYPES = (0, 1)
DEAL_ENTRY_IN = 0
EVENTS_LIMIT = 50


@dataclass(frozen=True)
class SavedRiskEvent:
    time: float  # UTC seconds
    type: str
    message: str


class RiskRepository:
    def __init__(self, store: Store) -> None:
        self.store = store

    # Key-value state (LimitsStateStore) ---------------------------------------------------
    def get_state(self, key: str) -> str | None:
        return self.store.get_state(key)

    def set_state(self, key: str, value: str) -> None:
        self.store.set_state(key, value)

    # Events -------------------------------------------------------------------------------
    def record_event(
        self,
        account: str | None,
        event_type: str,
        message: str,
        details: Mapping[str, Any],
        at: float,
    ) -> str:
        row_id = new_id()
        row = {
            "id": row_id,
            "account_id": account,
            "time": iso_time(at),
            "type": event_type,
            "details_json": {"message": message, **dict(details)},
        }
        self.store.upsert("risk_events", row)
        return row_id

    def recent_events(
        self,
        account: str | None,
        limit: int = EVENTS_LIMIT,
    ) -> list[SavedRiskEvent]:
        if account is None:
            rows = self.store.db.query(
                "SELECT time, type, details_json FROM risk_events ORDER BY time DESC LIMIT ?",
                (limit,),
            )
        else:
            rows = self.store.db.query(
                "SELECT time, type, details_json FROM risk_events WHERE account_id = ? "
                "ORDER BY time DESC LIMIT ?",
                (account, limit),
            )
        return [_event(row) for row in rows]

    # Commission estimate -------------------------------------------------------------------
    def commission_stats(self, account: str, symbol: str) -> tuple[float, float]:
        """All commissions of the symbol's trade deals and the lots that were opened."""
        rows = self.store.db.query(
            "SELECT COALESCE(SUM(commission), 0) AS commission, "
            "COALESCE(SUM(CASE WHEN entry = ? THEN volume ELSE 0 END), 0) AS volume "
            "FROM mt5_deals WHERE account_id = ? AND symbol = ? AND type IN (?, ?)",
            (DEAL_ENTRY_IN, account, symbol, *TRADE_DEAL_TYPES),
        )
        if not rows:
            return 0.0, 0.0
        return float(rows[0]["commission"] or 0.0), float(rows[0]["volume"] or 0.0)


def _event(row: Mapping[str, Any]) -> SavedRiskEvent:
    details = row.get("details_json")
    message = ""
    if isinstance(details, str):
        try:
            parsed = json.loads(details)
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict):
            message = str(parsed.get("message", ""))
    return SavedRiskEvent(epoch(row.get("time")), str(row.get("type") or ""), message)
