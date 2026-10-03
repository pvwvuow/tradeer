"""Windows notifications from the system tray (spec C14, F3). `notify` may be called from any
thread; the notice is shown on the UI thread through a queued Qt signal. Without a tray (CI,
some remote desktops) the notices are only kept in `shown`."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QStyle, QSystemTrayIcon, QWidget

from app.notify.events import Notice

SHOW_MS = 8_000


class TrayNotifier(QObject):
    message = Signal(str, str, bool)

    def __init__(self, window: QWidget, parent: QObject | None = None) -> None:
        super().__init__(parent or window)
        self.window = window
        self.shown: list[tuple[str, str]] = []
        self.tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            icon = window.windowIcon()
            if icon.isNull():
                icon = window.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
            tray = QSystemTrayIcon(QIcon(icon), self)
            tray.setToolTip(window.windowTitle())
            tray.activated.connect(lambda _reason: self.bring_up())
            tray.show()
            self.tray = tray
        self.message.connect(self._show, Qt.ConnectionType.QueuedConnection)

    def notify(self, notice: Notice) -> None:
        self.message.emit(notice.title, notice.text, notice.kind.urgent)

    def _show(self, title: str, text: str, urgent: bool) -> None:
        self.shown.append((title, text))
        del self.shown[:-50]
        if self.tray is None:
            return
        icon = (
            QSystemTrayIcon.MessageIcon.Critical
            if urgent
            else QSystemTrayIcon.MessageIcon.Information
        )
        self.tray.showMessage(title, text[:250], icon, SHOW_MS)

    def bring_up(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def hide(self) -> None:
        if self.tray is not None:
            self.tray.hide()
