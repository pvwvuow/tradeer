"""The cloud sync (spec E1): outbox -> Supabase in batches, with exponential backoff.

`SyncEngine.run_once()` does one step and is testable without threads or a network.
`SyncWorker` runs it in a background thread every few seconds, or at once when nudged.
Uploads are upserts by id, so a retry after a lost answer never creates a duplicate. Rows the
cloud refuses are parked one by one, so a single bad row never blocks the rest.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol

from app.storage import outbox
from app.storage.outbox import OutboxCounts, OutboxItem
from app.storage.remote import AuthSession, RemoteError, RemoteErrorKind
from app.storage.schema import TABLES
from app.storage.sqlite_db import Database, iso_utc

BATCH_SIZE = 200
SYNC_ORDER = tuple(TABLES)


class SyncState(StrEnum):
    DISABLED = "disabled"
    SIGNED_OUT = "signed_out"
    UP_TO_DATE = "up_to_date"
    SYNCING = "syncing"
    OFFLINE = "offline"
    PAUSED = "paused"
    SETUP_NEEDED = "setup_needed"
    ERROR = "error"


STATE_LABELS: dict[SyncState, str] = {
    SyncState.DISABLED: "Cloud: off",
    SyncState.SIGNED_OUT: "Cloud: signed out",
    SyncState.UP_TO_DATE: "Cloud: up to date",
    SyncState.SYNCING: "Cloud: uploading",
    SyncState.OFFLINE: "Cloud: offline",
    SyncState.PAUSED: "Cloud: project paused",
    SyncState.SETUP_NEEDED: "Cloud: setup needed",
    SyncState.ERROR: "Cloud: error",
}
ERROR_STATES: dict[RemoteErrorKind, SyncState] = {
    RemoteErrorKind.OFFLINE: SyncState.OFFLINE,
    RemoteErrorKind.RATE_LIMITED: SyncState.OFFLINE,
    RemoteErrorKind.PAUSED: SyncState.PAUSED,
    RemoteErrorKind.SETUP: SyncState.SETUP_NEEDED,
    RemoteErrorKind.SERVER: SyncState.ERROR,
}
ERROR_MESSAGES: dict[RemoteErrorKind, str] = {
    RemoteErrorKind.OFFLINE: (
        "No connection to Supabase. Everything is saved on this PC and uploads when the "
        "connection is back."
    ),
    RemoteErrorKind.RATE_LIMITED: "Supabase asked the app to slow down. Retrying soon.",
    RemoteErrorKind.PAUSED: (
        "Your Supabase project is paused (free plan after a week without use). Restore it on "
        "supabase.com; nothing is lost and the upload continues."
    ),
    RemoteErrorKind.SETUP: (
        "The cloud tables are missing or outdated. Run the SQL files from the supabase folder "
        "in the Supabase SQL editor (see the user guide)."
    ),
    RemoteErrorKind.SERVER: "Supabase reported an error. Retrying later.",
}


@dataclass(frozen=True)
class SyncStatus:
    state: SyncState = SyncState.DISABLED
    pending: int = 0
    failed: int = 0
    message: str = "Cloud sync is off. Everything is saved on this PC."
    email: str = ""
    last_upload: str | None = None
    retry_in_seconds: float | None = None

    def status_bar_text(self) -> str:
        text = STATE_LABELS[self.state]
        if self.pending and self.state is not SyncState.UP_TO_DATE:
            text += f" \u00b7 {self.pending:,} waiting"
        if self.failed:
            text += f" \u00b7 {self.failed:,} refused"
        return text


class Remote(Protocol):
    def upsert(self, table: str, rows: Sequence[Mapping[str, Any]], access_token: str) -> None: ...

    def refresh(self, refresh_token: str) -> AuthSession: ...


SessionListener = Callable[[AuthSession | None], None]


@dataclass(frozen=True)
class SyncBackoff:
    first_seconds: float = 2.0
    factor: float = 2.0
    max_seconds: float = 300.0
    paused_seconds: float = 600.0

    def delay(self, failures: int) -> float:
        grown = self.first_seconds * self.factor ** max(failures - 1, 0)
        return float(min(grown, self.max_seconds))


class SyncEngine:
    def __init__(
        self,
        db: Database,
        *,
        batch_size: int = BATCH_SIZE,
        backoff: SyncBackoff | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = db
        self._batch_size = batch_size
        self._backoff = backoff or SyncBackoff()
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._remote: Remote | None = None
        self._session: AuthSession | None = None
        self._on_session: SessionListener | None = None
        self._failures = 0
        self._next_attempt = 0.0
        self._refreshed = False
        self._last_upload: str | None = None
        self._status = SyncStatus()

    @property
    def status(self) -> SyncStatus:
        return self._status

    @property
    def session(self) -> AuthSession | None:
        with self._lock:
            return self._session

    def configure(
        self,
        remote: Remote | None,
        session: AuthSession | None,
        on_session: SessionListener | None = None,
    ) -> None:
        """Set the cloud target. `None` turns sync off; a session without one signs out."""
        with self._lock:
            self._remote = remote
            self._session = session
            self._on_session = on_session
            self._failures = 0
            self._next_attempt = 0.0

    def retry_now(self) -> None:
        with self._lock:
            self._next_attempt = 0.0

    def run_once(self) -> SyncStatus:
        with self._run_lock:
            status = self._step()
            self._status = status
            return status

    # One step ---------------------------------------------------------------------------
    def _step(self) -> SyncStatus:
        with self._lock:
            remote, session = self._remote, self._session
        counts = self._counts()
        if remote is None:
            return SyncStatus(SyncState.DISABLED, counts.pending, counts.failed)
        if session is None:
            message = "Sign in to upload. Nothing is lost: the rows wait on this PC."
            return SyncStatus(SyncState.SIGNED_OUT, counts.pending, counts.failed, message)
        now = self._clock()
        if now < self._next_attempt:
            wait = round(self._next_attempt - now, 1)
            return replace(self._status, pending=counts.pending, retry_in_seconds=wait)
        items = self._pending()
        if not items:
            self._failures = 0
            message = "Everything is uploaded."
            return self._ok(SyncState.UP_TO_DATE, counts, session, message)
        self._refreshed = False
        try:
            if session.expires_soon(self._wall_clock()):
                session = self._refresh(remote, session)
            self._upload(remote, items)
        except RemoteError as error:
            return self._failed(error, session)
        self._failures = 0
        self._last_upload = iso_utc()
        counts = self._counts()
        state = SyncState.SYNCING if counts.pending else SyncState.UP_TO_DATE
        message = "Uploading..." if counts.pending else "Everything is uploaded."
        current = self.session or session
        return self._ok(state, counts, current, message)

    def _ok(
        self,
        state: SyncState,
        counts: OutboxCounts,
        session: AuthSession,
        message: str,
    ) -> SyncStatus:
        if counts.failed:
            message += f" {counts.failed:,} row(s) were refused by the cloud (see the logs)."
        return SyncStatus(
            state,
            counts.pending,
            counts.failed,
            message,
            email=session.email,
            last_upload=self._last_upload,
        )

    def _failed(self, error: RemoteError, session: AuthSession) -> SyncStatus:
        counts = self._counts()
        if error.kind is RemoteErrorKind.AUTH:
            self._set_session(None)
            message = f"The cloud sign-in expired or was refused ({error.message}). Sign in again."
            return SyncStatus(SyncState.SIGNED_OUT, counts.pending, counts.failed, message)
        self._failures += 1
        delay = self._backoff.delay(self._failures)
        if error.retry_after:
            delay = max(delay, error.retry_after)
        if error.kind is RemoteErrorKind.PAUSED:
            delay = max(delay, self._backoff.paused_seconds)
        self._next_attempt = self._clock() + delay
        message = ERROR_MESSAGES.get(error.kind, error.message)
        if error.kind is RemoteErrorKind.SERVER:
            message = f"{message} ({error.message})"
        return SyncStatus(
            ERROR_STATES.get(error.kind, SyncState.ERROR),
            counts.pending,
            counts.failed,
            message,
            email=session.email,
            last_upload=self._last_upload,
            retry_in_seconds=delay,
        )

    # Uploads ----------------------------------------------------------------------------
    def _upload(self, remote: Remote, items: Sequence[OutboxItem]) -> None:
        groups: dict[str, list[OutboxItem]] = {}
        for item in items:
            groups.setdefault(item.table, []).append(item)
        for table in SYNC_ORDER:
            if table in groups:
                self._send(remote, table, groups[table])

    def _send(self, remote: Remote, table: str, group: Sequence[OutboxItem]) -> None:
        try:
            self._upsert(remote, table, group)
        except RemoteError as error:
            if error.kind is not RemoteErrorKind.REJECTED:
                raise
            if len(group) == 1:
                with self._db.transaction() as connection:
                    outbox.mark_failed(connection, group[0], error.message)
                return
            middle = len(group) // 2
            self._send(remote, table, group[:middle])
            self._send(remote, table, group[middle:])
            return
        with self._db.transaction() as connection:
            outbox.mark_sent(connection, group)

    def _upsert(self, remote: Remote, table: str, group: Sequence[OutboxItem]) -> None:
        session = self._current()
        try:
            remote.upsert(table, _rows(group, session), session.access_token)
        except RemoteError as error:
            if error.kind is not RemoteErrorKind.AUTH or self._refreshed:
                raise
            session = self._refresh(remote, session)
            remote.upsert(table, _rows(group, session), session.access_token)

    def _refresh(self, remote: Remote, session: AuthSession) -> AuthSession:
        self._refreshed = True
        try:
            fresh = remote.refresh(session.refresh_token)
        except RemoteError as error:
            if error.kind in (RemoteErrorKind.AUTH, RemoteErrorKind.REJECTED):
                raise RemoteError(RemoteErrorKind.AUTH, error.message) from error
            raise
        if not fresh.email:
            fresh = replace(fresh, email=session.email)
        self._set_session(fresh)
        return fresh

    def _current(self) -> AuthSession:
        session = self.session
        if session is None:
            raise RemoteError(RemoteErrorKind.AUTH, "Not signed in")
        return session

    def _set_session(self, session: AuthSession | None) -> None:
        with self._lock:
            self._session = session
            listener = self._on_session
        if listener is not None:
            with contextlib.suppress(Exception):
                listener(session)

    # Outbox access ------------------------------------------------------------------------
    def _counts(self) -> OutboxCounts:
        return outbox.counts(self._db.connection())

    def _pending(self) -> list[OutboxItem]:
        return outbox.pending(self._db.connection(), self._batch_size)


def _rows(group: Sequence[OutboxItem], session: AuthSession) -> list[dict[str, Any]]:
    return [{**item.payload, "user_id": session.user_id} for item in group]


StatusListener = Callable[[SyncStatus], None]
EventLog = Callable[[str, str], None]


class SyncWorker:
    """Runs the engine in the background thread `cloud-sync`."""

    def __init__(
        self,
        engine: SyncEngine,
        *,
        interval_seconds: float = 5.0,
        log: EventLog | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        self.engine = engine
        self._interval = interval_seconds
        self._log = log
        self._heartbeat = heartbeat
        self._listeners: list[StatusListener] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def add_listener(self, listener: StatusListener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: StatusListener) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cloud-sync", daemon=True)
        self._thread.start()

    def nudge(self) -> None:
        """Run a step now, for example after new rows were saved or a sign-in."""
        self.engine.retry_now()
        self._wake.set()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        last: SyncStatus | None = None
        while not self._stop.is_set():
            try:
                status = self.engine.run_once()
            except Exception as error:
                message = f"The cloud sync failed: {type(error).__name__}: {error}"
                status = SyncStatus(SyncState.ERROR, message=message)
            if last is None or status.state is not last.state:
                self._report(status)
            if status != last:
                self._publish(status)
            last = status
            self._beat()
            fast = status.state is SyncState.SYNCING
            self._wake.wait(0.2 if fast else self._interval)
            self._wake.clear()

    def _report(self, status: SyncStatus) -> None:
        if self._log is None:
            return
        trouble = status.state in (SyncState.OFFLINE, SyncState.PAUSED, SyncState.ERROR)
        level = "WARNING" if trouble or status.state is SyncState.SETUP_NEEDED else "INFO"
        with contextlib.suppress(Exception):
            self._log(level, f"Cloud sync: {status.state.value}. {status.message}")

    def _publish(self, status: SyncStatus) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            with contextlib.suppress(Exception):
                listener(status)

    def _beat(self) -> None:
        if self._heartbeat is not None:
            with contextlib.suppress(Exception):
                self._heartbeat()
