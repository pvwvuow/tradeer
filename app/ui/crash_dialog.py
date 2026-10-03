"""Friendly crash dialog and the thread-safe bridge that shows it (spec E3)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.observability.crash_handler import CrashInfo
from app.ui.pages import styled_label

EXPLANATION = (
    "The app hit an unexpected error and saved a crash report. The report contains no "
    "passwords or keys. Restarting the app is the safest next step. If it happens again, "
    "attach the newest crash report to a comment on the GitHub pull request."
)


class CrashNotifier(QObject):
    """Receives crash notifications from any thread and re-emits them as a Qt signal.

    Connect `crashed` to a slot of a GUI-thread object with a queued connection, so the
    dialog always opens on the GUI thread, after the failing code has unwound.
    """

    crashed = Signal(str, str)

    def notify(self, info: CrashInfo) -> None:
        if info.notify_user:
            self.crashed.emit(str(info.path) if info.path is not None else "", info.summary)


class CrashDialog(QDialog):
    def __init__(
        self,
        report_path: Path | None,
        summary: str,
        crash_dir: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CrashDialog")
        self.setWindowTitle("Something went wrong")
        self.setMinimumWidth(520)
        self.report_path = report_path
        self.crash_dir = crash_dir
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(styled_label("Something went wrong", "brand"))
        layout.addWidget(styled_label(EXPLANATION, "muted", wrap=True))
        self.summary_box = QPlainTextEdit()
        self.summary_box.setReadOnly(True)
        self.summary_box.setMaximumHeight(96)
        self.summary_box.setPlainText(summary)
        layout.addWidget(self.summary_box)
        where = str(report_path) if report_path is not None else "No report file could be written."
        self.path_label = styled_label(where, "muted", wrap=True)
        layout.addWidget(self.path_label)
        buttons = QHBoxLayout()
        self.open_button = QPushButton("Open crash reports folder")
        self.copy_button = QPushButton("Copy report path")
        self.copy_button.setEnabled(report_path is not None)
        self.close_button = QPushButton("Close")
        self.close_button.setProperty("variant", "primary")
        self.close_button.setDefault(True)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.open_button.clicked.connect(self._open_folder)
        self.copy_button.clicked.connect(self._copy_path)
        self.close_button.clicked.connect(self.accept)

    def _open_folder(self) -> None:
        self.crash_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.crash_dir)))

    def _copy_path(self) -> None:
        if self.report_path is not None:
            QGuiApplication.clipboard().setText(str(self.report_path))
