"""Route Qt's own messages into the `ui` log category (spec E3).

Qt fatal messages also produce a crash report, because Qt aborts the process right after.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PySide6.QtCore import QMessageLogContext, QtMsgType, qInstallMessageHandler

from app.observability.levels import LogLevel

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


def install_qt_message_handler(log: QtLog, on_fatal: Callable[[str], None] | None = None) -> None:
    def handler(mode: QtMsgType, context: QMessageLogContext, message: str) -> None:
        details: dict[str, object] = {}
        for name in ("category", "file", "line", "function"):
            value = getattr(context, name, None)
            if value:
                details[f"qt_{name}"] = value
        log(qt_level(mode.name), message, details)
        if mode == QtMsgType.QtFatalMsg and on_fatal is not None:
            on_fatal(message)

    qInstallMessageHandler(handler)
