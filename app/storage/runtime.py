"""Open the profile's storage at start-up and close it at exit (used by `app.main`).

Start-up: integrity check, a backup before any schema change, migrations, the daily backup,
the local clean-up and the saved cloud sign-in. While running, a maintenance thread saves
account snapshots and makes the daily backup. Nothing here touches the network.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.credentials import CredentialStore
from app.storage.backup import BACKUP_DIR_NAME, daily_backup, upgrade_backup
from app.storage.cleanup import cleanup
from app.storage.cloud import ClientFactory, CloudAccount
from app.storage.log_store import LogStore, keep_entry
from app.storage.migrate import MigrationError, migrate
from app.storage.repositories import Store
from app.storage.sqlite_db import Database, database_path
from app.storage.sync import SyncEngine, SyncWorker
from app.storage.tracker import AccountTracker

if TYPE_CHECKING:
    from app.observability.logger import LogPipeline

MAINTENANCE_SECONDS = 60.0

EventLog = Callable[[str, str], None]


class StorageError(Exception):
    """The local database cannot be used; the app must not run without it."""


@dataclass
class StorageRuntime:
    profile: str
    profile_dir: Path
    session_id: str
    db: Database
    store: Store
    engine: SyncEngine
    worker: SyncWorker
    cloud: CloudAccount
    log_store: LogStore
    backups: Path
    log: EventLog | None = None
    tracker: AccountTracker | None = None
    _pipeline: LogPipeline | None = None
    _sink: int | None = None
    _backup_day: date | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def start_session(self, *, app_version: str, mode: str, settings: Mapping[str, Any]) -> None:
        self.store.start_session(
            self.session_id,
            app_version=app_version,
            profile=self.profile,
            mode=mode,
            settings=settings,
        )

    def attach_logs(self, pipeline: LogPipeline) -> None:
        """Store audit entries and WARNING+ log entries (and so upload them)."""
        self._pipeline = pipeline
        self._sink = pipeline.add_entry_sink(self.log_store, keep_entry)

    def current_account(self) -> str | None:
        return self.tracker.account_id if self.tracker is not None else None

    def start(self) -> None:
        self.worker.start()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._maintain,
            name="storage-maintenance",
            daemon=True,
        )
        self._thread.start()

    def maintenance(self, today: date | None = None) -> None:
        """Snapshots when due; once a day the backup and the clean-up."""
        if self.tracker is not None:
            self.tracker.tick()
        day = today or datetime.now(UTC).date()
        if day != self._backup_day:
            self._backup_day = day
            daily_backup(self.db, self.backups, day)
            cleanup(self.db)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        with contextlib.suppress(Exception):
            self.store.end_session(self.session_id)
        self.worker.stop()
        if self._pipeline is not None and self._sink is not None:
            self._pipeline.flush()
            self._pipeline.remove_sink(self._sink)
            self._sink = None
        self.cloud.close()
        self.db.close()

    def _maintain(self) -> None:
        while not self._stop.wait(MAINTENANCE_SECONDS):
            try:
                self.maintenance()
            except Exception as error:
                name = type(error).__name__
                self._emit("WARNING", f"Storage maintenance failed: {name}: {error}")

    def _emit(self, level: str, message: str) -> None:
        if self.log is not None:
            with contextlib.suppress(Exception):
                self.log(level, message)


def open_storage(
    profile: str,
    profile_dir: Path,
    session_id: str,
    *,
    credentials: CredentialStore,
    client_factory: ClientFactory,
    log: EventLog | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> StorageRuntime:
    path = database_path(profile_dir)
    backups = profile_dir / BACKUP_DIR_NAME
    try:
        db = Database(path)
    except (sqlite3.Error, OSError) as error:
        raise StorageError(f"The local database {path} could not be opened: {error}") from error
    try:
        if not db.integrity_ok():
            raise StorageError(
                f"The local database {path} is damaged. Close the app and restore the newest "
                f"file from {backups} (rename it to {path.name}).",
            )
        migrate(db, before_upgrade=lambda version: upgrade_backup(db, backups, version))
        today = datetime.now(UTC).date()
        daily_backup(db, backups, today)
        cleanup(db)
    except MigrationError as error:
        db.close()
        raise StorageError(str(error)) from error
    except (sqlite3.Error, OSError) as error:
        db.close()
        raise StorageError(f"The local database {path} could not be prepared: {error}") from error
    except StorageError:
        db.close()
        raise
    store = Store(db)
    engine = SyncEngine(db)
    worker = SyncWorker(engine, log=log, heartbeat=heartbeat)
    cloud = CloudAccount(profile, profile_dir, engine, credentials, client_factory)
    cloud.restore()
    runtime = StorageRuntime(
        profile=profile,
        profile_dir=profile_dir,
        session_id=session_id,
        db=db,
        store=store,
        engine=engine,
        worker=worker,
        cloud=cloud,
        log_store=LogStore(store),
        backups=backups,
        log=log,
        _backup_day=today,
    )
    runtime.log_store.account = runtime.current_account
    return runtime
