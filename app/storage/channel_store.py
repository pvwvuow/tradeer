"""The Telegram channels the app reads and their messages (docs/SIGNAL_DESK.md 3.3 and 4.2,
phase 21c).

Local only: the tables are made here (`CREATE TABLE IF NOT EXISTS`), not by the synced
schema, so channel ids and texts never go to the cloud. The first text of a message is kept
forever (channels often edit or delete a losing signal); an edit and a deletion are stored
beside it. Every channel gets its own magic number once (26_071_001, 26_071_002, ...),
never reused, so the history and the risk limits keep channels apart.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.channels.folder import ChannelMessage, Peer, clean_text
from app.channels.policy import ChannelMoney, ChannelPolicy, policy_from_json
from app.signals.shadow import ShadowResult
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
    # Every parsed signal of a channel and what the feed did with it (phase 21d2)
    "CREATE TABLE IF NOT EXISTS tg_signals ("
    "channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL, date TEXT NOT NULL, "
    "symbol TEXT NOT NULL, direction TEXT NOT NULL, action TEXT NOT NULL, "
    "reason TEXT NOT NULL DEFAULT '', request_id TEXT NOT NULL DEFAULT '', "
    "latency REAL NOT NULL DEFAULT 0, close_at TEXT, break_even_at TEXT, "
    "created_at TEXT NOT NULL, PRIMARY KEY (channel_id, message_id))",
    # Its shadow result per target (leg), followed on the price history
    "CREATE TABLE IF NOT EXISTS tg_shadow ("
    "channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL, leg INTEGER NOT NULL, "
    "state TEXT NOT NULL, fill_time TEXT, fill_price REAL, exit_time TEXT, exit_price REAL, "
    "r REAL, updated_at TEXT NOT NULL, PRIMARY KEY (channel_id, message_id, leg))",
)
OPEN_SHADOW = ("waiting", "open")


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
class ChannelSignal:
    """A parsed signal of a channel (the text stays in tg_messages)."""

    channel_id: int
    message_id: int
    date: float  # the message time
    symbol: str
    direction: str  # long or short
    action: str  # card, paper or skip (app.channels.policy.Action)
    reason: str = ""
    request_id: str = ""  # the order card's request, when there was one
    latency: float = 0.0  # seconds from the message to the card
    close_at: float | None = None  # the channel said close or cancel
    break_even_at: float | None = None  # the channel said move the stop to the entry


@dataclass(frozen=True)
class ShadowRow:
    channel_id: int
    message_id: int
    leg: int
    state: str
    r: float | None = None
    exit_time: float | None = None


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


def _when(raw: object) -> float | None:
    return epoch(raw) if isinstance(raw, str) and raw else None


def _signal(row: Mapping[str, Any]) -> ChannelSignal:
    return ChannelSignal(
        channel_id=int(row["channel_id"]),
        message_id=int(row["message_id"]),
        date=epoch(row["date"]),
        symbol=str(row["symbol"]),
        direction=str(row["direction"]),
        action=str(row["action"]),
        reason=str(row["reason"] or ""),
        request_id=str(row["request_id"] or ""),
        latency=float(row["latency"] or 0.0),
        close_at=_when(row.get("close_at")),
        break_even_at=_when(row.get("break_even_at")),
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

    def source(self, channel_id: int) -> ChannelSource | None:
        rows = self.store.db.query("SELECT * FROM tg_channels WHERE channel_id = ?", (channel_id,))
        return _source(rows[0]) if rows else None

    def policy(self, channel_id: int) -> ChannelPolicy:
        """The channel's own settings (phase 21d); the defaults are a Paper trial."""
        text = self.store.db.scalar(
            "SELECT settings_json FROM tg_channels WHERE channel_id = ?",
            (channel_id,),
        )
        return policy_from_json(str(text or "{}"))

    def set_policy(self, channel_id: int, policy: ChannelPolicy, now: float) -> bool:
        with self.store.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE tg_channels SET settings_json = ?, updated_at = ? WHERE channel_id = ?",
                (policy.to_json(), iso_time(now), channel_id),
            )
            return cursor.rowcount > 0

    def money(self, magic: int, day_start: float) -> ChannelMoney:
        """The net result of the channel's closed trades (its magic), all and since
        `day_start`."""
        rows = self.store.db.query(
            "SELECT COALESCE(SUM(net_profit), 0) AS closed, "
            "COALESCE(SUM(CASE WHEN close_time >= ? THEN net_profit ELSE 0 END), 0) AS today "
            "FROM trades WHERE magic = ? AND close_time IS NOT NULL AND net_profit IS NOT NULL",
            (iso_time(day_start), magic),
        )
        if not rows:
            return ChannelMoney()
        return ChannelMoney(float(rows[0]["closed"] or 0.0), float(rows[0]["today"] or 0.0))

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

    def message(self, channel_id: int, message_id: int) -> StoredMessage | None:
        rows = self.store.db.query(
            "SELECT * FROM tg_messages WHERE channel_id = ? AND message_id = ?",
            (channel_id, message_id),
        )
        return _message(rows[0]) if rows else None

    # Parsed signals and their shadow results (phase 21d2) -------------------------------
    def save_signal(self, signal: ChannelSignal, now: float) -> bool:
        """The feed's decision on a signal; the first one is kept."""
        with self.store.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO tg_signals (channel_id, message_id, date, symbol, "
                "direction, action, reason, request_id, latency, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    signal.channel_id,
                    signal.message_id,
                    iso_time(signal.date),
                    signal.symbol,
                    signal.direction,
                    signal.action,
                    signal.reason,
                    signal.request_id,
                    round(signal.latency, 3),
                    iso_time(now),
                ),
            )
            return cursor.rowcount > 0

    def signals(self, channel_id: int | None = None, since: float = 0.0) -> list[ChannelSignal]:
        """Parsed signals, oldest first (of one channel, or of all)."""
        where = "WHERE date >= ?" + (" AND channel_id = ?" if channel_id is not None else "")
        values: tuple[Any, ...] = (iso_time(since),)
        if channel_id is not None:
            values += (channel_id,)
        rows = self.store.db.query(f"SELECT * FROM tg_signals {where} ORDER BY date", values)
        return [_signal(row) for row in rows]

    def mark_signal(self, channel_id: int, message_id: int, column: str, at: float) -> bool:
        """A follow-up: `close_at` (close or cancel) or `break_even_at` (stop to entry)."""
        if column not in ("close_at", "break_even_at"):
            raise ValueError(f"unknown follow-up column {column}")
        with self.store.db.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE tg_signals SET {column} = ? WHERE channel_id = ? AND message_id = ? "
                f"AND {column} IS NULL",
                (iso_time(at), channel_id, message_id),
            )
            return cursor.rowcount > 0

    def save_shadow(
        self,
        channel_id: int,
        message_id: int,
        leg: int,
        result: ShadowResult,
        now: float,
    ) -> None:
        """One leg's shadow result."""

        def stamp(value: float | None) -> str | None:
            return iso_time(value) if value is not None else None

        with self.store.db.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO tg_shadow (channel_id, message_id, leg, state, "
                "fill_time, fill_price, exit_time, exit_price, r, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    channel_id,
                    message_id,
                    leg,
                    result.state.value,
                    stamp(result.fill_time),
                    result.fill_price,
                    stamp(result.exit_time),
                    result.exit_price,
                    result.r,
                    iso_time(now),
                ),
            )

    def shadow(self, channel_id: int | None = None) -> list[ShadowRow]:
        where = "WHERE channel_id = ?" if channel_id is not None else ""
        values: tuple[Any, ...] = (channel_id,) if channel_id is not None else ()
        rows = self.store.db.query(
            f"SELECT * FROM tg_shadow {where} ORDER BY channel_id, message_id, leg",
            values,
        )
        return [
            ShadowRow(
                int(row["channel_id"]),
                int(row["message_id"]),
                int(row["leg"]),
                str(row["state"]),
                float(row["r"]) if row.get("r") is not None else None,
                _when(row.get("exit_time")),
            )
            for row in rows
        ]

    def to_follow(self, since: float) -> list[ChannelSignal]:
        """Signals whose shadow is not resolved yet (no legs saved, or a leg still waiting
        or open)."""
        rows = self.shadow()
        done = {(row.channel_id, row.message_id) for row in rows if row.state not in OPEN_SHADOW}
        waiting = {(row.channel_id, row.message_id) for row in rows if row.state in OPEN_SHADOW}
        return [
            signal
            for signal in self.signals(since=since)
            if (signal.channel_id, signal.message_id) in waiting
            or (signal.channel_id, signal.message_id) not in done
        ]

    def channel_trades(self, magic: int) -> list[tuple[float, float | None, float | None, str]]:
        """The channel's closed trades oldest first: net profit, R, slippage and the
        card's request id (from the signal's leg group)."""
        rows = self.store.db.query(
            "SELECT t.net_profit AS profit, t.r_multiple AS r, t.slippage AS slippage, "
            "s.features_json AS features FROM trades t "
            "LEFT JOIN signals s ON s.id = t.signal_id WHERE t.magic = ? "
            "AND t.close_time IS NOT NULL AND t.net_profit IS NOT NULL ORDER BY t.close_time",
            (magic,),
        )
        found: list[tuple[float, float | None, float | None, str]] = []
        for row in rows:
            group = ""
            with contextlib.suppress(ValueError, TypeError, AttributeError):
                meta = json.loads(str(row.get("features") or "{}"))
                group = str(meta.get("features", {}).get("leg_group", "") or "")
            r = row.get("r")
            slip = row.get("slippage")
            found.append(
                (
                    float(row["profit"]),
                    float(r) if r is not None else None,
                    float(slip) if slip is not None else None,
                    group,
                ),
            )
        return found

    def opened(self, magic: int) -> dict[str, float]:
        """The first open time of the channel's trades per card request id (for latency)."""
        rows = self.store.db.query(
            "SELECT t.open_time AS opened, s.features_json AS features FROM trades t "
            "JOIN signals s ON s.id = t.signal_id WHERE t.magic = ? AND t.open_time IS NOT NULL",
            (magic,),
        )
        found: dict[str, float] = {}
        for row in rows:
            with contextlib.suppress(ValueError, TypeError, AttributeError):
                meta = json.loads(str(row.get("features") or "{}"))
                group = str(meta.get("features", {}).get("leg_group", "") or "")
                when = epoch(row["opened"])
                if group and (group not in found or when < found[group]):
                    found[group] = when
        return found

    def edited_after(self, channel_id: int) -> tuple[int, int]:
        """Signal messages the channel deleted, and those edited after their shadow result."""
        signals = {signal.message_id for signal in self.signals(channel_id)}
        exits: dict[int, float] = {}
        for row in self.shadow(channel_id):
            if row.exit_time is not None:
                exits[row.message_id] = min(exits.get(row.message_id, row.exit_time), row.exit_time)
        rows = self.store.db.query(
            "SELECT message_id, edited_at, deleted_at FROM tg_messages WHERE channel_id = ?",
            (channel_id,),
        )
        deleted = edited = 0
        for row in rows:
            message_id = int(row["message_id"])
            if message_id not in signals:
                continue
            if row.get("deleted_at"):
                deleted += 1
            edited_at = _when(row.get("edited_at"))
            if edited_at is not None and message_id in exits and edited_at > exits[message_id]:
                edited += 1
        return deleted, edited
