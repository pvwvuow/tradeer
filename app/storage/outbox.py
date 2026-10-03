"""The transactional outbox (spec E1): rows waiting for the cloud.

`enqueue()` runs in the same transaction as the business write, so a row can never be saved
without its upload, or the other way round. There is at most one entry per row: a newer write
replaces the waiting payload and raises its version, and `mark_sent()` only removes the
version that was uploaded, so a change made during an upload is never lost.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.storage.schema import TABLES
from app.storage.sqlite_db import iso_utc

ERROR_CHARS = 1000

ENQUEUE = """
INSERT INTO outbox (table_name, row_id, payload, version, state, attempts, created_at, updated_at)
VALUES (?, ?, ?, 1, 'pending', 0, ?, ?)
ON CONFLICT (table_name, row_id) DO UPDATE SET
    payload = excluded.payload,
    version = outbox.version + 1,
    state = 'pending',
    attempts = 0,
    last_error = NULL,
    updated_at = excluded.updated_at
"""


@dataclass(frozen=True)
class OutboxItem:
    id: int
    table: str
    row_id: str
    payload: dict[str, Any]
    version: int
    attempts: int


@dataclass(frozen=True)
class OutboxCounts:
    pending: int = 0
    failed: int = 0


def enqueue(connection: sqlite3.Connection, table: str, payload: Mapping[str, Any]) -> None:
    if table not in TABLES:
        raise ValueError(f"{table} is not synced to the cloud")
    text = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), default=str)
    now = iso_utc()
    connection.execute(ENQUEUE, (table, str(payload["id"]), text, now, now))


def pending(connection: sqlite3.Connection, limit: int) -> list[OutboxItem]:
    rows = connection.execute(
        "SELECT id, table_name, row_id, payload, version, attempts FROM outbox "
        "WHERE state = 'pending' ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        OutboxItem(
            id=int(row["id"]),
            table=str(row["table_name"]),
            row_id=str(row["row_id"]),
            payload=json.loads(row["payload"]),
            version=int(row["version"]),
            attempts=int(row["attempts"]),
        )
        for row in rows
    ]


def mark_sent(connection: sqlite3.Connection, items: Sequence[OutboxItem]) -> int:
    """Remove uploaded entries. An entry that changed meanwhile stays for the next upload."""
    removed = 0
    for item in items:
        cursor = connection.execute(
            "DELETE FROM outbox WHERE id = ? AND version = ?",
            (item.id, item.version),
        )
        removed += cursor.rowcount
    return removed


def mark_failed(connection: sqlite3.Connection, item: OutboxItem, error: str) -> None:
    """Park a row the cloud refused, so it no longer blocks the others ("Retry failed")."""
    connection.execute(
        "UPDATE outbox SET state = 'failed', attempts = attempts + 1, last_error = ?, "
        "updated_at = ? WHERE id = ? AND version = ?",
        (error[:ERROR_CHARS], iso_utc(), item.id, item.version),
    )


def retry_failed(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(
        "UPDATE outbox SET state = 'pending', attempts = 0, updated_at = ? WHERE state = 'failed'",
        (iso_utc(),),
    )
    return cursor.rowcount


def counts(connection: sqlite3.Connection) -> OutboxCounts:
    rows = connection.execute("SELECT state, COUNT(*) AS n FROM outbox GROUP BY state").fetchall()
    found = {str(row["state"]): int(row["n"]) for row in rows}
    return OutboxCounts(pending=found.get("pending", 0), failed=found.get("failed", 0))


def failures(connection: sqlite3.Connection, limit: int = 20) -> list[tuple[str, str, str]]:
    """(table, row id, error) of parked rows, newest first."""
    rows = connection.execute(
        "SELECT table_name, row_id, last_error FROM outbox WHERE state = 'failed' "
        "ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [(str(row["table_name"]), str(row["row_id"]), str(row["last_error"])) for row in rows]


def waiting_ids(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute("SELECT row_id FROM outbox WHERE table_name = ?", (table,))
    return {str(row["row_id"]) for row in rows.fetchall()}
