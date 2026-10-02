"""Connection state, heartbeat and automatic reconnect (spec C1, I5).

`connect()` runs the checklist. After a successful connection the monitor thread checks the
terminal every few seconds; when it is lost, the service reconnects with exponential backoff
(`initialize()` also starts the terminal again if it was closed) and raises an urgent alert
when positions were open. A failed first connection is not retried on its own: retrying a
wrong password could lock the account.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, replace
from enum import StrEnum

from app.mt5.api import MT5Api
from app.mt5.checklist import ChecklistReport, ConnectRequest, run_checklist
from app.mt5.errors import MT5Error, MT5Timeout, error_from_last
from app.mt5.gateway import MT5Gateway
from app.mt5.models import AccountSnapshot, TerminalSnapshot

EventLog = Callable[[str, str], None]
StatusListener = Callable[["ConnectionStatus"], None]

HEARTBEAT_SECONDS = 5.0
HEARTBEAT_TIMEOUT_SECONDS = 15.0
CONNECT_EXTRA_SECONDS = 30.0


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


@dataclass(frozen=True)
class ConnectionStatus:
    state: ConnectionState = ConnectionState.DISCONNECTED
    message: str = "Not connected"
    account: AccountSnapshot | None = None
    terminal: TerminalSnapshot | None = None
    report: ChecklistReport | None = None
    open_positions: int = 0
    attempt: int = 0
    retry_in_seconds: float | None = None

    @property
    def connected(self) -> bool:
        return self.state is ConnectionState.CONNECTED

    @property
    def analysis_only(self) -> bool:
        return self.account is not None and self.account.read_only

    def status_bar_text(self) -> str:
        if self.state is ConnectionState.CONNECTED and self.account is not None:
            account = self.account
            return f"MT5: {account.server} · {account.login} · {account.kind.value}"
        if self.state is ConnectionState.RECONNECTING:
            wait = f", next try in {self.retry_in_seconds:.0f} s" if self.retry_in_seconds else ""
            return f"MT5: reconnecting (attempt {self.attempt}{wait})"
        if self.state is ConnectionState.CONNECTING:
            return "MT5: connecting..."
        if self.state is ConnectionState.FAILED:
            return "MT5: connection failed"
        return "MT5: not connected"


@dataclass(frozen=True)
class Backoff:
    initial_seconds: float = 2.0
    factor: float = 2.0
    maximum_seconds: float = 120.0

    def delay(self, attempt: int) -> float:
        return min(self.maximum_seconds, self.initial_seconds * self.factor ** max(0, attempt - 1))


@dataclass(frozen=True)
class _Heartbeat:
    terminal: TerminalSnapshot | None
    account: AccountSnapshot | None
    positions: int


def _read_heartbeat(mt5: MT5Api) -> _Heartbeat:
    info = mt5.terminal_info()
    if info is None:
        return _Heartbeat(None, None, 0)
    account = mt5.account_info()
    return _Heartbeat(
        TerminalSnapshot.from_mt5(info),
        AccountSnapshot.from_mt5(account) if account is not None else None,
        int(mt5.positions_total() or 0),
    )


def _reconnect(mt5: MT5Api, request: ConnectRequest) -> _Heartbeat:
    options: dict[str, object] = {"timeout": request.timeout_ms}
    started = (
        mt5.initialize(request.terminal_path, **options)
        if request.terminal_path
        else mt5.initialize(**options)
    )
    if not started:
        raise error_from_last(mt5.last_error())
    if request.login is not None:
        credentials: dict[str, object] = {"timeout": request.timeout_ms}
        if request.password:
            credentials["password"] = request.password
        if request.server:
            credentials["server"] = request.server
        if not mt5.login(request.login, **credentials):
            raise error_from_last(mt5.last_error())
    return _read_heartbeat(mt5)


class ConnectionService:
    def __init__(
        self,
        gateway: MT5Gateway,
        request_factory: Callable[[], ConnectRequest],
        *,
        log: EventLog | None = None,
        backoff: Backoff | None = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        heartbeat_timeout: float = HEARTBEAT_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        elevated: bool | None = None,
        path_exists: Callable[[str], bool] = os.path.exists,
    ) -> None:
        self.gateway = gateway
        self._path_exists = path_exists
        self._request_factory = request_factory
        self._log = log
        self._backoff = backoff or Backoff()
        self._heartbeat_seconds = heartbeat_seconds
        self._heartbeat_timeout = heartbeat_timeout
        self._clock = clock
        self._elevated = elevated
        self._lock = threading.RLock()
        self._status = ConnectionStatus()
        self._listeners: list[StatusListener] = []
        self._request: ConnectRequest | None = None
        self._next_heartbeat = 0.0
        self._next_retry = 0.0
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None

    @property
    def status(self) -> ConnectionStatus:
        with self._lock:
            return self._status

    def add_listener(self, listener: StatusListener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: StatusListener) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def connect(self, request: ConnectRequest | None = None) -> ChecklistReport:
        """Run the full checklist. Blocks: call it from a worker, never from the UI thread."""
        active = request or self._request_factory()
        with self._lock:
            self._request = active
        self._set(ConnectionStatus(ConnectionState.CONNECTING, "Connecting to MT5"))
        timeout = active.timeout_ms / 1000.0 + CONNECT_EXTRA_SECONDS
        elevated = self._elevated
        exists = self._path_exists
        try:
            report = self.gateway.run(
                "connection_checklist",
                lambda mt5: run_checklist(mt5, active, path_exists=exists, elevated=elevated),
                timeout=timeout,
                arguments={"login": active.login, "server": active.server},
            )
        except MT5Error as error:
            self._emit("ERROR", f"Connection failed: {error.title}")
            self._set(ConnectionStatus(ConnectionState.FAILED, f"{error.title}. {error.fix}"))
            raise
        if report.connected:
            account = report.account
            mode = " (read-only: Analysis-only)" if report.analysis_only else ""
            summary = account.summary() if account is not None else ""
            self._emit("INFO", f"Connected to MT5: {summary}{mode}")
            with self._lock:
                self._next_heartbeat = self._clock() + self._heartbeat_seconds
            self._set(
                ConnectionStatus(
                    ConnectionState.CONNECTED,
                    f"Connected{mode}",
                    account,
                    report.terminal,
                    report,
                ),
            )
        else:
            problem = report.first_problem()
            message = f"{problem.value}. {problem.fix}" if problem else "Not connected"
            self._emit("WARNING", f"Connection checklist failed: {message}")
            self._set(ConnectionStatus(ConnectionState.FAILED, message, report=report))
        return report

    def connect_async(self, request: ConnectRequest | None = None) -> Future[ChecklistReport]:
        future: Future[ChecklistReport] = Future()

        def work() -> None:
            if not future.set_running_or_notify_cancel():
                return
            try:
                future.set_result(self.connect(request))
            except BaseException as error:
                future.set_exception(error)

        threading.Thread(target=work, name="mt5-connect", daemon=True).start()
        return future

    def disconnect(self) -> None:
        with self._lock:
            self._request = None
        with contextlib.suppress(MT5Error):
            self.gateway.call("shutdown", timeout=10)
        self._emit("INFO", "Disconnected from MT5 by the user")
        self._set(ConnectionStatus(ConnectionState.DISCONNECTED, "Disconnected"))

    def poll(self) -> None:
        """One monitor step: heartbeat when connected, a reconnect attempt when it is due."""
        now = self._clock()
        status = self.status
        with self._lock:
            request = self._request
        if request is None:
            return
        if status.state is ConnectionState.CONNECTED and now >= self._next_heartbeat:
            self._heartbeat(status, now)
        elif status.state is ConnectionState.RECONNECTING and now >= self._next_retry:
            self._try_reconnect(status, request, now)

    def start_monitor(self, interval_seconds: float = 1.0) -> None:
        if self._monitor is not None:
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(interval_seconds):
                with contextlib.suppress(Exception):
                    self.poll()

        self._monitor = threading.Thread(target=loop, name="mt5-monitor", daemon=True)
        self._monitor.start()

    def stop_monitor(self) -> None:
        self._stop.set()
        if self._monitor is not None:
            self._monitor.join(timeout=5)
            self._monitor = None

    def _heartbeat(self, status: ConnectionStatus, now: float) -> None:
        with self._lock:
            self._next_heartbeat = now + self._heartbeat_seconds
        try:
            beat = self.gateway.run(
                "heartbeat",
                _read_heartbeat,
                timeout=self._heartbeat_timeout,
            )
        except MT5Timeout as error:
            busy = self.gateway.busy()
            if busy is not None and busy.name != "heartbeat":
                # Another long request holds the gateway (a first history download can take
                # 15 s). MT5 is working; the helper process ends a call that hangs (ADR 46).
                self._emit(
                    "DEBUG",
                    f"Heartbeat postponed: MT5 is busy with {busy.name} ({busy.seconds:.0f} s)",
                )
                return
            self._lost(status, now, error.title)
            return
        except MT5Error as error:
            self._lost(status, now, error.title)
            return
        if beat.terminal is None or not beat.terminal.connected or beat.account is None:
            reason = "the terminal closed" if beat.terminal is None else "no broker connection"
            self._lost(status, now, reason)
            return
        self._set(
            replace(
                status,
                account=beat.account,
                terminal=beat.terminal,
                open_positions=beat.positions,
            ),
            quiet=True,
        )

    def _lost(self, status: ConnectionStatus, now: float, reason: str) -> None:
        delay = self._backoff.delay(1)
        with self._lock:
            self._next_retry = now + delay
        self._emit("WARNING", f"MT5 connection lost ({reason}); reconnecting")
        if status.open_positions:
            self._emit(
                "CRITICAL",
                f"MT5 disconnected while {status.open_positions} position(s) are open. They stay "
                "protected by their server-side stop loss; new entries are paused.",
            )
        self._set(
            replace(
                status,
                state=ConnectionState.RECONNECTING,
                message=f"Connection lost: {reason}",
                attempt=1,
                retry_in_seconds=delay,
            ),
        )

    def _try_reconnect(self, status: ConnectionStatus, request: ConnectRequest, now: float) -> None:
        timeout = request.timeout_ms / 1000.0 + CONNECT_EXTRA_SECONDS
        try:
            beat = self.gateway.run(
                "reconnect",
                lambda mt5: _reconnect(mt5, request),
                timeout=timeout,
                arguments={"login": request.login, "server": request.server},
            )
            if beat.terminal is None or not beat.terminal.connected or beat.account is None:
                raise MT5Error("The terminal is not connected to the broker yet", "")
        except MT5Error as error:
            attempt = status.attempt + 1
            delay = self._backoff.delay(attempt)
            with self._lock:
                self._next_retry = self._clock() + delay
            self._emit("WARNING", f"Reconnect attempt {status.attempt} failed: {error.title}")
            self._set(
                replace(
                    status,
                    message=f"Reconnecting: {error.title}",
                    attempt=attempt,
                    retry_in_seconds=delay,
                ),
            )
            return
        self._emit("INFO", f"Reconnected to MT5 after {status.attempt} attempt(s)")
        with self._lock:
            self._next_heartbeat = self._clock() + self._heartbeat_seconds
        self._set(
            replace(
                status,
                state=ConnectionState.CONNECTED,
                message="Reconnected",
                account=beat.account,
                terminal=beat.terminal,
                open_positions=beat.positions,
                attempt=0,
                retry_in_seconds=None,
            ),
        )

    def _set(self, status: ConnectionStatus, quiet: bool = False) -> None:
        with self._lock:
            changed = status != self._status
            self._status = status
            listeners = list(self._listeners)
        if quiet and not changed:
            return
        for listener in listeners:
            with contextlib.suppress(Exception):
                listener(status)

    def _emit(self, level: str, message: str) -> None:
        if self._log is not None:
            with contextlib.suppress(Exception):
                self._log(level, message)
