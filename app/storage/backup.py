"""Daily database backups (spec E1): one file per day, the newest 7 are kept.

The SQLite online backup API copies a consistent snapshot while the app keeps running. A
backup is also made before a migration changes an existing database.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from app.storage.sqlite_db import Database

BACKUP_DIR_NAME = "backups"
KEEP_DAILY = 7
KEEP_UPGRADES = 3
DAILY_PATTERN = "workstation-????????.db"
UPGRADE_PATTERN = "workstation-before-schema-*.db"


def _copy(db: Database, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    destination = sqlite3.connect(temporary)
    try:
        db.connection().backup(destination)
    finally:
        destination.close()
    temporary.replace(target)
    return target


def prune(folder: Path, pattern: str, keep: int) -> list[Path]:
    """Delete all but the newest `keep` files that match `pattern`. Returns the deleted."""
    files = sorted(folder.glob(pattern))
    old = files[:-keep] if keep > 0 else files
    for path in old:
        path.unlink(missing_ok=True)
    return old


def daily_backup(db: Database, folder: Path, today: date | None = None) -> Path | None:
    """Back up once per day. Returns the new file, or None when today's backup exists."""
    day = today or datetime.now(UTC).date()
    target = folder / f"workstation-{day:%Y%m%d}.db"
    if target.exists():
        return None
    _copy(db, target)
    prune(folder, DAILY_PATTERN, KEEP_DAILY)
    return target


def upgrade_backup(db: Database, folder: Path, version: int) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = _copy(db, folder / f"workstation-before-schema-{version}-{stamp}.db")
    prune(folder, UPGRADE_PATTERN, KEEP_UPGRADES)
    return target
