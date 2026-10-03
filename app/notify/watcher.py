"""Turns the engine's snapshots into notices (spec C14). Each `on_*` method is a listener
for one part of the app (they are called from that part's thread) and remembers the last
snapshot it saw, so only changes notify."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from app.engine.execution import ExecutionSnapshot
from app.engine.signal_pipeline import SignalsSnapshot
from app.mt5.connection import ConnectionStatus
from app.notify.center import NotificationCenter
from app.notify.events import (
    EventKind,
    Notice,
    connection_notices,
    execution_notices,
    risk_notices,
    signal_notices,
    sync_notices,
)
from app.risk.risk_manager import RiskSnapshot
from app.storage.sync import SyncStatus

ERROR_SECONDS = 300.0


class NoticeWatcher:
    def __init__(
        self,
        center: NotificationCenter,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.center = center
        self._clock = clock
        self._lock = threading.Lock()
        self._execution: ExecutionSnapshot | None = None
        self._signals: SignalsSnapshot | None = None
        self._risk: RiskSnapshot | None = None
        self._connection: ConnectionStatus | None = None
        self._sync: SyncStatus | None = None
        self._last_error = -ERROR_SECONDS

    def on_execution(self, snapshot: object) -> None:
        if isinstance(snapshot, ExecutionSnapshot):
            with self._lock:
                old, self._execution = self._execution, snapshot
            self.center.publish_all(execution_notices(old, snapshot))

    def on_signals(self, snapshot: object) -> None:
        if isinstance(snapshot, SignalsSnapshot):
            with self._lock:
                old, self._signals = self._signals, snapshot
            self.center.publish_all(signal_notices(old, snapshot))

    def on_risk(self, snapshot: object) -> None:
        if isinstance(snapshot, RiskSnapshot):
            with self._lock:
                old, self._risk = self._risk, snapshot
            self.center.publish_all(risk_notices(old, snapshot))

    def on_connection(self, status: object) -> None:
        if isinstance(status, ConnectionStatus):
            with self._lock:
                old, self._connection = self._connection, status
            self.center.publish_all(connection_notices(old, status))

    def on_sync(self, status: object) -> None:
        if isinstance(status, SyncStatus):
            with self._lock:
                old, self._sync = self._sync, status
            self.center.publish_all(sync_notices(old, status))

    def on_error(self, message: str) -> None:
        """An ERROR or CRITICAL log line: at most one notice every five minutes."""
        now = self._clock()
        with self._lock:
            if now - self._last_error < ERROR_SECONDS:
                return
            self._last_error = now
        self.center.publish(Notice(EventKind.ERROR, message[:300], f"error:{message[:80]}"))

    def on_drift(self, warnings: list[str]) -> None:
        if warnings:
            self.center.publish(Notice(EventKind.DRIFT, "; ".join(warnings), "drift"))
