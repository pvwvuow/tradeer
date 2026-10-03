"""Shared helpers for the storage tests: a migrated database in a temporary folder."""

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.storage.migrate import migrate
from app.storage.repositories import Store
from app.storage.sqlite_db import Database, database_path


@contextmanager
def temporary_store() -> Iterator[Store]:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(database_path(Path(tmp)))
        try:
            migrate(db)
            yield Store(db)
        finally:
            db.close()
