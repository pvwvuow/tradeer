"""Data & cloud sync (spec E1, D5): the local database, Supabase sign-in, sync and history.

Everything is saved on this PC first. Cloud sync to the user's own Supabase project is
optional. Network, MT5 and database work runs in background threads; results come back
through queued Qt signals, so this page never blocks the UI thread.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.mt5.errors import MT5Error
from app.mt5.history_sync import HistoryResult
from app.storage.cloud import CloudSetupError
from app.storage.remote import AuthSession, RemoteError
from app.storage.runtime import StorageRuntime
from app.storage.sync import SyncStatus
from app.ui.pages import PAGE_MARGIN, card_frame, styled_label

INTRO = (
    "Everything is saved on this PC first, in a local database with a daily backup (the last "
    "7 days are kept). Cloud sync to your own free Supabase project is optional: the app "
    "uploads in the background and catches up after the internet or the project was away."
)
CLOUD_HELP = (
    "Use the Project URL and the anon public key from Supabase (Project Settings > API), never "
    "the service key. The password is only used to sign in and is not saved."
)


class _Bridge(QObject):
    sync = Signal(object)
    cloud = Signal(object)
    history = Signal(object)


def _background(
    work: Callable[[], object],
    emit: Callable[[object], None],
    done: Callable[[], None] | None = None,
) -> None:
    """Run `work` in a daemon thread and emit its result (or exception) through a signal."""

    def run() -> None:
        try:
            result: object = work()
        except Exception as error:
            result = error
        finally:
            if done is not None:
                done()
        emit(result)

    threading.Thread(target=run, name="data-page", daemon=True).start()


def _error_text(error: BaseException) -> str:
    if isinstance(error, RemoteError | CloudSetupError):
        return str(error)
    if isinstance(error, MT5Error):
        return f"{error.title}. {error.fix}".strip()
    return f"{type(error).__name__}: {error}"


class DataPage(QWidget):
    def __init__(self, storage: StorageRuntime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.setObjectName("page_data")
        self.last_status: SyncStatus = storage.engine.status
        self.last_history: object = None
        self.last_cloud: object = None
        self.bridge = _Bridge(self)
        self.bridge.sync.connect(self.show_sync)
        self.bridge.cloud.connect(self._on_cloud)
        self.bridge.history.connect(self._on_history)
        storage.worker.add_listener(self.bridge.sync.emit)
        if storage.tracker is not None:
            storage.tracker.add_listener(self.bridge.history.emit)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(16)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        layout.addWidget(styled_label("Data & cloud sync", "title"))
        layout.addWidget(styled_label(INTRO, "muted", wrap=True))
        layout.addWidget(self._build_cloud_card())
        layout.addWidget(self._build_history_card())
        layout.addWidget(self._build_local_card())
        layout.addStretch(1)
        self._load_settings()
        self.show_sync(storage.engine.status)

    # Layout ----------------------------------------------------------------------------
    def _build_cloud_card(self) -> QWidget:
        card, layout = card_frame()
        layout.addWidget(styled_label("Cloud sync (Supabase)", "section"))
        layout.addWidget(styled_label(CLOUD_HELP, "muted", wrap=True))
        form = QFormLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setObjectName("CloudUrl")
        self.url_edit.setPlaceholderText("https://abcdefgh.supabase.co")
        self.key_edit = QLineEdit()
        self.key_edit.setObjectName("CloudKey")
        self.key_edit.setPlaceholderText("anon public key")
        self.email_edit = QLineEdit()
        self.email_edit.setObjectName("CloudEmail")
        self.password_edit = QLineEdit()
        self.password_edit.setObjectName("CloudPassword")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Project URL", self.url_edit)
        form.addRow("Anon key", self.key_edit)
        form.addRow("Email", self.email_edit)
        form.addRow("Password", self.password_edit)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.sign_in_button = QPushButton("Sign in")
        self.sign_in_button.setObjectName("CloudSignIn")
        self.sign_in_button.clicked.connect(self.sign_in)
        self.sign_up_button = QPushButton("Create account")
        self.sign_up_button.clicked.connect(self.sign_up)
        self.sign_out_button = QPushButton("Sign out")
        self.sign_out_button.clicked.connect(self.sign_out)
        self.upload_button = QPushButton("Upload now")
        self.upload_button.setObjectName("CloudUpload")
        self.upload_button.clicked.connect(self.upload_now)
        self.retry_button = QPushButton("Retry refused rows")
        self.retry_button.clicked.connect(self.retry_failed)
        for button in (
            self.sign_in_button,
            self.sign_up_button,
            self.sign_out_button,
            self.upload_button,
            self.retry_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.cloud_message = styled_label("", "muted", wrap=True)
        self.cloud_message.setObjectName("CloudMessage")
        layout.addWidget(self.cloud_message)
        self.sync_label = styled_label("", "muted", wrap=True)
        self.sync_label.setObjectName("SyncStatus")
        layout.addWidget(self.sync_label)
        return card

    def _build_history_card(self) -> QWidget:
        card, layout = card_frame()
        layout.addWidget(styled_label("Trade history from MT5", "section"))
        layout.addWidget(
            styled_label(
                "After every connection the app imports new deals and orders and rebuilds your "
                "trades, including manual ones. Importing again never creates duplicates.",
                "muted",
                wrap=True,
            ),
        )
        self.history_label = styled_label("No import yet in this session.", "muted", wrap=True)
        self.history_label.setObjectName("HistoryStatus")
        layout.addWidget(self.history_label)
        self.import_button = QPushButton("Import history now")
        self.import_button.setObjectName("HistoryImport")
        self.import_button.clicked.connect(self.import_history)
        self.import_button.setEnabled(self.storage.tracker is not None)
        layout.addWidget(self.import_button)
        return card

    def _build_local_card(self) -> QWidget:
        card, layout = card_frame()
        layout.addWidget(styled_label("On this PC", "section"))
        text = f"Database: {self.storage.db.path}\nBackups: {self.storage.backups}"
        layout.addWidget(styled_label(text, "muted", wrap=True))
        self.open_button = QPushButton("Open data folder")
        self.open_button.clicked.connect(self.open_data_folder)
        layout.addWidget(self.open_button)
        return card

    # Actions ---------------------------------------------------------------------------
    def sign_in(self) -> None:
        url, key, email = self._fields()
        password = self.password_edit.text()
        cloud = self.storage.cloud
        self._busy("Signing in...")
        _background(lambda: cloud.sign_in(url, key, email, password), self.bridge.cloud.emit)

    def sign_up(self) -> None:
        url, key, email = self._fields()
        password = self.password_edit.text()
        cloud = self.storage.cloud

        def work() -> object:
            session = cloud.sign_up(url, key, email, password)
            return session if session is not None else "confirm"

        self._busy("Creating the cloud account...")
        _background(work, self.bridge.cloud.emit)

    def sign_out(self) -> None:
        cloud = self.storage.cloud

        def work() -> object:
            cloud.sign_out()
            return "signed_out"

        _background(work, self.bridge.cloud.emit)

    def upload_now(self) -> None:
        self.storage.worker.nudge()

    def retry_failed(self) -> None:
        store = self.storage.store

        def work() -> object:
            return f"{store.retry_failed():,} refused row(s) will be uploaded again."

        _background(work, self.bridge.cloud.emit, self.storage.db.release)

    def import_history(self) -> None:
        tracker = self.storage.tracker
        if tracker is None:
            return
        self.import_button.setEnabled(False)
        self.history_label.setText("Importing the trade history from MT5...")
        _background(tracker.import_history, lambda result: None, self.storage.db.release)

    def open_data_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.storage.db.path.parent)))

    # Results ---------------------------------------------------------------------------
    def show_sync(self, status: object) -> None:
        if not isinstance(status, SyncStatus):
            return
        self.last_status = status
        lines = [status.status_bar_text(), status.message]
        if status.email:
            lines.append(f"Signed in as {status.email}.")
        if status.last_upload:
            lines.append(f"Last upload: {status.last_upload.replace('T', ' ')[:19]} UTC.")
        if status.retry_in_seconds:
            lines.append(f"Next try in {status.retry_in_seconds:.0f} s.")
        self.sync_label.setText("\n".join(lines))
        self.sign_out_button.setEnabled(self.storage.cloud.settings.enabled)

    def _on_cloud(self, result: object) -> None:
        self.last_cloud = result
        self._set_buttons(True)
        self.password_edit.clear()
        if isinstance(result, BaseException):
            self.cloud_message.setText(f"Cloud: {_error_text(result)}")
            return
        if isinstance(result, AuthSession):
            self.cloud_message.setText(f"Signed in as {result.email}. Uploading in the background.")
        elif result == "confirm":
            self.cloud_message.setText(
                "Account created. Confirm the email Supabase sent you, then press Sign in.",
            )
        elif result == "signed_out":
            self.cloud_message.setText("Signed out. Rows keep waiting on this PC.")
        else:
            self.cloud_message.setText(str(result))
        self.storage.worker.nudge()

    def _on_history(self, result: object) -> None:
        self.last_history = result
        self.import_button.setEnabled(self.storage.tracker is not None)
        if isinstance(result, HistoryResult):
            self.history_label.setText(result.text())
            self.storage.worker.nudge()
        elif isinstance(result, BaseException):
            self.history_label.setText(f"History import failed: {_error_text(result)}")

    # Helpers ---------------------------------------------------------------------------
    def _fields(self) -> tuple[str, str, str]:
        return self.url_edit.text(), self.key_edit.text(), self.email_edit.text()

    def _load_settings(self) -> None:
        settings = self.storage.cloud.settings
        self.url_edit.setText(settings.url)
        self.key_edit.setText(settings.anon_key)
        self.email_edit.setText(settings.email)

    def _busy(self, message: str) -> None:
        self._set_buttons(False)
        self.cloud_message.setText(message)

    def _set_buttons(self, enabled: bool) -> None:
        for button in (self.sign_in_button, self.sign_up_button):
            button.setEnabled(enabled)
