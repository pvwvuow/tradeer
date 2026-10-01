"""Composition root: parse arguments, run a self-test, or start the desktop app."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from functools import partial
from typing import TYPE_CHECKING

from app.cli import CliOptions, emit_report, parse_args, run_self_check, self_check_main
from app.core.paths import app_data_dir
from app.observability.categories import LogCategory
from app.observability.levels import LogLevel

if TYPE_CHECKING:
    from app.observability.runtime import Observability

APP_NAME = "MT5 Trading Workstation"
UI_HEARTBEAT_MS = 1000
UI_FREEZE_SECONDS = 10.0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    options = parse_args(args)
    if options.self_check:
        return self_check_main(options)
    if options.crash_test:
        return crash_test_main(options)
    return run_gui(options, args)


def _has_console() -> bool:
    stream = sys.stderr
    return not getattr(sys, "frozen", False) and stream is not None and stream.isatty()


def crash_test_main(options: CliOptions) -> int:
    from app.observability.crash_test import run_crash_test
    from app.observability.logger import get_logger
    from app.observability.runtime import start_observability

    observability = start_observability(options.profile)
    try:
        log = get_logger(LogCategory.APP)
        result = run_crash_test(
            observability.crash_reporter,
            observability.masker,
            emit=partial(log.info, "{}"),
            flush=observability.pipeline.flush,
            log_dir=observability.log_dir,
        )
    finally:
        observability.shutdown()
    emit_report("\n".join(result.lines()), options.report_file)
    return 0 if result.ok else 1


def run_gui(options: CliOptions, qt_args: list[str]) -> int:
    from app.domain.config import TradingDefaults
    from app.observability.runtime import start_observability

    defaults = TradingDefaults().model_dump(mode="json")
    observability = start_observability(
        options.profile,
        console=_has_console(),
        startup_details={"defaults": defaults},
    )
    try:
        return _run_window(observability, options, qt_args)
    finally:
        observability.shutdown()


def _run_window(observability: Observability, options: CliOptions, qt_args: list[str]) -> int:
    # Qt is imported lazily so that `--self-check` can report a broken Qt install.
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.core.ui_prefs import load_prefs
    from app.observability.logger import get_logger
    from app.ui.crash_dialog import CrashNotifier
    from app.ui.main_window import MainWindow
    from app.ui.qt_logging import install_qt_message_handler

    ui_log = get_logger(LogCategory.UI)
    reporter = observability.crash_reporter

    def log_qt(level: LogLevel, text: str, details: dict[str, object]) -> None:
        ui_log.bind(**details).log(level.name, "Qt: {}", text)

    def qt_fatal(text: str) -> None:
        reporter.report_message(f"Qt fatal error: {text}", source="qt", notify_user=False)
        observability.pipeline.flush()

    install_qt_message_handler(log_qt, on_fatal=qt_fatal)
    application = QApplication([sys.argv[0], *qt_args])
    application.setApplicationName(APP_NAME)
    font = QFont()
    font.setFamilies(["Inter", "Segoe UI Variable", "Segoe UI"])
    application.setFont(font)
    if getattr(sys, "frozen", False):
        ok, report = run_self_check()
        if not ok:
            # A production build must fail loudly, never fall back to fake data (spec I2).
            ui_log.critical("Startup self-check failed:\n{}", report)
            QMessageBox.critical(None, APP_NAME, report)
            return 1
    try:
        prefs_dir = app_data_dir(options.profile)
        window = MainWindow(load_prefs(prefs_dir), prefs_dir, observability.controls)
    except Exception as error:
        reporter.report_exception(type(error), error, error.__traceback__, source="startup")
        QMessageBox.critical(None, APP_NAME, "The app could not start. A crash report was saved.")
        return 1
    notifier = CrashNotifier()
    notifier.crashed.connect(
        window.show_crash_dialog,
        type=Qt.ConnectionType.QueuedConnection,
    )
    reporter.add_listener(notifier.notify)
    reporter.set_state_provider(window.crash_state)
    observability.watchdog.register("ui", UI_FREEZE_SECONDS)
    heartbeat = QTimer(window)
    heartbeat.timeout.connect(partial(observability.watchdog.beat, "ui"))
    heartbeat.start(UI_HEARTBEAT_MS)
    window.show()
    ui_log.info("Main window shown")
    try:
        return int(application.exec())
    finally:
        heartbeat.stop()
        observability.watchdog.unregister("ui")
        reporter.remove_listener(notifier.notify)
        reporter.set_state_provider(None)


if __name__ == "__main__":
    raise SystemExit(main())
