"""Local clean-up of old low-level rows (spec E1). Trades and signals are never deleted.

Only rows that are already in the cloud (nothing left in the outbox) are removed, so a long
offline period never loses data. The cloud copy has its own clean-up: `supabase/cleanup.sql`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.storage.schema import time_column
from app.storage.sqlite_db import Database, iso_utc

RETENTION_DAYS: dict[str, int] = {
    "app_logs": 90,
    "mt5_requests": 90,
    "health_checks": 30,
    "performance_metrics": 30,
}
NEVER_DELETED = frozenset({"trades", "signals", "decision_traces", "trade_events", "audit_log"})


def cleanup(db: Database, now: datetime | None = None) -> dict[str, int]:
    """Delete expired rows. Returns the number deleted per table."""
    moment = now or datetime.now(UTC)
    deleted: dict[str, int] = {}
    for table, days in RETENTION_DAYS.items():
        if table in NEVER_DELETED:
            continue
        cutoff = iso_utc(moment - timedelta(days=days))
        column = time_column(table)
        with db.transaction() as connection:
            cursor = connection.execute(
                f"DELETE FROM {table} WHERE {column} < ? AND id NOT IN "
                "(SELECT row_id FROM outbox WHERE table_name = ?)",
                (cutoff, table),
            )
            deleted[table] = cursor.rowcount
    return deleted
