"""Settings > Updates (spec J2) and the update banner of the main window.

The page shows the installed version and what the update service is doing, with Check now,
Download, Restart to update and Roll back; and the settings (automatic checks, background
download, pause). The banner is non-modal: it appears when a version is ready or available
and never blocks anything.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.observability.logger import audit
from app.ui.pages import PAGE_MARGIN, card_frame, styled_label
from app.updates.service import UpdateService, UpdateSnapshot, UpdateStatus
from app.updates.settings import UpdateSettings, UpdateSettingsSource
from app.updates.velopack_backend import REPO_URL
from app.updates.versions import tag_for

INTRO = (
    "New versions come from the project's GitHub releases. Only the changed parts are "
    "downloaded, checked, and installed when you restart the app. Your settings, trades and "
    "history stay where they are."
)


def _never() -> bool:
    return False


@dataclass
class UpdatesContext:
    service: UpdateService
    settings: UpdateSettingsSource
    restart: Callable[[], bool] = _never  # set by the main window: confirm, apply and close


class _Bridge(QObject):
    snapshot = Signal(object)


def status_text(snapshot: UpdateSnapshot) -> str:
    if snapshot.paused and snapshot.status not in (UpdateStatus.READY, UpdateStatus.UNAVAILABLE):
        return "Updates are paused. " + snapshot.message
    return snapshot.message


def banner_text(snapshot: UpdateSnapshot) -> str:
    """The banner line, or "" when there is nothing to say."""
    if snapshot.crash_loop and snapshot.rollback_to and not snapshot.offered:
        return snapshot.message
    if snapshot.paused and not snapshot.ready:
        return ""
    if snapshot.ready:
        verb = "Go back to" if snapshot.downgrade else "Version"
        tail = "" if snapshot.downgrade else " is ready"
        return f"{verb} {snapshot.version}{tail}: restart the app to install it."
    if snapshot.status is UpdateStatus.AVAILABLE:
        extra = " It is a major version: read the release notes first." if snapshot.major else ""
        return f"Version {snapshot.version} is available.{extra}"
    if snapshot.status is UpdateStatus.DOWNLOADING:
        return f"Downloading version {snapshot.version}… {snapshot.progress}%"
    return ""


class UpdatesPage(QWidget):
    def __init__(self, context: UpdatesContext | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page_updates")
        self.context = context
        self.last: UpdateSnapshot | None = None
        self.bridge = _Bridge()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(12)
        layout.addWidget(styled_label("Updates", "title"))
        layout.addWidget(styled_label(INTRO, "muted", wrap=True))
        card, card_layout = card_frame()
        self.version_label = styled_label("", "heading")
        self.version_label.setObjectName("UpdatesVersion")
        self.status_label = styled_label("", "body", wrap=True)
        self.status_label.setObjectName("UpdatesStatus")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        card_layout.addWidget(self.version_label)
        card_layout.addWidget(self.status_label)
        card_layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.check_button = QPushButton("Check now")
        self.check_button.setObjectName("UpdatesCheck")
        self.check_button.clicked.connect(self.check_now)
        self.download_button = QPushButton("Download")
        self.download_button.setObjectName("UpdatesDownload")
        self.download_button.clicked.connect(self.download)
        self.restart_button = QPushButton("Restart to update")
        self.restart_button.setObjectName("UpdatesRestart")
        self.restart_button.setProperty("variant", "accent")
        self.restart_button.clicked.connect(self.restart)
        self.notes_button = QPushButton("Release notes")
        self.notes_button.clicked.connect(self.open_notes)
        self.rollback_button = QPushButton("Roll back")
        self.rollback_button.setObjectName("UpdatesRollback")
        self.rollback_button.clicked.connect(self.rollback)
        for button in (
            self.check_button,
            self.download_button,
            self.restart_button,
            self.notes_button,
            self.rollback_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        card_layout.addLayout(buttons)
        layout.addWidget(card)
        self.notes = QTextBrowser()
        self.notes.setObjectName("UpdatesNotes")
        self.notes.setOpenExternalLinks(True)
        self.notes.setMinimumHeight(140)
        self.notes.setVisible(False)
        layout.addWidget(self.notes)
        layout.addWidget(styled_label("Settings", "heading"))
        self.auto_check = QCheckBox("Check for new versions automatically")
        background = "Download new versions in the background (never a major one)"
        self.auto_download = QCheckBox(background)
        self.paused = QCheckBox("Pause updates (for example during a challenge or a live session)")
        hours_row = QHBoxLayout()
        hours_row.addWidget(styled_label("Check every", "body"))
        self.hours = QSpinBox()
        self.hours.setRange(1, 48)
        self.hours.setSuffix(" h")
        hours_row.addWidget(self.hours)
        hours_row.addStretch(1)
        self.save_button = QPushButton("Save update settings")
        self.save_button.clicked.connect(self.save)
        for box in (self.auto_check, self.auto_download, self.paused):
            layout.addWidget(box)
        layout.addLayout(hours_row)
        layout.addWidget(self.save_button)
        layout.addStretch(1)
        self.bridge.snapshot.connect(self.show_snapshot)
        if context is None:
            self.version_label.setText("Updates are not running.")
            for widget in (self.check_button, self.download_button, self.save_button):
                widget.setEnabled(False)
            self.restart_button.setEnabled(False)
            self.rollback_button.setVisible(False)
            return
        self.show_settings(context.settings.settings)
        context.service.add_listener(self.bridge.snapshot.emit)
        self.show_snapshot(context.service.snapshot)

    def show_settings(self, settings: UpdateSettings) -> None:
        self.auto_check.setChecked(settings.auto_check)
        self.auto_download.setChecked(settings.auto_download)
        self.paused.setChecked(settings.paused)
        self.hours.setValue(settings.check_hours)

    def show_snapshot(self, snapshot: object) -> None:
        if not isinstance(snapshot, UpdateSnapshot):
            return
        self.last = snapshot
        self.version_label.setText(f"Installed version: {snapshot.current}")
        self.status_label.setText(status_text(snapshot))
        busy = snapshot.status in (UpdateStatus.CHECKING, UpdateStatus.DOWNLOADING)
        usable = snapshot.status is not UpdateStatus.UNAVAILABLE
        self.progress.setVisible(snapshot.status is UpdateStatus.DOWNLOADING)
        self.progress.setValue(snapshot.progress)
        self.check_button.setEnabled(usable and not busy)
        self.download_button.setVisible(snapshot.status is UpdateStatus.AVAILABLE)
        self.download_button.setEnabled(not busy)
        self.restart_button.setVisible(snapshot.ready)
        self.restart_button.setText(
            f"Restart and go back to {snapshot.version}"
            if snapshot.downgrade
            else "Restart to update",
        )
        self.notes_button.setVisible(bool(snapshot.version))
        self.rollback_button.setVisible(usable and bool(snapshot.rollback_to) and not busy)
        self.rollback_button.setText(f"Roll back to {snapshot.rollback_to}")
        self.notes.setVisible(bool(snapshot.notes))
        if snapshot.notes:
            self.notes.setMarkdown(snapshot.notes)

    def check_now(self) -> None:
        if self.context is not None:
            self.context.service.check_now()

    def download(self) -> None:
        if self.context is not None:
            self.context.service.download()

    def rollback(self) -> None:
        if self.context is not None:
            self.context.service.rollback()

    def restart(self) -> bool:
        return self.context is not None and self.context.restart()

    def open_notes(self) -> None:
        last = self.last
        if last is not None and last.version:
            QDesktopServices.openUrl(QUrl(f"{REPO_URL}/releases/tag/{tag_for(last.version)}"))

    def save(self) -> bool:
        context = self.context
        if context is None:
            return False
        old = context.settings.settings
        new = UpdateSettings(
            auto_check=self.auto_check.isChecked(),
            check_hours=self.hours.value(),
            auto_download=self.auto_download.isChecked(),
            paused=self.paused.isChecked(),
        )
        context.settings.save(new)
        audit("update settings saved", before=old.model_dump(), after=new.model_dump())
        context.service.settings_changed()
        self.status_label.setText("Saved. " + status_text(context.service.snapshot))
        return True


class UpdateBanner(QFrame):
    """A thin non-modal bar under the top bar; hidden when there is nothing to say."""

    def __init__(self, context: UpdatesContext | None, open_page: Callable[[], None]) -> None:
        super().__init__()
        self.setObjectName("UpdateBanner")
        self.context = context
        self._dismissed = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        self.text = styled_label("", "body")
        self.text.setObjectName("UpdateBannerText")
        self.restart_button = QPushButton("Restart to update")
        self.restart_button.setObjectName("UpdateBannerRestart")
        self.restart_button.setProperty("variant", "accent")
        self.details_button = QPushButton("Details")
        self.details_button.clicked.connect(open_page)
        self.later_button = QPushButton("Later")
        self.later_button.clicked.connect(self.dismiss)
        layout.addWidget(self.text, 1)
        layout.addWidget(self.restart_button)
        layout.addWidget(self.details_button)
        layout.addWidget(self.later_button)
        self.bridge = _Bridge()
        self.bridge.snapshot.connect(self.show_snapshot)
        self.setVisible(False)
        if context is not None:
            self.restart_button.clicked.connect(context.restart)
            context.service.add_listener(self.bridge.snapshot.emit)
            self.show_snapshot(context.service.snapshot)

    def show_snapshot(self, snapshot: object) -> None:
        if not isinstance(snapshot, UpdateSnapshot):
            return
        text = banner_text(snapshot)
        self.text.setText(text)
        self.restart_button.setVisible(snapshot.ready)
        self.setVisible(bool(text) and text != self._dismissed)

    def dismiss(self) -> None:
        self._dismissed = self.text.text()
        self.setVisible(False)
