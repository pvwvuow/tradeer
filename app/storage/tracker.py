"""Record the connected MT5 account (spec E2): the account row, snapshots and the history.

Listens to the connection status. On a new connection it saves the account, links it to the
running session and imports the history in the background. While connected, an account
snapshot is saved every 5 minutes (the maintenance timer calls `tick()`).
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable

from app.mt5.connection import ConnectionStatus
from app.mt5.diagnostics import estimate_broker_offset
from app.mt5.errors import MT5Error
from app.mt5.history_sync import HistoryImporter, HistoryResult
from app.storage.repositories import Store

SNAPSHOT_SECONDS = 300.0

EventLog = Callable[[str, str], None]
HistoryListener = Callable[[object], None]


def broker_offset(status: ConnectionStatus, utc_now: float) -> float | None:
    """The broker's UTC offset from the freshest quote of the connection checklist."""
    report = status.report
    times = [quote.server_time for quote in report.quotes if quote.valid] if report else []
    return estimate_broker_offset(max(times), utc_now) if times else None


def _in_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="history-import", daemon=True).start()


class AccountTracker:
    def __init__(
        self,
        store: Store,
        session_id: str,
        *,
        status: Callable[[], ConnectionStatus],
        history: HistoryImporter | None = None,
        snapshot_seconds: float = SNAPSHOT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        run_in_background: Callable[[Callable[[], None]], None] = _in_thread,
        log: EventLog | None = None,
    ) -> None:
        self._store = store
        self._session_id = session_id
        self._status = status
        self._history = history
        self._snapshot_seconds = snapshot_seconds
        self._clock = clock
        self._wall_clock = wall_clock
        self._background = run_in_background
        self._log = log
        self._lock = threading.Lock()
        self._key: tuple[str, int] | None = None
        self._next_snapshot = 0.0
        self._listeners: list[HistoryListener] = []
        self.account_id: str | None = None
        self.last_history: HistoryResult | None = None

    @property
    def can_import(self) -> bool:
        return self._history is not None

    def add_listener(self, listener: HistoryListener) -> None:
        """`listener(result)` gets a `HistoryResult` or the exception of a failed import."""
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: HistoryListener) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def on_status(self, status: ConnectionStatus) -> None:
        """Connection listener (any thread)."""
        account = status.account
        if not status.connected or account is None:
            return
        key = (account.server, account.login)
        with self._lock:
            new = key != self._key
            self._key = key
        if new:
            self.account_id = self._store.upsert_account(account)
            self._store.set_session_account(self._session_id, self.account_id)
            self._next_snapshot = 0.0
            if self._history is not None:
                self._background(self.import_in_background)
        self.tick()

    def tick(self) -> None:
        """Save an account snapshot when one is due."""
        status = self._status()
        account = status.account
        if not status.connected or account is None or self.account_id is None:
            return
        now = self._clock()
        if now < self._next_snapshot:
            return
        self._next_snapshot = now + self._snapshot_seconds
        self._store.record_snapshot(self.account_id, account, status.open_positions)

    def import_history(self) -> HistoryResult:
        """Import new history now. Blocks on MT5 and the database: call it from a worker."""
        try:
            result = self._run_import()
        except Exception as error:
            self._notify(error)
            raise
        self.last_history = result
        self._notify(result)
        self._emit("INFO", f"Trade history imported: {result.text()}")
        return result

    def _run_import(self) -> HistoryResult:
        if self._history is None:
            raise MT5Error("History import is not available", "Restart the app.")
        status = self._status()
        if not status.connected or status.account is None:
            raise MT5Error("Not connected to MT5", "Connect to MT5 first, then import.")
        return self._history.run(status.account, broker_offset(status, self._wall_clock()))

    def import_in_background(self) -> None:
        try:
            self.import_history()
        except Exception as error:
            self._emit("WARNING", f"Trade history import failed: {type(error).__name__}: {error}")
        finally:
            self._store.db.release()

    def _notify(self, result: object) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            with contextlib.suppress(Exception):
                listener(result)

    def _emit(self, level: str, message: str) -> None:
        if self._log is not None:
            with contextlib.suppress(Exception):
                self._log(level, message)
