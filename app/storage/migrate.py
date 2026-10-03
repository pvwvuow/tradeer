"""Apply the schema migrations (spec E1). Refuses edited migrations and newer databases."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence

from app.storage.migrations import MIGRATIONS, Migration
from app.storage.sqlite_db import Database, iso_utc

CREATE_HISTORY = (
    "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
    "name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
)
INSERT_HISTORY = (
    "INSERT INTO schema_migrations (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)"
)


class MigrationError(Exception):
    """The database cannot be migrated safely, so the app must not use it."""


def split_statements(sql: str) -> list[str]:
    """Split a script into statements (`sqlite3.complete_statement` handles quotes)."""
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise MigrationError(f"Incomplete SQL statement: {buffer.strip()[:80]}")
    return statements


def applied_migrations(db: Database) -> dict[int, str]:
    """Applied versions and their checksums."""
    db.connection().execute(CREATE_HISTORY)
    rows = db.query("SELECT version, checksum FROM schema_migrations")
    return {int(row["version"]): str(row["checksum"]) for row in rows}


def schema_version(db: Database) -> int:
    return max(applied_migrations(db), default=0)


def migrate(
    db: Database,
    migrations: Sequence[Migration] = MIGRATIONS,
    *,
    before_upgrade: Callable[[int], object] | None = None,
) -> list[int]:
    """Apply the pending migrations in order and return their versions.

    `before_upgrade(current_version)` runs once before an existing database is changed (the
    app makes a backup there). Each migration runs in its own transaction.
    """
    done = applied_migrations(db)
    known = {migration.version: migration for migration in migrations}
    newer = sorted(set(done) - set(known))
    if newer:
        raise MigrationError(
            f"This database was created by a newer app version (schema {newer[-1]}). Install "
            "the newer version again, or restore a backup from the backups folder.",
        )
    for version, checksum in sorted(done.items()):
        if known[version].checksum != checksum:
            raise MigrationError(
                f"Migration {version} changed after it was applied to this database. This is a "
                "bug in the app; restore a backup or report it.",
            )
    pending = sorted((m for m in migrations if m.version not in done), key=lambda m: m.version)
    if pending and done and before_upgrade is not None:
        before_upgrade(max(done))
    for migration in pending:
        with db.transaction() as connection:
            for statement in split_statements(migration.sql):
                connection.execute(statement)
            values = (migration.version, migration.name, migration.checksum, iso_utc())
            connection.execute(INSERT_HISTORY, values)
    return [migration.version for migration in pending]
