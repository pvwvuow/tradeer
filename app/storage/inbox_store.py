"""The Telegram signals tab remembers (0.49.1): its lines and cards are kept in the local
database, so they are still there after the app was closed and opened again.

Local only, like the channel tables (`CREATE TABLE IF NOT EXISTS`, nothing goes to the
cloud). A row is one entry of the tab: a note, a channel's order card or a follow-up card,
with its text and the state it ended in. A card cannot come back alive (its order was
planned against a price of that moment), so the next start shows it as history.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.storage.repositories import Store
from app.storage.signal_store import epoch, iso_time

TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS tg_inbox ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, channel TEXT NOT NULL, "
    "at TEXT NOT NULL, text TEXT NOT NULL, state TEXT NOT NULL DEFAULT '')"
)
KEEP_ROWS = 500
TEXT_CHARS = 1500


@dataclass(frozen=True)
class InboxRow:
    id: int
    kind: str
    channel: str
    at: float
    text: str
    state: str


class InboxStore:
    def __init__(self, store: Store) -> None:
        self.store = store
        with store.db.transaction() as connection:
            connection.execute(TABLE_SQL)

    def add(self, kind: str, channel: str, at: float, text: str, state: str = "") -> int:
        """Keep one entry (the oldest beyond `KEEP_ROWS` go); its row number."""
        with self.store.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO tg_inbox (kind, channel, at, text, state) VALUES (?, ?, ?, ?, ?)",
                (kind, channel, iso_time(at), text[:TEXT_CHARS], state),
            )
            row_id = int(cursor.lastrowid or 0)
            connection.execute("DELETE FROM tg_inbox WHERE id <= ?", (row_id - KEEP_ROWS,))
        return row_id

    def set_state(self, row_id: int, state: str) -> bool:
        with self.store.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE tg_inbox SET state = ? WHERE id = ?",
                (state, row_id),
            )
            return cursor.rowcount > 0

    def delete(self, ids: Iterable[int]) -> int:
        removed = 0
        with self.store.db.transaction() as connection:
            for row_id in ids:
                cursor = connection.execute("DELETE FROM tg_inbox WHERE id = ?", (row_id,))
                removed += cursor.rowcount
        return removed

    def recent(self, limit: int) -> list[InboxRow]:
        """The newest `limit` entries, oldest first (so the tab can show them newest first)."""
        rows = self.store.db.query("SELECT * FROM tg_inbox ORDER BY id DESC LIMIT ?", (limit,))
        found = [
            InboxRow(
                int(row["id"]),
                str(row["kind"]),
                str(row["channel"]),
                epoch(row["at"]),
                str(row["text"]),
                str(row["state"] or ""),
            )
            for row in rows
        ]
        return list(reversed(found))
