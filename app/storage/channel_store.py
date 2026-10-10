"""The Telegram channels the app reads and their messages (docs/SIGNAL_DESK.md 3.3 and 4.2,
phase 21c).

Local only: the tables are made here (`CREATE TABLE IF NOT EXISTS`), not by the synced
schema, so channel ids and texts never go to the cloud. The first text of a message is kept
forever (channels often edit or delete a losing signal); an edit and a deletion are stored
beside it. Every channel gets its own magic number once (26_071_001, 26_071_002, ...),
never reused, so the history and the risk limits keep channels apart.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.channels.folder import ChannelMessage, Peer, clean_text
from app.storage.repositories import Store
from app.storage.signal_store import epoch, iso_time

FIRST_MAGIC = 26_071_001
LAST_MAGIC = 26_071_999
TABLES_SQL = (
    "CREATE TABLE IF NOT EXISTS tg_channels ("
    "channel_id INTEGER PRIMARY KEY, title TEXT NOT NULL, username TEXT NOT NULL DEFAULT '', "
    "kind TEXT NOT NULL, magic INTEGER NOT NULL UNIQUE, enabled INTEGER NOT NULL DEFAULT 0, "
    "in_folder INTEGER NOT NULL DEFAULT 1, settings_json TEXT NOT NULL DEFAULT '{}', "
    "first_seen TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS tg_messages ("
    "channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL, date TEXT NOT NULL, "
    "text TEXT NOT NULL, reply_to INTEGER, edited_text TEXT, edited_at TEXT, deleted_at TEXT, "
    "received_at TEXT NOT NULL, PRIMARY KEY (channel_id, message_id))",
    "CREATE INDEX IF NOT EXISTS tg_messages_channel_id_date_idx ON tg_messages (channel_id, date)",
)


@dataclass(frozen=True)
class ChannelSource:
    channel_id: int
    title: str
    username: str
    kind: str
    magic: int
    enabled: bool  # new channels start off
    in_folder: bool  # False after it was taken out of the Telegram folder
    first_seen: float


@dataclass(frozen=True)
class StoredMessage:
    channel_id: int
    message_id: int
    date: float
    text: str  # the first text, never changed
    reply_to: int | None = None
    edited_text: str | None = None
    deleted: bool = False


def _source(row: Mapping[str, Any]) -> ChannelSource:
    return ChannelSource(
        channel_id=int(row["channel_id"]),
        title=str(row["title"]),
        username=str(row["username"] or ""),
        kind=str(row["kind"]),
        magic=int(row["magic"]),
        enabled=bool(row["enabled"]),
        in_folder=bool(row["in_folder"]),
        first_seen=epoch(row["first_seen"]),
    )


def _message(row: Mapping[str, Any]) -> StoredMessage:
    reply = row.get("reply_to")
    edited = row.get("edited_text")
    return StoredMessage(
        channel_id=int(row["channel_id"]),
        message_id=int(row["message_id"]),
        date=epoch(row["date"]),
        text=str(row["text"]),
        reply_to=int(reply) if reply is not None else None,
        edited_text=str(edited) if edited is not None else None,
        deleted=row.get("deleted_at") is not None,
    )


class ChannelRepository:
    def __init__(self, store: Store) -> None:
        self.store = store
        with store.db.transaction() as connection:
            for statement in TABLES_SQL:
                connection.execute(statement)

    # Channels ---------------------------------------------------------------------------
    def sync_folder(self, peers: Sequence[Peer], now: float) -> list[ChannelSource]:
        """The folder's channels: a new one gets the next magic and starts off; one that
        left the folder is kept (its trades keep their magic), marked as not in the folder."""
        stamp = iso_time(now)
        wanted = {peer.id: peer for peer in peers if peer.source}
        with self.store.db.transaction() as connection:
            rows = connection.execute("SELECT channel_id, magic FROM tg_channels").fetchall()
            known = {int(row["channel_id"]) for row in rows}
            magic = max((int(row["magic"]) for row in rows), default=FIRST_MAGIC - 1)
            for peer_id, peer in wanted.items():
                if peer_id in known:
                    connection.execute(
                        "UPDATE tg_channels SET title = ?, username = ?, kind = ?, "
                        "in_folder = 1, updated_at = ? WHERE channel_id = ?",
                        (peer.title, peer.username, peer.kind.value, stamp, peer_id),
                    )
                    continue
                magic += 1
                if magic > LAST_MAGIC:
                    raise ValueError("no channel magic number is left")
                connection.execute(
                    "INSERT INTO tg_channels (channel_id, title, username, kind, magic, "
                    "first_seen, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (peer_id, peer.title, peer.username, peer.kind.value, magic, stamp, stamp),
                )
            for gone in known - set(wanted):
                connection.execute(
                    "UPDATE tg_channels SET in_folder = 0, updated_at = ? WHERE channel_id = ?",
                    (stamp, gone),
                )
        return self.channels()

    def channels(self) -> list[ChannelSource]:
        rows = self.store.db.query("SELECT * FROM tg_channels ORDER BY magic")
        return [_source(row) for row in rows]

    def set_enabled(self, channel_id: int, enabled: bool, now: float) -> bool:
        with self.store.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE tg_channels SET enabled = ?, updated_at = ? WHERE channel_id = ?",
                (int(enabled), iso_time(now), channel_id),
            )
            return cursor.rowcount > 0

    def read_ids(self) -> set[int]:
        """The channels whose messages are stored: on and still in the folder."""
        rows = self.store.db.query(
            "SELECT channel_id FROM tg_channels WHERE enabled = 1 AND in_folder = 1",
        )
        return {int(row["channel_id"]) for row in rows}

    # Messages ---------------------------------------------------------------------------
    def save_message(self, message: ChannelMessage, now: float) -> bool:
        """A new message (True), or False when it is already stored (the first text stays)."""
        with self.store.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO tg_messages (channel_id, message_id, date, text, "
                "reply_to, received_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    message.channel_id,
                    message.message_id,
                    iso_time(message.date),
                    clean_text(message.text),
                    message.reply_to,
                    iso_time(now),
                ),
            )
            return cursor.rowcount > 0

    def edit_message(self, message: ChannelMessage, now: float) -> bool:
        """An edit is kept beside the first text; an edit of an unknown message is saved
        as a new one."""
        if self.save_message(message, now):
            return True
        text = clean_text(message.text)
        with self.store.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE tg_messages SET edited_text = ?, edited_at = ? WHERE channel_id = ? "
                "AND message_id = ? AND text != ? AND COALESCE(edited_text, '') != ?",
                (text, iso_time(now), message.channel_id, message.message_id, text, text),
            )
            return cursor.rowcount > 0

    def delete_messages(self, channel_id: int, message_ids: Iterable[int], now: float) -> int:
        """Mark messages deleted by the channel; their text stays."""
        removed = 0
        with self.store.db.transaction() as connection:
            for message_id in message_ids:
                cursor = connection.execute(
                    "UPDATE tg_messages SET deleted_at = ? WHERE channel_id = ? "
                    "AND message_id = ? AND deleted_at IS NULL",
                    (iso_time(now), channel_id, message_id),
                )
                removed += cursor.rowcount
        return removed

    def messages(self, channel_id: int, limit: int = 50) -> list[StoredMessage]:
        """The newest messages of a channel first."""
        rows = self.store.db.query(
            "SELECT * FROM tg_messages WHERE channel_id = ? "
            "ORDER BY date DESC, message_id DESC LIMIT ?",
            (channel_id, limit),
        )
        return [_message(row) for row in rows]

    def counts(self) -> dict[int, int]:
        """Stored messages per channel."""
        rows = self.store.db.query(
            "SELECT channel_id, COUNT(*) AS n FROM tg_messages GROUP BY channel_id",
        )
        return {int(row["channel_id"]): int(row["n"]) for row in rows}
