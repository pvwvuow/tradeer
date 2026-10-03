"""Start and stop observability for one app run: logs, crash reports and the watchdog."""

from __future__ import annotations

import platform
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.__version__ import __version__
from app.core.paths import CRASH_REPORTS_DIR_NAME, LOGS_DIR_NAME, app_data_dir
from app.observability.buffer import RecentLogBuffer
from app.observability.categories import LogCategory
from app.observability.context import SESSION_ID
from app.observability.controls import LogControls
from app.observability.crash_handler import CrashInfo, CrashReporter, package_versions
from app.observability.files import LogFileWriter
from app.observability.levels import LevelRegistry
from app.observability.logger import LogPipeline, audit, get_logger, log_startup
from app.observability.masking import MASKER, SecretMasker
from app.observability.watchdog import FreezeEvent, RecoveryEvent, Watchdog


@dataclass
class Observability:
    profile: str
    log_dir: Path
    crash_dir: Path
    masker: SecretMasker
    pipeline: LogPipeline
    controls: LogControls
    crash_reporter: CrashReporter
    watchdog: Watchdog

    def shutdown(self) -> None:
        get_logger(LogCategory.APP).info("Application stopping")
        self.watchdog.stop()
        self.crash_reporter.uninstall()
        self.pipeline.flush()
        self.pipeline.stop()


def _log_crash(info: CrashInfo) -> None:
    log = get_logger(LogCategory.APP).bind(
        crash_source=info.source,
        crash_thread=info.thread,
        crash_report=str(info.path) if info.path is not None else None,
    )
    if info.exc_info is not None:
        log.opt(exception=info.exc_info).critical("Uncaught error: {}", info.summary)
    else:
        log.critical("Crash report: {}", info.summary)


def _audit_change(action: str, before: object, after: object) -> None:
    audit(action, before=before, after=after)


class _FreezeHandler:
    def __init__(self, reporter: CrashReporter) -> None:
        self._reporter = reporter
        self._log = get_logger(LogCategory.APP)

    def frozen(self, event: FreezeEvent) -> None:
        details = {
            "worker": event.worker,
            "silent_seconds": round(event.silent_seconds, 1),
            "timeout_seconds": event.timeout_seconds,
            "restarted": event.restarted,
            "restart_error": event.restart_error,
            "stack": event.stack,
        }
        self._log.bind(**details).critical(
            "Worker {} did not respond for {:.0f} s",
            event.worker,
            event.silent_seconds,
        )
        self._reporter.report_message(
            f"Worker {event.worker} froze for {event.silent_seconds:.0f} s",
            source="watchdog",
            details=details,
        )

    def recovered(self, event: RecoveryEvent) -> None:
        self._log.bind(worker=event.worker).warning(
            "Worker {} responded again after {:.0f} s",
            event.worker,
            event.silent_seconds,
        )


def start_observability(
    profile: str,
    *,
    data_dir: Path | None = None,
    console: bool = False,
    enqueue: bool = True,
    startup_details: Mapping[str, object] | None = None,
) -> Observability:
    base = data_dir if data_dir is not None else app_data_dir(profile)
    log_dir = base / LOGS_DIR_NAME
    crash_dir = base / CRASH_REPORTS_DIR_NAME
    registry = LevelRegistry()
    buffer = RecentLogBuffer()
    pipeline = LogPipeline(
        LogFileWriter(log_dir),
        buffer,
        registry,
        MASKER,
        enqueue=enqueue,
        console=console,
    )
    pipeline.start()
    reporter = CrashReporter(crash_dir, recent_logs=buffer.last, masker=MASKER)
    reporter.add_listener(_log_crash)
    reporter.install()
    freezes = _FreezeHandler(reporter)
    watchdog = Watchdog(on_freeze=freezes.frozen, on_recover=freezes.recovered)
    watchdog.start()
    controls = LogControls(buffer, registry, log_dir, crash_dir, audit=_audit_change)
    details = {
        "app_version": __version__,
        "python": platform.python_version(),
        "os": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "profile": profile,
        "session_id": SESSION_ID,
        "packages": package_versions(),
        "log_dir": str(log_dir),
        "log_levels": registry.snapshot(),
        **dict(startup_details or {}),
    }
    log_startup(details)
    return Observability(
        profile=profile,
        log_dir=log_dir,
        crash_dir=crash_dir,
        masker=MASKER,
        pipeline=pipeline,
        controls=controls,
        crash_reporter=reporter,
        watchdog=watchdog,
    )
