"""Trades placed by the bot (spec E2 `trades`, `trade_events`, `mt5_requests`).

Live trades use the same row id as the history import (`trade_id(account, position)`), so the
import and the execution engine fill in one row together and never create a duplicate. Paper
trades get their own ids and `mode = 'paper'`. Every MT5 request and its answer is one
`mt5_requests` row. The engine's working state (the trades it manages) is local only, in
`sync_state`, so a crash or restart resumes managing exactly the same positions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.brokers.base import Attempt
from app.storage.ids import new_id, stable_id, trade_id
from app.storage.repositories import Store
from app.storage.signal_store import iso_time

STATE_KEY = "execution_state"


def bot_trade_id(account: str, mode: str, ticket: int) -> str:
    if mode == "paper":
        return stable_id("paper-trade", account, int(ticket))
    return trade_id(account, ticket)


class TradeRepository:
    def __init__(self, store: Store) -> None:
        self.store = store

    def save_trade(self, row: Mapping[str, Any]) -> bool:
        return self.store.upsert("trades", row)

    def trade(self, row_id: str) -> dict[str, Any] | None:
        return self.store.get("trades", row_id)

    def record_event(
        self,
        account: str | None,
        trade: str,
        kind: str,
        at: float,
        *,
        old: object = None,
        new: object = None,
        reason: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        row_id = new_id()
        self.store.upsert(
            "trade_events",
            {
                "id": row_id,
                "account_id": account,
                "trade_id": trade,
                "time": iso_time(at),
                "type": kind,
                "old_value": None if old is None else str(old),
                "new_value": None if new is None else str(new),
                "reason": reason,
                "payload_json": dict(payload or {}),
            },
        )
        return row_id

    def record_attempts(
        self,
        account: str | None,
        trace_id: str,
        attempts: Sequence[Attempt],
    ) -> int:
        with self.store.db.transaction() as connection:
            for attempt in attempts:
                self.store.upsert_in(
                    connection,
                    "mt5_requests",
                    {
                        "id": new_id(),
                        "account_id": account,
                        "trace_id": trace_id,
                        "action": attempt.action,
                        "request_json": dict(attempt.request),
                        "retcode": attempt.retcode,
                        "retcode_text": attempt.retcode_text,
                        "result_json": dict(attempt.result),
                        "last_error": attempt.last_error,
                        "latency_ms": attempt.latency_ms,
                        "attempt": attempt.attempt,
                    },
                )
        return len(attempts)

    def daily_net(self, account: str, mode: str, since: str) -> list[tuple[str, float]]:
        """Net result of the trades closed per UTC day since `since` (ISO text), oldest first."""
        rows = self.store.db.query(
            "SELECT substr(close_time, 1, 10) AS day, SUM(net_profit) AS net FROM trades "
            "WHERE account_id = ? AND mode = ? AND close_time >= ? AND net_profit IS NOT NULL "
            "GROUP BY day ORDER BY day",
            (account, mode, since),
        )
        return [(str(row["day"]), float(row["net"] or 0.0)) for row in rows]

    def get_state(self, key: str) -> str | None:
        return self.store.get_state(key)

    def set_state(self, key: str, value: str) -> None:
        self.store.set_state(key, value)
