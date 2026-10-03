"""The update service (spec J2, J4): checks in the background, downloads on request (or by
itself for a non-major version), and applies the update when the user restarts the app.

Everything slow runs in the `updates` thread; the UI only queues commands and receives
snapshots. A failed automatic check is quiet (a log line); a failed manual check says why.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum

from app.updates.backend import UpdateBackend, UpdateError, UpdateOffer
from app.updates.settings import UpdateSettingsSource
from app.updates.state import StartInfo
from app.updates.versions import is_major_update, is_newer

FIRST_CHECK_SECONDS = 60.0
Log = Callable[[str, str], None]
Record = Callable[[str, str, str], None]


class UpdateStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    READY = "ready"
    ERROR = "error"


class Command(StrEnum):
    CHECK = "check"
    AUTO_CHECK = "auto_check"
    DOWNLOAD = "download"
    ROLLBACK = "rollback"
    STOP = "stop"


@dataclass(frozen=True)
class UpdateSnapshot:
    status: UpdateStatus
    current: str
    message: str
    version: str = ""  # the offered version
    notes: str = ""
    size: int = 0
    delta: bool = False
    major: bool = False
    downgrade: bool = False
    progress: int = 0
    last_check: float | None = None
    rollback_to: str = ""
    crash_loop: bool = False
    paused: bool = False

    @property
    def ready(self) -> bool:
        return self.status is UpdateStatus.READY

    @property
    def offered(self) -> bool:
        return self.status in (UpdateStatus.AVAILABLE, UpdateStatus.READY)


class UpdateService:
    def __init__(
        self,
        backend: UpdateBackend,
        settings: UpdateSettingsSource,
        start: StartInfo,
        log: Log | None = None,
        record: Record | None = None,
        clock: Callable[[], float] = time.monotonic,
        first_check: float = FIRST_CHECK_SECONDS,
    ) -> None:
        self._backend = backend
        self._settings = settings
        self._log: Log = log if log is not None else _no_log
        self._record: Record = record if record is not None else _no_record
        self._clock = clock
        self._lock = threading.Lock()
        self._listeners: list[Callable[[UpdateSnapshot], None]] = []
        self._commands: queue.Queue[Command] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._offer: UpdateOffer | None = None
        self._next_check = clock() + first_check
        current = backend.current_version() or start.version
        reason = backend.unavailable_reason()
        rollback = start.previous if start.previous and start.previous != current else ""
        if reason:
            snapshot = UpdateSnapshot(UpdateStatus.UNAVAILABLE, current, reason)
        elif start.crash_loop and rollback:
            text = f"Version {current} did not start properly twice. You can go back to {rollback}."
            snapshot = UpdateSnapshot(UpdateStatus.IDLE, current, text, crash_loop=True)
        elif start.updated_from:
            snapshot = UpdateSnapshot(
                UpdateStatus.UP_TO_DATE,
                current,
                f"Updated from {start.updated_from} to {current}.",
            )
        else:
            snapshot = UpdateSnapshot(UpdateStatus.IDLE, current, "Not checked yet.")
        self._snapshot = replace(
            snapshot,
            rollback_to=rollback,
            paused=settings.settings.paused,
        )
        if start.updated_from:
            self._log("INFO", f"Update installed: {start.updated_from} -> {current}")
            self._record("update installed", start.updated_from, current)
        if start.crash_loop:
            self._log("WARNING", f"Version {current} ended early twice; rollback offered")

    # ----- public, any thread -----

    @property
    def snapshot(self) -> UpdateSnapshot:
        with self._lock:
            return self._snapshot

    def add_listener(self, listener: Callable[[UpdateSnapshot], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def start(self) -> None:
        if self._thread is not None or self.snapshot.status is UpdateStatus.UNAVAILABLE:
            return
        self._thread = threading.Thread(target=self._run, name="updates", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        thread = self._thread
        if thread is None:
            return
        self._commands.put(Command.STOP)
        thread.join(timeout)
        self._thread = None

    def check_now(self) -> None:
        self._commands.put(Command.CHECK)

    def download(self) -> None:
        self._commands.put(Command.DOWNLOAD)

    def rollback(self) -> None:
        self._commands.put(Command.ROLLBACK)

    def settings_changed(self) -> None:
        settings = self._settings.settings
        self._publish(replace(self.snapshot, paused=settings.paused))
        self._next_check = min(self._next_check, self._clock() + settings.check_hours * 3600.0)

    def apply(self) -> str:
        """Hand the downloaded update to the installer; the caller then closes the app.

        Returns "" on success, else the reason it cannot be applied now.
        """
        snapshot = self.snapshot
        offer = self._offer
        if not snapshot.ready or offer is None:
            return "No downloaded update is waiting."
        try:
            self._backend.apply_after_exit(offer)
        except UpdateError as error:
            self._log("ERROR", f"Update could not be started: {error}")
            self._publish(replace(snapshot, status=UpdateStatus.ERROR, message=str(error)))
            return str(error)
        what = "rollback" if offer.downgrade else "update"
        self._log("INFO", f"Restarting to install {offer.version} ({what})")
        self._record(f"{what} started", snapshot.current, offer.version)
        return ""

    # ----- the worker (also called directly by tests) -----

    def handle(self, command: Command) -> None:
        if command in (Command.CHECK, Command.AUTO_CHECK):
            self._check(manual=command is Command.CHECK)
        elif command is Command.DOWNLOAD:
            self._download()
        elif command is Command.ROLLBACK:
            self._rollback()

    def due(self) -> bool:
        settings = self._settings.settings
        return settings.auto_check and not settings.paused and self._clock() >= self._next_check

    def _run(self) -> None:
        while True:
            wait = max(1.0, min(300.0, self._next_check - self._clock()))
            try:
                command = self._commands.get(timeout=wait)
            except queue.Empty:
                if self.due():
                    command = Command.AUTO_CHECK
                else:
                    continue
            if command is Command.STOP:
                return
            try:
                self.handle(command)
            except Exception as error:  # a bug here must never take the app down
                self._log("ERROR", f"Update step {command.value} failed: {error}")

    def _check(self, manual: bool) -> None:
        before = self.snapshot
        if before.status in (UpdateStatus.DOWNLOADING, UpdateStatus.UNAVAILABLE):
            return
        settings = self._settings.settings
        self._next_check = self._clock() + settings.check_hours * 3600.0
        self._publish(replace(before, status=UpdateStatus.CHECKING, message="Checking…"))
        self._log("INFO", "Checking for updates" + (" (asked by the user)" if manual else ""))
        try:
            offer = self._backend.check()
        except UpdateError as error:
            self._log("WARNING" if not manual else "ERROR", f"Update check failed: {error}")
            if manual:
                self._publish(replace(before, status=UpdateStatus.ERROR, message=str(error)))
            else:
                self._publish(replace(before, message="The last automatic check failed."))
            return
        now = time.time()
        if offer is None or not is_newer(offer.version, before.current):
            self._offer = None
            self._log("INFO", f"Up to date ({before.current})")
            self._publish(
                replace(
                    before,
                    status=UpdateStatus.UP_TO_DATE,
                    message=f"You have the newest version ({before.current}).",
                    version="",
                    last_check=now,
                ),
            )
            return
        if before.ready and before.version == offer.version:
            self._publish(replace(before, last_check=now))
            return
        major = is_major_update(offer.version, before.current)
        self._offer = offer
        kind = "changes only" if offer.delta else "full package"
        self._log("INFO", f"Update found: {offer.version} ({_size(offer.size)}, {kind})")
        self._publish(
            replace(
                before,
                status=UpdateStatus.AVAILABLE,
                message=f"Version {offer.version} is available ({_size(offer.size)}, {kind}).",
                version=offer.version,
                notes=offer.notes,
                size=offer.size,
                delta=offer.delta,
                major=major,
                downgrade=False,
                progress=0,
                last_check=now,
            ),
        )
        if settings.auto_download and not settings.paused and not major:
            self._download()

    def _download(self) -> None:
        before = self.snapshot
        offer = self._offer
        if offer is None or before.status is not UpdateStatus.AVAILABLE:
            return
        self._publish(
            replace(before, status=UpdateStatus.DOWNLOADING, message="Downloading…", progress=0),
        )
        self._log("INFO", f"Downloading {offer.version}")

        def progress(percent: int) -> None:
            value = max(0, min(100, int(percent)))
            current = self.snapshot
            if current.status is UpdateStatus.DOWNLOADING and value != current.progress:
                self._publish(replace(current, progress=value, message=f"Downloading… {value}%"))

        try:
            self._backend.download(offer, progress)
        except UpdateError as error:
            self._log("ERROR", f"Update download failed: {error}")
            self._publish(replace(before, status=UpdateStatus.ERROR, message=str(error)))
            return
        verb = "go back to" if offer.downgrade else "install"
        self._log("INFO", f"Downloaded and verified {offer.version}")
        self._publish(
            replace(
                self.snapshot,
                status=UpdateStatus.READY,
                message=f"Ready: restart the app to {verb} {offer.version}.",
                progress=100,
            ),
        )

    def _rollback(self) -> None:
        before = self.snapshot
        target = before.rollback_to
        if not target or before.status in (UpdateStatus.DOWNLOADING, UpdateStatus.UNAVAILABLE):
            return
        self._log("WARNING", f"Rollback to {target} asked")
        self._publish(replace(before, status=UpdateStatus.CHECKING, message=f"Finding {target}…"))
        try:
            offer = self._backend.check_version(target)
        except UpdateError as error:
            self._log("ERROR", f"Rollback failed: {error}")
            self._publish(replace(before, status=UpdateStatus.ERROR, message=str(error)))
            return
        if offer is None:
            text = f"Version {target} was not found in the releases."
            self._publish(replace(before, status=UpdateStatus.ERROR, message=text))
            return
        self._offer = replace(offer, downgrade=True)
        self._publish(
            replace(
                before,
                status=UpdateStatus.AVAILABLE,
                message=f"Going back to {offer.version} ({_size(offer.size)}).",
                version=offer.version,
                notes=offer.notes,
                size=offer.size,
                delta=False,
                downgrade=True,
            ),
        )
        self._download()

    def _publish(self, snapshot: UpdateSnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot
            listeners = list(self._listeners)
        for listener in listeners:
            listener(snapshot)


def _no_log(level: str, message: str) -> None:
    return None


def _no_record(action: str, before: str, after: str) -> None:
    return None


def _size(size: int) -> str:
    if size <= 0:
        return "size unknown"
    if size < 1024 * 1024:
        return f"{max(1, size // 1024)} KB"
    return f"{size / (1024 * 1024):.1f} MB"
