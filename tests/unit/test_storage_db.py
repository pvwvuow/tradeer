import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.storage.migrate import MigrationError, migrate, schema_version, split_statements
from app.storage.migrations import MIGRATIONS, Migration
from app.storage.schema import TABLES
from app.storage.sqlite_db import Database, DatabaseClosed, database_path


def open_db(folder: str) -> Database:
    return Database(database_path(Path(folder)))


def table_names(db: Database) -> set[str]:
    rows = db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {str(row["name"]) for row in rows}


def test_a_new_database_uses_wal_and_gets_every_table() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = open_db(tmp)
        try:
            assert migrate(db) == [1]
            assert db.journal_mode() == "wal"
            assert db.integrity_ok()
            local = {"outbox", "sync_state", "mt5_deals", "mt5_orders", "schema_migrations"}
            assert set(TABLES) | local <= table_names(db)
            assert schema_version(db) == 1
            assert db.path == Path(tmp) / "data" / "workstation.db"
        finally:
            db.close()


def test_migrating_again_changes_nothing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = open_db(tmp)
        try:
            migrate(db)
            assert migrate(db) == []
        finally:
            db.close()


def test_a_changed_migration_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = open_db(tmp)
        try:
            migrate(db)
            edited = [Migration(1, "initial", MIGRATIONS[0].sql + "\n-- edited\n;")]
            with pytest.raises(MigrationError):
                migrate(db, edited)
        finally:
            db.close()


def test_a_database_from_a_newer_app_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = open_db(tmp)
        try:
            newer = [*MIGRATIONS, Migration(2, "later", "CREATE TABLE later (id TEXT);")]
            migrate(db, newer)
            with pytest.raises(MigrationError):
                migrate(db)
        finally:
            db.close()


def test_an_existing_database_gets_a_backup_before_an_upgrade() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = open_db(tmp)
        calls: list[int] = []
        try:
            migrate(db, before_upgrade=calls.append)
            assert calls == []
            upgrade = [*MIGRATIONS, Migration(2, "notes", "CREATE TABLE notes (id TEXT);")]
            assert migrate(db, upgrade, before_upgrade=calls.append) == [2]
            assert calls == [1]
        finally:
            db.close()


def test_a_failing_migration_is_rolled_back_completely() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = open_db(tmp)
        try:
            migrate(db)
            broken = Migration(2, "broken", "CREATE TABLE half (id TEXT);\nCREATE TABLE half (x);")
            with pytest.raises(sqlite3.OperationalError):
                migrate(db, [*MIGRATIONS, broken])
            assert "half" not in table_names(db)
            assert schema_version(db) == 1
        finally:
            db.close()


def test_statements_are_split_outside_of_strings_only() -> None:
    script = "INSERT INTO t VALUES ('a;b');\nCREATE TABLE u (\n    id TEXT\n);\n"
    assert split_statements(script) == [
        "INSERT INTO t VALUES ('a;b');",
        "CREATE TABLE u (\n    id TEXT\n);",
    ]
    with pytest.raises(MigrationError):
        split_statements("CREATE TABLE v (id TEXT")


def test_transactions_commit_roll_back_and_nest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = open_db(tmp)
        try:
            db.connection().execute("CREATE TABLE t (n INTEGER)")
            with db.transaction() as connection:
                connection.execute("INSERT INTO t VALUES (1)")
                with db.transaction() as inner:
                    inner.execute("INSERT INTO t VALUES (2)")
            with pytest.raises(ValueError), db.transaction() as connection:
                connection.execute("INSERT INTO t VALUES (3)")
                raise ValueError("abort")
            assert [row["n"] for row in db.query("SELECT n FROM t ORDER BY n")] == [1, 2]
        finally:
            db.close()


def test_a_closed_database_refuses_new_connections() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = open_db(tmp)
        db.close()
        assert db.closed
        with pytest.raises(DatabaseClosed):
            db.connection()


def test_a_thread_can_release_its_connection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = open_db(tmp)
        try:
            first = db.connection()
            db.release()
            second = db.connection()
            assert first is not second
            assert db.scalar("SELECT 1") == 1
        finally:
            db.close()
