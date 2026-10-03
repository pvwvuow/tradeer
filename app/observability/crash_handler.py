"""Crash reports (spec E3): an uncaught exception becomes `crash_reports/crash_<time>.json`.

Hooks: `sys.excepthook`, `threading.excepthook` and, in the UI, the Qt message handler.
`faulthandler` also records native crashes (for example an access violation inside a C
extension) in `fatal_errors.log`. Every report is masked: it never contains a raw secret.
A report holds the stack, the last 200 log entries, app state, versions and OS details.
"""

from __future__ import annotations

import contextlib
import faulthandler
import importlib.metadata
import json
import platform
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

from app.__version__ import __version__
from app.observability.context import snapshot
from app.observability.masking import MASKER, SecretMasker
from app.observability.records import format_exception, qualified_name, utc_iso

REPORT_VERSION = 1
REPORT_PREFIX = "crash_"
FATAL_LOG_NAME = "fatal_errors.log"
RECENT_LOG_LINES = 200
KEEP_REPORTS = 50
MAX_REPORTS_PER_MINUTE = 5
PACKAGES: tuple[str, ...] = ("PySide6", "loguru", "pydantic", "numpy", "MetaTrader5")

ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]
StateProvider = Callable[[], Mapping[str, object]]
RecentLogs = Callable[[int], Sequence[Mapping[str, Any]]]


class CrashTestError(RuntimeError):
    """Raised on purpose to prove that crash reporting works."""


@dataclass(frozen=True)
class CrashInfo:
    path: Path | None
    source: str
    thread: str
    summary: str
    exc_info: ExcInfo | None
    notify_user: bool


def package_versions(names: Sequence[str] = PACKAGES) -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            # Frozen builds may lack package metadata; use an already imported module.
            module = sys.modules.get(name)
            version = getattr(module, "__version__", None)
            versions[name] = str(version) if version is not None else "unknown"
    return versions


def environment() -> dict[str, Any]:
    return {
        "app": __version__,
        "python": platform.python_version(),
        "python_bits": platform.architecture()[0],
        "frozen": bool(getattr(sys, "frozen", False)),
        "packages": package_versions(),
    }


def os_details() -> dict[str, str]:
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
    }


def _safe_str(value: object) -> str:
    try:
        return str(value)
    except Exception:
        return f"<{type(value).__name__} without a readable message>"


class CrashReporter:
    def __init__(
        self,
        directory: Path,
        recent_logs: RecentLogs | None = None,
        masker: SecretMasker = MASKER,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        keep: int = KEEP_REPORTS,
        max_per_minute: int = MAX_REPORTS_PER_MINUTE,
        native_crashes: bool = True,
    ) -> None:
        self.directory = directory
        self.last_report: Path | None = None
        self.suppressed = 0
        self._recent_logs = recent_logs
        self._masker = masker
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._keep = keep
        self._max_per_minute = max_per_minute
        self._native_crashes = native_crashes
        self._lock = threading.RLock()
        self._listeners: list[Callable[[CrashInfo], None]] = []
        self._state: StateProvider | None = None
        self._recent: deque[float] = deque()
        self._installed = False
        self._previous_sys_hook = sys.excepthook
        self._previous_thread_hook = threading.excepthook
        self._fatal_file: TextIO | None = None

    @property
    def installed(self) -> bool:
        return self._installed

    def add_listener(self, listener: Callable[[CrashInfo], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[CrashInfo], None]) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def set_state_provider(self, provider: StateProvider | None) -> None:
        """`provider` is called from any thread, so it must not touch Qt widgets."""
        self._state = provider

    def install(self) -> None:
        with self._lock:
            if self._installed:
                return
            self._previous_sys_hook = sys.excepthook
            self._previous_thread_hook = threading.excepthook
            sys.excepthook = self._sys_hook
            threading.excepthook = self._thread_hook
            if self._native_crashes:
                self._enable_faulthandler()
            self._installed = True

    def uninstall(self) -> None:
        with self._lock:
            if not self._installed:
                return
            if sys.excepthook == self._sys_hook:
                sys.excepthook = self._previous_sys_hook
            if threading.excepthook == self._thread_hook:
                threading.excepthook = self._previous_thread_hook
            self._disable_faulthandler()
            self._installed = False

    def reports(self) -> list[Path]:
        """Existing crash reports, newest first."""
        if not self.directory.exists():
            return []
        return sorted(self.directory.glob(f"{REPORT_PREFIX}*.json"), reverse=True)

    def report_exception(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
        *,
        source: str,
        thread: str | None = None,
        notify_user: bool = True,
    ) -> Path | None:
        message = _safe_str(exc_value) if exc_value is not None else ""
        summary = self._masker.mask(f"{exc_type.__name__}: {message}" if message else "")
        exception = {
            "type": qualified_name(exc_type),
            "message": self._masker.mask(message),
            "stack": self._masker.mask(format_exception(exc_type, exc_value, exc_tb)),
        }
        exc_info = (exc_type, exc_value, exc_tb) if exc_value is not None else None
        return self._report(
            source=source,
            thread=thread or threading.current_thread().name,
            summary=summary or exc_type.__name__,
            exception=exception,
            details=None,
            exc_info=exc_info,
            notify_user=notify_user,
        )

    def report_message(
        self,
        message: str,
        *,
        source: str,
        details: Mapping[str, object] | None = None,
        notify_user: bool = False,
    ) -> Path | None:
        """A report without an exception, for Qt fatal errors and watchdog freezes."""
        return self._report(
            source=source,
            thread=threading.current_thread().name,
            summary=self._masker.mask(message),
            exception=None,
            details=details,
            exc_info=None,
            notify_user=notify_user,
        )

    def _sys_hook(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            self._previous_sys_hook(exc_type, exc_value, exc_tb)
            return
        self.report_exception(exc_type, exc_value, exc_tb, source="sys.excepthook")

    def _thread_hook(self, args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        thread = args.thread.name if args.thread is not None else "unknown thread"
        self.report_exception(
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
            source="threading.excepthook",
            thread=thread,
        )

    def _report(
        self,
        *,
        source: str,
        thread: str,
        summary: str,
        exception: Mapping[str, str] | None,
        details: Mapping[str, object] | None,
        exc_info: ExcInfo | None,
        notify_user: bool,
    ) -> Path | None:
        path: Path | None = None
        try:
            with self._lock:
                if self._throttled():
                    self.suppressed += 1
                else:
                    path = self._write(source, thread, summary, exception, details)
                    self.last_report = path
        except Exception:
            # A crash hook must never raise. The console line below still tells the story.
            path = None
        self._print_to_console(summary, exception, path)
        info = CrashInfo(path, source, thread, summary, exc_info, notify_user)
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            with contextlib.suppress(Exception):
                listener(info)
        return path

    def _throttled(self) -> bool:
        now = self._monotonic()
        while self._recent and now - self._recent[0] > 60.0:
            self._recent.popleft()
        if len(self._recent) >= self._max_per_minute:
            return True
        self._recent.append(now)
        return False

    def _write(
        self,
        source: str,
        thread: str,
        summary: str,
        exception: Mapping[str, str] | None,
        details: Mapping[str, object] | None,
    ) -> Path:
        moment = self._clock()
        report = {
            "report_version": REPORT_VERSION,
            "time": utc_iso(moment),
            "source": source,
            "thread": thread,
            "summary": summary,
            "context": snapshot(),
            "exception": exception,
            "details": dict(details or {}),
            "state": self._collect_state(),
            "versions": environment(),
            "os": os_details(),
            "recent_logs": self._collect_logs(),
        }
        text = json.dumps(self._masker.mask_value(report), ensure_ascii=False, indent=2)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._unique_path(moment)
        partial = path.with_name(f"{path.name}.part")
        partial.write_text(text, encoding="utf-8")
        partial.replace(path)
        self._prune()
        return path

    def _collect_state(self) -> dict[str, Any]:
        provider = self._state
        if provider is None:
            return {}
        try:
            return dict(provider())
        except Exception as error:
            return {"error": f"state unavailable ({type(error).__name__})"}

    def _collect_logs(self) -> list[Any]:
        if self._recent_logs is None:
            return []
        try:
            return list(self._recent_logs(RECENT_LOG_LINES))
        except Exception:
            return []

    def _unique_path(self, moment: datetime) -> Path:
        stamp = moment.astimezone(UTC).strftime("%Y%m%d-%H%M%S-%f")
        path = self.directory / f"{REPORT_PREFIX}{stamp}.json"
        counter = 1
        while path.exists():
            path = self.directory / f"{REPORT_PREFIX}{stamp}-{counter}.json"
            counter += 1
        return path

    def _prune(self) -> None:
        for old in self.reports()[self._keep :]:
            with contextlib.suppress(OSError):
                old.unlink()

    def _print_to_console(
        self,
        summary: str,
        exception: Mapping[str, str] | None,
        path: Path | None,
    ) -> None:
        stream = sys.stderr
        if stream is None:
            return
        where = f"Crash report: {path}" if path is not None else "No crash report was written."
        stack = exception.get("stack", "") if exception else ""
        with contextlib.suppress(Exception):
            stream.write(f"{stack.rstrip()}\nUncaught error: {summary}\n{where}\n".lstrip())
            stream.flush()

    def _enable_faulthandler(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._fatal_file = (self.directory / FATAL_LOG_NAME).open("a", encoding="utf-8")
            faulthandler.enable(self._fatal_file, all_threads=True)
        except (OSError, RuntimeError, ValueError):
            self._close_fatal_file()

    def _disable_faulthandler(self) -> None:
        if self._fatal_file is not None:
            faulthandler.disable()
            self._close_fatal_file()

    def _close_fatal_file(self) -> None:
        if self._fatal_file is not None:
            with contextlib.suppress(OSError):
                self._fatal_file.close()
            self._fatal_file = None
