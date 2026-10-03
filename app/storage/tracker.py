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

from app.core.clock import BrokerClock, fx_weekend, real_offset
from app.mt5.connection import ConnectionStatus
from app.mt5.diagnostics import estimate_broker_offset
from app.mt5.errors import MT5Error
from app.mt5.history_sync import HistoryImporter, HistoryResult
from app.storage.repositories import Store

SNAPSHOT_SECONDS = 300.0

EventLog = Callable[[str, str], None]
HistoryListener = Callable[[object], None]


def broker_offset(status: ConnectionStatus, utc_now: float) -> float | None:
    """The broker's UTC offset from the freshest quote of the connection checklist.

    None at the weekend and for impossible offsets: a stale price would give a wrong one, and
    the saved, measured clock is used instead.
    """
    report = status.report
    times = [quote.server_time for quote in report.quotes if quote.valid] if report else []
    if not times or fx_weekend(utc_now):
        return None
    offset = estimate_broker_offset(max(times), utc_now)
    return offset if offset is not None and real_offset(offset) else None


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
        self.clock_source: Callable[[], BrokerClock | None] | None = None
        self._guessed = False

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
        with self._lock:
            self._guessed = result.offset_hours is None
        self._notify(result)
        self._emit("INFO", f"Trade history imported: {result.text()}")
        if result.offset_hours is None and self._known_clock() is not None:
            self.on_broker_clock()  # measured while this import ran
        return result

    def on_broker_clock(self) -> None:
        """The analysis measured the broker clock: import again if the last import had to guess."""
        with self._lock:
            again, self._guessed = self._guessed, False
        if again and self._history is not None:
            self._emit("INFO", "Broker time is known now: importing the history again")
            self._background(self.import_in_background)

    def _known_clock(self) -> BrokerClock | None:
        source = self.clock_source
        if source is None:
            return None
        with contextlib.suppress(Exception):
            clock = source()
            return clock if clock is not None and clock.measured else None
        return None

    def _run_import(self) -> HistoryResult:
        if self._history is None:
            raise MT5Error("History import is not available", "Restart the app.")
        status = self._status()
        if not status.connected or status.account is None:
            raise MT5Error("Not connected to MT5", "Connect to MT5 first, then import.")
        offset = broker_offset(status, self._wall_clock())
        return self._history.run(status.account, offset, self._known_clock())

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
