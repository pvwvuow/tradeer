"""The local SQLite database (spec E1): the source of truth, WAL mode, one file per profile.

Every thread gets its own connection to the same file. Writes run inside
`with db.transaction():` (`BEGIN IMMEDIATE`), so a business row and its outbox entry are
saved together or not at all.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATA_DIR_NAME = "data"
DB_FILE_NAME = "workstation.db"
BUSY_TIMEOUT_MS = 5000

Row = dict[str, Any]


class DatabaseClosed(RuntimeError):
    """The database was closed because the app is shutting down."""


def iso_utc(moment: datetime | None = None) -> str:
    """UTC time as ISO 8601 text with milliseconds, for example `2026-10-01T12:00:00.000Z`."""
    value = moment if moment is not None else datetime.now(UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def database_path(profile_dir: Path) -> Path:
    return profile_dir / DATA_DIR_NAME / DB_FILE_NAME


def _dict_row(cursor: sqlite3.Cursor, values: tuple[Any, ...]) -> Row:
    names = [column[0] for column in cursor.description]
    return dict(zip(names, values, strict=True))


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = threading.local()
        self._lock = threading.Lock()
        self._connections: list[sqlite3.Connection] = []
        self._closed = False
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection()

    @property
    def closed(self) -> bool:
        return self._closed

    def connection(self) -> sqlite3.Connection:
        """This thread's connection, opened on first use."""
        existing: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if existing is not None:
            return existing
        with self._lock:
            if self._closed:
                raise DatabaseClosed(f"The database {self.path.name} is closed")
            connection = sqlite3.connect(
                self.path,
                timeout=BUSY_TIMEOUT_MS / 1000,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connections.append(connection)
        connection.row_factory = _dict_row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        except sqlite3.Error:
            # A damaged file: close the handle at once, or Windows keeps the file locked.
            with self._lock:
                self._connections.remove(connection)
            connection.close()
            raise
        self._local.connection = connection
        self._local.depth = 0
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One write transaction. A nested call joins the outer transaction."""
        connection = self.connection()
        depth = int(getattr(self._local, "depth", 0))
        if depth:
            self._local.depth = depth + 1
            try:
                yield connection
            finally:
                self._local.depth = depth
            return
        connection.execute("BEGIN IMMEDIATE")
        self._local.depth = 1
        try:
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise
        finally:
            self._local.depth = 0

    def query(self, sql: str, parameters: Sequence[Any] = ()) -> list[Row]:
        return list(self.connection().execute(sql, parameters).fetchall())

    def scalar(self, sql: str, parameters: Sequence[Any] = ()) -> Any:
        row = self.connection().execute(sql, parameters).fetchone()
        return next(iter(row.values())) if row else None

    def journal_mode(self) -> str:
        return str(self.scalar("PRAGMA journal_mode"))

    def integrity_ok(self) -> bool:
        return str(self.scalar("PRAGMA quick_check")) == "ok"

    def release(self) -> None:
        """Close this thread's connection. Call it at the end of a short-lived thread."""
        connection: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if connection is None:
            return
        self._local.connection = None
        with self._lock:
            if connection in self._connections:
                self._connections.remove(connection)
        with contextlib.suppress(sqlite3.Error):
            connection.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            with contextlib.suppress(sqlite3.Error):
                connection.close()
        self._local = threading.local()
