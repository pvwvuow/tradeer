from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from pytestqt.qtbot import QtBot

from app.core.credentials import MemoryStore, credential_name
from app.core.profiles import load_account
from app.core.ui_prefs import UiPrefs
from app.mt5.connection import ConnectionService, ConnectionState
from app.mt5.gateway import MT5Gateway
from app.mt5.terminals import TerminalInstall
from app.ui.connection_page import ConnectionContext, ConnectionPage
from app.ui.main_window import MainWindow
from tests.fakes.fake_mt5 import DEFAULT_PATH, FakeAccount, FakeMT5

ACCOUNT = FakeAccount()


def make_context(tmp_path: Path, fake: FakeMT5) -> ConnectionContext:
    gateway = MT5Gateway(lambda: fake, default_timeout=10.0, idle_seconds=0.05)
    gateway.start()
    service = ConnectionService(
        gateway,
        lambda: load_account(tmp_path).request(),
        path_exists=lambda path: True,
    )
    terminal = TerminalInstall(DEFAULT_PATH, "Demo Broker MetaTrader 5", servers=(ACCOUNT.server,))
    return ConnectionContext(
        profile="pytest",
        profile_dir=tmp_path,
        gateway=gateway,
        service=service,
        credentials=MemoryStore(),
        discover=lambda: [terminal],
        launch_profile=lambda name: True,
        path_exists=lambda path: True,
    )


def fill_and_connect(qtbot: QtBot, page: ConnectionPage, password: str) -> None:
    page.login_edit.setText(str(ACCOUNT.login))
    page.password_edit.setText(password)
    page.server_box.setCurrentText(ACCOUNT.server)
    qtbot.mouseClick(page.connect_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.last_report is not None, timeout=10_000)


def test_connecting_shows_the_checklist_saves_the_profile_and_password(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path, FakeMT5())
    page = ConnectionPage(context)
    qtbot.addWidget(page)
    try:
        assert page.terminal_box.currentData() == DEFAULT_PATH
        assert page.server_box.findText(ACCOUNT.server) >= 0
        fill_and_connect(qtbot, page, ACCOUNT.password)
        assert page.last_report is not None and page.last_report.connected
        texts = [
            page.checklist_rows.itemAt(index).widget().text()
            for index in range(page.checklist_rows.count())
        ]
        assert any(text.startswith("\u2713  Logged in") for text in texts)
        assert ACCOUNT.password not in "\n".join(texts)
        saved = load_account(tmp_path)
        assert (saved.login, saved.server, saved.terminal_path) == (
            ACCOUNT.login,
            ACCOUNT.server,
            DEFAULT_PATH,
        )
        name = credential_name("pytest", ACCOUNT.login, ACCOUNT.server)
        assert context.credentials.get(name) == ACCOUNT.password
        assert page.password_edit.text() == ""
        qtbot.waitUntil(lambda: "EURUSD.m" in page.quotes_label.text(), timeout=5000)
        assert context.service.status.state is ConnectionState.CONNECTED
    finally:
        context.gateway.stop()


def test_a_wrong_password_is_not_saved(qtbot: QtBot, tmp_path: Path) -> None:
    context = make_context(tmp_path, FakeMT5())
    page = ConnectionPage(context)
    qtbot.addWidget(page)
    try:
        fill_and_connect(qtbot, page, "wrong-password")
        name = credential_name("pytest", ACCOUNT.login, ACCOUNT.server)
        assert context.credentials.get(name) is None
        assert context.service.status.state is ConnectionState.FAILED
    finally:
        context.gateway.stop()


def test_investor_login_switches_the_status_bar_to_analysis_only(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path, FakeMT5())
    window = MainWindow(UiPrefs(), tmp_path, None, context)
    qtbot.addWidget(window)
    page = window.connection_page
    assert page is not None
    try:
        fill_and_connect(qtbot, page, ACCOUNT.investor_password)
        qtbot.waitUntil(lambda: window.mode_badge.text() == "ANALYSIS-ONLY", timeout=5000)
        assert "DemoBroker-Server" in window.connection_label.text()
        assert "only watches and never trades" in window.home.status_line.text()
        assert window.crash_state()["mt5"] == "connected"
    finally:
        context.gateway.stop()


def test_diagnostics_report_can_be_copied_without_the_password(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path, FakeMT5())
    page = ConnectionPage(context)
    qtbot.addWidget(page)
    try:
        page.login_edit.setText(str(ACCOUNT.login))
        page.password_edit.setText(ACCOUNT.password)
        page.server_box.setCurrentText(ACCOUNT.server)
        qtbot.mouseClick(page.diagnostics_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: page.last_diagnostics is not None, timeout=10_000)
        qtbot.mouseClick(page.copy_button, Qt.MouseButton.LeftButton)
        copied = QGuiApplication.clipboard().text()
        assert "connection diagnostics" in copied
        assert "EURUSD.m M15" in copied
        assert ACCOUNT.password not in copied
        assert ACCOUNT.password not in page.report_box.toPlainText()
    finally:
        context.gateway.stop()
