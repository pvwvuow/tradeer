"""Account & connection (spec C1, I3, I4): terminal, login, live checklist, prices, diagnostics.

All MT5 work runs in the gateway thread. Results come back through Qt signals, so this page
never blocks the UI thread.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QRegularExpression, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core.credentials import (
    CredentialError,
    CredentialStore,
    credential_name,
    read_password,
    save_password,
)
from app.core.paths import profiles_root
from app.core.profiles import AccountProfile, list_profiles, load_account, save_account
from app.mt5.api import MT5Api
from app.mt5.checklist import ChecklistReport, CheckStatus, ConnectRequest, read_quotes
from app.mt5.connection import ConnectionService, ConnectionStatus
from app.mt5.diagnostics import DiagnosticsReport, run_diagnostics
from app.mt5.gateway import MT5Gateway
from app.mt5.models import Quote
from app.mt5.terminals import TerminalInstall, system_terminals
from app.ui.pages import PAGE_MARGIN, card_frame, styled_label

QUOTE_REFRESH_MS = 1000
DIAGNOSTICS_TIMEOUT_SECONDS = 180.0
INTRO = (
    "The app talks to the MetaTrader 5 terminal on this PC, never directly to your broker. "
    "Keep MT5 open and logged in. Your password is stored only in Windows Credential Manager."
)


@dataclass
class ConnectionContext:
    profile: str
    profile_dir: Path
    gateway: MT5Gateway
    service: ConnectionService
    credentials: CredentialStore
    discover: Callable[[], list[TerminalInstall]] = system_terminals
    elevated: bool | None = None
    launch_profile: Callable[[str], bool] | None = None
    path_exists: Callable[[str], bool] = os.path.exists


def launch_profile_instance(profile: str) -> bool:
    """Start another app window for `profile` (one instance per profile)."""
    arguments = ["--profile", profile]
    if not getattr(sys, "frozen", False):
        arguments = ["-m", "app", *arguments]
    result: object = QProcess.startDetached(sys.executable, arguments)
    # PySide6 returns (started, process id) for the static overload.
    return bool(result[0]) if isinstance(result, tuple) else bool(result)


class _Bridge(QObject):
    checklist = Signal(object)
    quotes = Signal(object)
    diagnostics = Signal(object)
    status = Signal(object)


def _deliver(future: Future[Any], emit: Callable[[object], None]) -> None:
    """Emit the result (or the exception) of `future` through a Qt signal, on any thread."""

    def done(finished: Future[Any]) -> None:
        try:
            emit(finished.result())
        except BaseException as error:
            emit(error)

    future.add_done_callback(done)


class ConnectionPage(QWidget):
    def __init__(self, context: ConnectionContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        self.setObjectName("page_settings")
        self.account = load_account(context.profile_dir)
        self.terminals: list[TerminalInstall] = []
        self.last_report: ChecklistReport | None = None
        self.last_diagnostics: DiagnosticsReport | None = None
        self.last_quotes: tuple[Quote, ...] = ()
        self._quotes_pending = False
        self._typed_password = ""
        self.bridge = _Bridge(self)
        self.bridge.checklist.connect(self._on_checklist)
        self.bridge.quotes.connect(self._on_quotes)
        self.bridge.diagnostics.connect(self._on_diagnostics)
        self.bridge.status.connect(self.show_status)
        context.service.add_listener(self.bridge.status.emit)

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

        layout.addWidget(styled_label("Account & connection", "title"))
        layout.addWidget(styled_label(INTRO, "muted", wrap=True))
        layout.addWidget(self._build_profile_card())
        layout.addWidget(self._build_terminal_card())
        layout.addWidget(self._build_account_card())
        layout.addLayout(self._build_buttons())
        self.status_label = styled_label("Not connected", "muted", wrap=True)
        self.status_label.setObjectName("ConnectionStatus")
        layout.addWidget(self.status_label)
        checklist_card, self.checklist_layout = card_frame()
        self.checklist_layout.addWidget(styled_label("Checklist", "section"))
        self.checklist_rows = QVBoxLayout()
        self.checklist_layout.addLayout(self.checklist_rows)
        layout.addWidget(checklist_card)
        quotes_card, quotes_layout = card_frame()
        quotes_layout.addWidget(styled_label("Live prices (updated every second)", "section"))
        self.quotes_label = styled_label("Connect to see live prices.", "muted", wrap=True)
        self.quotes_label.setObjectName("LiveQuotes")
        quotes_layout.addWidget(self.quotes_label)
        layout.addWidget(quotes_card)
        layout.addWidget(styled_label("Diagnostics report", "section"))
        self.report_box = QPlainTextEdit()
        self.report_box.setObjectName("DiagnosticsReport")
        self.report_box.setReadOnly(True)
        self.report_box.setPlaceholderText(
            "Run diagnostics to get a full report without passwords. Copy it into a comment on "
            "the GitHub pull request when something does not work.",
        )
        self.report_box.setMinimumHeight(180)
        layout.addWidget(self.report_box)
        layout.addStretch(1)

        self.quote_timer = QTimer(self)
        self.quote_timer.setInterval(QUOTE_REFRESH_MS)
        self.quote_timer.timeout.connect(self.poll_quotes)
        self.quote_timer.start()
        self.refresh_terminals()
        self._load_account_fields()
        self.show_status(context.service.status)

    # Layout ----------------------------------------------------------------------------
    def _build_profile_card(self) -> QWidget:
        card, layout = card_frame()
        layout.addWidget(styled_label(f"Profile: {self.context.profile}", "section"))
        hint = (
            "Each profile is one account with its own settings and logs. To run several "
            "accounts at the same time, open each profile in its own window."
        )
        layout.addWidget(styled_label(hint, "muted", wrap=True))
        row = QHBoxLayout()
        self.profile_box = QComboBox()
        self.profile_box.setObjectName("ProfileBox")
        self.profile_box.setEditable(True)
        others = [name for name in list_profiles(profiles_root()) if name != self.context.profile]
        self.profile_box.addItems(others)
        self.profile_box.setCurrentText("")
        edit = self.profile_box.lineEdit()
        if edit is not None:
            edit.setPlaceholderText("Profile name, for example demo2")
        self.open_profile_button = QPushButton("Open profile in a new window")
        self.open_profile_button.clicked.connect(self.open_profile)
        row.addWidget(self.profile_box, 1)
        row.addWidget(self.open_profile_button)
        layout.addLayout(row)
        return card

    def _build_terminal_card(self) -> QWidget:
        card, layout = card_frame()
        layout.addWidget(styled_label("1. MetaTrader 5 terminal", "section"))
        row = QHBoxLayout()
        self.terminal_box = QComboBox()
        self.terminal_box.setObjectName("TerminalBox")
        self.terminal_box.currentIndexChanged.connect(self._on_terminal_changed)
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self.browse_terminal)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_terminals)
        row.addWidget(self.terminal_box, 1)
        row.addWidget(self.browse_button)
        row.addWidget(self.refresh_button)
        layout.addLayout(row)
        return card

    def _build_account_card(self) -> QWidget:
        card, layout = card_frame()
        layout.addWidget(styled_label("2. Account", "section"))
        form = QFormLayout()
        self.login_edit = QLineEdit()
        self.login_edit.setObjectName("LoginEdit")
        self.login_edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{1,12}")))
        self.login_edit.setPlaceholderText("Account number, for example 51234567")
        self.password_edit = QLineEdit()
        self.password_edit.setObjectName("PasswordEdit")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.server_box = QComboBox()
        self.server_box.setObjectName("ServerBox")
        self.server_box.setEditable(True)
        self.auto_connect_box = QCheckBox("Connect automatically when the app starts")
        form.addRow("Login", self.login_edit)
        form.addRow("Password", self.password_edit)
        form.addRow("Server", self.server_box)
        layout.addLayout(form)
        layout.addWidget(self.auto_connect_box)
        note = (
            "The investor (read-only) password works too: the app then only analyzes and never "
            "trades."
        )
        layout.addWidget(styled_label(note, "muted", wrap=True))
        return card

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("ConnectButton")
        self.connect_button.setProperty("variant", "primary")
        self.recheck_button = QPushButton("Re-check")
        self.disconnect_button = QPushButton("Disconnect")
        self.diagnostics_button = QPushButton("Run diagnostics")
        self.diagnostics_button.setObjectName("DiagnosticsButton")
        self.copy_button = QPushButton("Copy report")
        self.connect_button.clicked.connect(self.connect_to_mt5)
        self.recheck_button.clicked.connect(self.connect_to_mt5)
        self.disconnect_button.clicked.connect(self.disconnect_from_mt5)
        self.diagnostics_button.clicked.connect(self.run_diagnostics)
        self.copy_button.clicked.connect(self.copy_report)
        for button in (
            self.connect_button,
            self.recheck_button,
            self.disconnect_button,
            self.diagnostics_button,
            self.copy_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        return row

    # Terminal and account fields --------------------------------------------------------
    def refresh_terminals(self) -> None:
        saved = self.account.terminal_path or self.selected_terminal_path()
        self.terminals = list(self.context.discover())
        self.terminal_box.blockSignals(True)
        self.terminal_box.clear()
        for terminal in self.terminals:
            self.terminal_box.addItem(terminal.label(), terminal.path)
        if saved and self.terminal_box.findData(saved) < 0:
            self.terminal_box.addItem(saved, saved)
        if not self.terminals and not saved:
            self.terminal_box.addItem("No MetaTrader 5 found: use Browse...", "")
        index = self.terminal_box.findData(saved) if saved else 0
        self.terminal_box.setCurrentIndex(max(0, index))
        self.terminal_box.blockSignals(False)
        self._on_terminal_changed()

    def selected_terminal_path(self) -> str:
        if not hasattr(self, "terminal_box"):
            return ""
        return str(self.terminal_box.currentData() or "")

    def browse_terminal(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select terminal64.exe",
            "",
            "MetaTrader 5 (terminal64.exe);;All files (*)",
        )
        if path:
            if self.terminal_box.findData(path) < 0:
                self.terminal_box.addItem(path, path)
            self.terminal_box.setCurrentIndex(self.terminal_box.findData(path))

    def _on_terminal_changed(self) -> None:
        path = self.selected_terminal_path()
        servers: list[str] = []
        for terminal in self.terminals:
            if terminal.path == path:
                servers = list(terminal.servers)
        current = self.server_box.currentText() or self.account.server
        self.server_box.clear()
        self.server_box.addItems(servers)
        if current and current not in servers:
            self.server_box.addItem(current)
        self.server_box.setCurrentText(current)

    def _load_account_fields(self) -> None:
        if self.account.login is not None:
            self.login_edit.setText(str(self.account.login))
        self.server_box.setCurrentText(self.account.server)
        self.auto_connect_box.setChecked(self.account.auto_connect)
        if self.account.configured:
            self.password_edit.setPlaceholderText("Saved in Windows Credential Manager")
        else:
            self.password_edit.setPlaceholderText("Your MT5 password (master or investor)")

    def build_request(self) -> ConnectRequest | None:
        login_text = self.login_edit.text().strip()
        server = self.server_box.currentText().strip()
        login = int(login_text) if login_text else None
        if login is not None and not server:
            self.status_label.setText("Enter the server name, exactly as shown in MT5.")
            return None
        password = self.password_edit.text()
        if login is not None and not password:
            try:
                stored = read_password(
                    self.context.credentials,
                    credential_name(self.context.profile, login, server),
                )
            except CredentialError as error:
                self.status_label.setText(str(error))
                return None
            password = stored or ""
        self._typed_password = self.password_edit.text()
        self.account = AccountProfile(
            login=login,
            server=server,
            terminal_path=self.selected_terminal_path(),
            symbols=list(self.account.symbols),
            auto_connect=self.auto_connect_box.isChecked(),
        )
        save_account(self.context.profile_dir, self.account)
        return self.account.request(password)

    # Actions ---------------------------------------------------------------------------
    def connect_to_mt5(self) -> None:
        request = self.build_request()
        if request is None:
            return
        self._set_busy(True, "Connecting to MT5. Starting the terminal can take up to a minute.")
        _deliver(self.context.service.connect_async(request), self.bridge.checklist.emit)

    def disconnect_from_mt5(self) -> None:
        threading.Thread(target=self.context.service.disconnect, daemon=True).start()

    def run_diagnostics(self) -> None:
        request = self.build_request()
        if request is None:
            return
        self._set_busy(True, "Running diagnostics. This can take a minute.")
        elevated = self.context.elevated
        exists = self.context.path_exists

        def work(mt5: MT5Api) -> DiagnosticsReport:
            return run_diagnostics(mt5, request, path_exists=exists, elevated=elevated)

        future = self.context.gateway.submit(
            "diagnostics",
            work,
            timeout=DIAGNOSTICS_TIMEOUT_SECONDS,
        )
        _deliver(future, self.bridge.diagnostics.emit)

    def copy_report(self) -> None:
        if self.last_diagnostics is not None:
            text = self.last_diagnostics.text()
        elif self.last_report is not None:
            text = "\n".join(self.last_report.lines())
        else:
            self.status_label.setText("Nothing to copy yet. Press Connect or Run diagnostics.")
            return
        QGuiApplication.clipboard().setText(text)
        self.status_label.setText("Report copied. It contains no password.")

    def open_profile(self) -> None:
        name = self.profile_box.currentText().strip()
        if not name or self.context.launch_profile is None:
            self.status_label.setText("Type or pick a profile name first.")
            return
        if self.context.launch_profile(name):
            self.status_label.setText(f"Opening profile {name} in a new window.")
        else:
            self.status_label.setText(f"Could not open profile {name}.")

    def poll_quotes(self) -> None:
        report = self.last_report
        if self._quotes_pending or report is None or not report.symbols:
            return
        if not self.context.service.status.connected:
            return
        symbols = report.symbols
        self._quotes_pending = True

        def work(mt5: MT5Api) -> tuple[Quote, ...]:
            return read_quotes(mt5, symbols)

        _deliver(self.context.gateway.submit("quotes", work, timeout=5.0), self.bridge.quotes.emit)

    # Results ---------------------------------------------------------------------------
    def _on_checklist(self, result: object) -> None:
        self._set_busy(False)
        if isinstance(result, BaseException):
            self.status_label.setText(f"Connection failed: {result}")
            return
        if not isinstance(result, ChecklistReport):
            return
        self.last_report = result
        self.show_checklist(result)
        login_ok = result.status("login") is CheckStatus.OK
        if login_ok and self._typed_password and self.account.login is not None:
            name = credential_name(self.context.profile, self.account.login, self.account.server)
            try:
                save_password(self.context.credentials, name, self._typed_password)
            except CredentialError as error:
                self.status_label.setText(f"Connected, but the password was not saved: {error}")
            else:
                self.password_edit.clear()
                self.password_edit.setPlaceholderText("Saved in Windows Credential Manager")
        self._typed_password = ""
        if result.quotes:
            self._on_quotes(result.quotes)

    def _on_quotes(self, result: object) -> None:
        self._quotes_pending = False
        if not isinstance(result, tuple):
            return
        quotes = tuple(quote for quote in result if isinstance(quote, Quote))
        self.last_quotes = quotes
        if quotes:
            self.quotes_label.setText("\n".join(quote.text() for quote in quotes))
        else:
            self.quotes_label.setText("No prices right now. The market may be closed.")

    def _on_diagnostics(self, result: object) -> None:
        self._set_busy(False)
        if isinstance(result, BaseException):
            self.status_label.setText(f"Diagnostics failed: {result}")
            return
        if not isinstance(result, DiagnosticsReport):
            return
        self.last_diagnostics = result
        self.last_report = result.checklist
        self.show_checklist(result.checklist)
        self.report_box.setPlainText(result.text())
        self.status_label.setText("Diagnostics finished. Press Copy report to share it.")

    def show_checklist(self, report: ChecklistReport) -> None:
        while self.checklist_rows.count():
            item = self.checklist_rows.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for check in report.items:
            text = f"{check.status.mark}  {check.title}"
            if check.value:
                text = f"{text}: {check.value}"
            row = styled_label(text, "status", wrap=True)
            row.setProperty("check", check.status.value)
            self.checklist_rows.addWidget(row)
            if check.fix and check.status in (CheckStatus.WARN, CheckStatus.FAIL):
                self.checklist_rows.addWidget(styled_label(f"Fix: {check.fix}", "muted", wrap=True))

    def show_status(self, status: object) -> None:
        if not isinstance(status, ConnectionStatus):
            return
        text = status.message
        if status.connected and status.account is not None:
            text = f"{status.message}: {status.account.summary()}"
            if status.analysis_only:
                text += ". Read-only login: Analysis-only mode."
        self.status_label.setText(text)
        if not status.connected:
            self._quotes_pending = False

    def _set_busy(self, busy: bool, message: str = "") -> None:
        for button in (self.connect_button, self.recheck_button, self.diagnostics_button):
            button.setEnabled(not busy)
        if message:
            self.status_label.setText(message)
