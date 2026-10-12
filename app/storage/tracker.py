"""Record the connected MT5 account (spec E2): the account row, snapshots and the history.

Listens to the connection status. On a new connection it saves the account, links it to the
running session and imports the history in the background. While connected, an account
snapshot is saved every 5 minutes (the maintenance timer calls `tick()`).

0.50.2 (PC log of 12 October 2026): the trade list missed a trade that closed at its target
while MT5 was cut off for 9 hours, because the history was only imported on the first
connection. Now it is imported again after every reconnect, every 10 minutes while
connected, and soon after the number of open positions goes down (a trade closed). These
quiet imports only log when they found something new.
"""

from __future__ import annotations

import contextlib
import math
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
HISTORY_SECONDS = 600.0  # while connected, the history is imported again this often

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
        history_seconds: float = HISTORY_SECONDS,
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
        self._history_seconds = history_seconds
        self._clock = clock
        self._wall_clock = wall_clock
        self._background = run_in_background
        self._log = log
        self._lock = threading.Lock()
        self._key: tuple[str, int] | None = None
        self._connected = False  # the last status was a connection (0.50.2)
        self._next_snapshot = 0.0
        self._next_import = math.inf  # the next quiet import (0.50.2)
        self._open: int | None = None  # open positions at the last tick
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
            with self._lock:
                self._connected = False
            return
        key = (account.server, account.login)
        with self._lock:
            new = key != self._key
            back = not new and not self._connected
            self._key = key
            self._connected = True
        if new:
            self.account_id = self._store.upsert_account(account)
            self._store.set_session_account(self._session_id, self.account_id)
            self._next_snapshot = 0.0
            self._next_import = self._clock() + self._history_seconds
            if self._history is not None:
                self._background(self.import_in_background)
        elif back and self._history is not None:
            self._next_import = self._clock() + self._history_seconds
            self._emit("INFO", "Reconnected to MT5: reading the trade history again")
            self._background(self.import_in_background)
        self.tick()

    def tick(self) -> None:
        """Save an account snapshot when one is due; import the history again when it is due
        or a position closed since the last tick."""
        status = self._status()
        account = status.account
        if not status.connected or account is None or self.account_id is None:
            return
        now = self._clock()
        opened = status.open_positions
        closed = self._open is not None and opened < self._open
        self._open = opened
        if self._history is not None and (closed or now >= self._next_import):
            self._next_import = now + self._history_seconds
            self._background(self.refresh_in_background)
        if now < self._next_snapshot:
            return
        self._next_snapshot = now + self._snapshot_seconds
        self._store.record_snapshot(self.account_id, account, status.open_positions)

    def import_history(self, *, quiet: bool = False) -> HistoryResult:
        """Import new history now. Blocks on MT5 and the database: call it from a worker.
        A quiet import (the periodic one) logs only when it found something new."""
        try:
            result = self._run_import()
        except Exception as error:
            self._notify(error)
            raise
        self.last_history = result
        with self._lock:
            self._guessed = result.offset_hours is None
        self._notify(result)
        if not quiet or result.new_deals or result.changed_trades:
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

    def refresh_in_background(self) -> None:
        """The quiet import of `tick` (in a worker)."""
        try:
            self.import_history(quiet=True)
        except Exception as error:
            self._emit("WARNING", f"Trade history refresh failed: {type(error).__name__}: {error}")
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
