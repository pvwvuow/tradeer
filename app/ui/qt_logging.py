"""Route Qt's own messages into the `ui` log category (spec E3).

Qt fatal messages also produce a crash report, because Qt aborts the process right after.
A message that repeats is logged once a minute with a count (ADR 48); fatal ones always pass.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PySide6.QtCore import QMessageLogContext, QtMsgType, qInstallMessageHandler

from app.observability.levels import LogLevel
from app.observability.repeats import RepeatFilter

QT_LEVELS: Mapping[str, LogLevel] = {
    "QtDebugMsg": LogLevel.DEBUG,
    "QtInfoMsg": LogLevel.INFO,
    "QtWarningMsg": LogLevel.WARNING,
    "QtCriticalMsg": LogLevel.ERROR,
    "QtSystemMsg": LogLevel.ERROR,
    "QtFatalMsg": LogLevel.CRITICAL,
}

QtLog = Callable[[LogLevel, str, dict[str, object]], None]


def qt_level(mode_name: str) -> LogLevel:
    return QT_LEVELS.get(mode_name, LogLevel.WARNING)


def install_qt_message_handler(
    log: QtLog,
    on_fatal: Callable[[str], None] | None = None,
    repeats: RepeatFilter | None = None,
) -> None:
    filter_ = repeats or RepeatFilter()

    def handler(mode: QtMsgType, context: QMessageLogContext, message: str) -> None:
        fatal = mode == QtMsgType.QtFatalMsg
        log_it, hidden = (True, 0) if fatal else filter_.check(message)
        if not log_it:
            return
        details: dict[str, object] = {}
        for name in ("category", "file", "line", "function"):
            value = getattr(context, name, None)
            if value:
                details[f"qt_{name}"] = value
        log(qt_level(mode.name), filter_.text(message, hidden), details)
        if fatal and on_fatal is not None:
            on_fatal(message)

    qInstallMessageHandler(handler)
