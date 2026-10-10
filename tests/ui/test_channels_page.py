"""Settings > Telegram channels (docs/SIGNAL_DESK.md 3.1 and 3.2, phase 21c3)."""

from pathlib import Path

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from app.channels.folder import ChannelMessage, Peer, PeerKind
from app.channels.reader import ChannelReader, ReaderStatus
from app.channels.secrets import CredentialSecrets
from app.channels.settings import ChannelSettingsSource, ProxyKind
from app.core.credentials import MemoryStore
from app.storage.channel_store import ChannelRepository
from app.storage.repositories import Store
from app.ui.channels_page import MISSING_TEXT, ChannelsContext, ChannelsPage
from tests.unit.storage_helpers import temporary_store

GOLD = Peer(-1001, "Gold Room", PeerKind.CHANNEL)
OLD = Peer(-1002, "Old channel", PeerKind.CHANNEL)


def context_for(store: Store, folder: Path) -> ChannelsContext:
    repository = ChannelRepository(store)
    source = ChannelSettingsSource(folder)
    secrets = CredentialSecrets(MemoryStore(), "default")
    reader = ChannelReader(lambda: source.settings, None, secrets, repository)
    return ChannelsContext(source, secrets, reader, repository, now=lambda: 100.0)


def test_saving_keeps_the_secrets_in_the_credential_store(qtbot: QtBot, tmp_path: Path) -> None:
    with temporary_store() as store:
        context = context_for(store, tmp_path)
        page = ChannelsPage(context)
        qtbot.addWidget(page)
        assert not page.code_button.isEnabled() and not page.refresh_button.isEnabled()
        page.enabled_box.setChecked(True)
        assert not page.save() and "api_id" in page.status.text()
        page.api_id.setText("12345")
        assert not page.save() and "api_hash" in page.status.text()
        page.api_hash.setText("0123abcd")
        page.proxy.setCurrentIndex(page.proxy.findData(ProxyKind.SOCKS5.value))
        assert not page.save() and "proxy host" in page.status.text()
        page.proxy_host.setText("127.0.0.1")
        page.proxy_port.setText("10808")
        page.proxy_secret.setText("pw-secret")
        assert page.save()
        settings = context.source.settings
        assert settings.enabled and settings.api_id == 12345
        assert settings.proxy is ProxyKind.SOCKS5 and settings.proxy_port == 10808
        assert context.secrets.api_hash() == "0123abcd"
        assert context.secrets.proxy_secret() == "pw-secret"
        text = (tmp_path / "channels.json").read_text(encoding="utf-8")
        assert "0123abcd" not in text and "pw-secret" not in text
        assert context.reader.state.status is ReaderStatus.MISSING  # no Telethon factory here
        assert page.status.text() == MISSING_TEXT
        assert page.api_hash.text() == "" and "saved" in page.api_hash.placeholderText()


def test_the_list_shows_the_folder_and_turns_channels_on(qtbot: QtBot, tmp_path: Path) -> None:
    with temporary_store() as store:
        context = context_for(store, tmp_path)
        repository = context.repository
        repository.sync_folder([GOLD, OLD], 100.0)
        repository.sync_folder([GOLD], 101.0)  # the old channel left the folder
        signal = ChannelMessage(GOLD.id, 5, 100.0, "XAUUSD buy 2345\nsl 2335")
        repository.save_message(signal, 100.0)
        page = ChannelsPage(context)
        qtbot.addWidget(page)
        assert page.channels.count() == 1
        item = page.channels.item(0)
        assert item is not None
        assert item.text().startswith("Gold Room  (channel, 1 message(s) stored)")
        assert item.text().endswith("XAUUSD buy 2345 sl 2335")
        assert item.checkState() == Qt.CheckState.Unchecked
        item.setCheckState(Qt.CheckState.Checked)
        assert repository.read_ids() == {GOLD.id}


def test_without_a_reader_the_page_says_so(qtbot: QtBot) -> None:
    page = ChannelsPage(None)
    qtbot.addWidget(page)
    assert "not available" in page.status.text()
    assert not page.save_button.isEnabled() and not page.save()
