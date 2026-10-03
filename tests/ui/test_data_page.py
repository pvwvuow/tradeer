from pathlib import Path

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from app.core.credentials import MemoryStore
from app.core.profiles import AccountProfile
from app.core.ui_prefs import UiPrefs
from app.mt5.connection import ConnectionService
from app.mt5.gateway import MT5Gateway
from app.mt5.history_sync import HistoryImporter
from app.storage.cloud import token_name
from app.storage.runtime import StorageRuntime, open_storage
from app.storage.tracker import AccountTracker
from app.ui.data_page import DataPage
from app.ui.main_window import MainWindow
from tests.fakes.fake_mt5 import DEFAULT_PATH, FakeAccount, FakeMT5, make_closed_trade
from tests.fakes.fake_supabase import EMAIL, PASSWORD, FakeSupabase

URL = "https://abcdefgh.supabase.co"
KEY = "sb_publishable_test"


def make_storage(tmp_path: Path, cloud: FakeSupabase, secrets: MemoryStore) -> StorageRuntime:
    return open_storage(
        "pytest",
        tmp_path,
        "session-ui",
        credentials=secrets,
        client_factory=lambda url, key: cloud,
    )


def test_signing_in_uploads_the_waiting_rows_and_updates_the_status_bar(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    cloud, secrets = FakeSupabase(), MemoryStore()
    storage = make_storage(tmp_path, cloud, secrets)
    storage.start_session(app_version="0.1.0", mode="paper", settings={})
    window = MainWindow(UiPrefs(), tmp_path, None, None, storage)
    qtbot.addWidget(window)
    storage.start()
    try:
        page = window.data_page
        assert page is not None
        qtbot.waitUntil(lambda: "1 waiting" in window.sync_label.text(), timeout=10_000)
        assert window.sync_label.text().startswith("Cloud: off")
        page.url_edit.setText(URL)
        page.key_edit.setText(KEY)
        page.email_edit.setText(EMAIL)
        page.password_edit.setText(PASSWORD)
        qtbot.mouseClick(page.sign_in_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: cloud.count("sessions") == 1, timeout=10_000)
        qtbot.waitUntil(lambda: window.sync_label.text() == "Cloud: up to date", timeout=10_000)
        assert page.password_edit.text() == ""
        assert "Signed in as trader@example.com" in page.cloud_message.text()
        assert secrets.get(token_name("pytest")) is not None
        assert PASSWORD not in (tmp_path / "cloud.json").read_text(encoding="utf-8")
    finally:
        storage.close()


def test_a_wrong_cloud_password_shows_the_reason(qtbot: QtBot, tmp_path: Path) -> None:
    storage = make_storage(tmp_path, FakeSupabase(), MemoryStore())
    page = DataPage(storage)
    qtbot.addWidget(page)
    try:
        page.url_edit.setText(URL)
        page.key_edit.setText(KEY)
        page.email_edit.setText(EMAIL)
        page.password_edit.setText("wrong")
        qtbot.mouseClick(page.sign_in_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: page.last_cloud is not None, timeout=10_000)
        assert "Invalid login credentials" in page.cloud_message.text()
        assert storage.engine.session is None
    finally:
        storage.close()


def test_importing_the_history_shows_the_rebuilt_trades(qtbot: QtBot, tmp_path: Path) -> None:
    fake = FakeMT5()
    fake.deals = make_closed_trade(7, opened=1_726_000_000, closed=1_726_000_600, profit=5.0)
    gateway = MT5Gateway(lambda: fake, default_timeout=10.0, idle_seconds=0.05)
    gateway.start()
    account = FakeAccount()
    profile = AccountProfile(login=account.login, server=account.server, terminal_path=DEFAULT_PATH)
    service = ConnectionService(
        gateway,
        lambda: profile.request(account.password),
        path_exists=lambda path: True,
    )
    storage = make_storage(tmp_path, FakeSupabase(), MemoryStore())
    try:
        assert service.connect().connected
        storage.tracker = AccountTracker(
            storage.store,
            storage.session_id,
            status=lambda: service.status,
            history=HistoryImporter(gateway, storage.store),
        )
        page = DataPage(storage)
        qtbot.addWidget(page)
        qtbot.mouseClick(page.import_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: "rebuilt" in page.history_label.text(), timeout=10_000)
        assert "1 trades rebuilt" in page.history_label.text()
        assert page.import_button.isEnabled()
    finally:
        storage.close()
        gateway.stop()
