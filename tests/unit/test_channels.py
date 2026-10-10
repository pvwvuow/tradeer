"""Telegram channels, phase 21c (docs/SIGNAL_DESK.md 3.1 to 3.3): the settings and the proxy,
the folder, the local message store, and the read-only rule."""

import re
from pathlib import Path

from app.channels.folder import (
    TEXT_LIMIT,
    ChannelMessage,
    Folder,
    Peer,
    PeerKind,
    clean_text,
    find_folder,
    folder_sources,
    preview,
)
from app.channels.settings import (
    DEFAULT_FOLDER,
    ChannelSettings,
    ChannelSettingsSource,
    ProxyKind,
    ProxyPlan,
    api_hash_name,
    proxy_plan,
    session_name,
    system_proxy,
)
from app.storage.channel_store import FIRST_MAGIC, ChannelRepository
from tests.unit.storage_helpers import temporary_store

APP = Path(__file__).resolve().parents[2] / "app"
NOW = 1_791_331_200.0
GOLD = Peer(-1001, "Gold Room", PeerKind.CHANNEL, "goldroom")
FX = Peer(-1002, "FX Group", PeerKind.GROUP)
FRIEND = Peer(55, "A friend", PeerKind.USER)
BOT = Peer(56, "Some bot", PeerKind.BOT)


def test_the_settings_need_an_api_id_and_a_manual_proxy_address(tmp_path: Path) -> None:
    settings = ChannelSettings()
    assert not settings.enabled and settings.folder == DEFAULT_FOLDER
    assert settings.proxy is ProxyKind.SYSTEM
    assert settings.problem().startswith("Enter your api_id")
    manual = ChannelSettings(api_id=123, proxy=ProxyKind.SOCKS5)
    assert manual.problem().startswith("Enter the proxy host")
    assert ChannelSettings(api_id=123, folder="  AI   Lab ").folder == "AI Lab"
    assert ChannelSettings(api_id=123, folder=" ").folder == DEFAULT_FOLDER
    source = ChannelSettingsSource(tmp_path)
    source.save(ChannelSettings(enabled=True, api_id=123))
    again = ChannelSettingsSource(tmp_path).settings
    assert again.enabled and again.api_id == 123 and again.problem() == ""
    text = (tmp_path / "channels.json").read_text(encoding="utf-8")
    assert "hash" not in text and "session" not in text
    assert api_hash_name("p") == "p/telegram-api-hash" and session_name("p") == "p/telegram-session"


def test_the_system_proxy_is_read_like_the_browser_does() -> None:
    socks = system_proxy({"https": "http://127.0.0.1:10809", "socks": "socks://127.0.0.1:10808"})
    assert socks == ProxyPlan(ProxyKind.SOCKS5, "127.0.0.1", 10808)
    https = system_proxy({"https": "http://user:pw@10.0.0.1:8080"})
    assert https is not None and https == ProxyPlan(ProxyKind.HTTP, "10.0.0.1", 8080, "user", "pw")
    assert https.text() == "http 10.0.0.1:8080"
    assert system_proxy({"http": "127.0.0.1:3128"}) == ProxyPlan(ProxyKind.HTTP, "127.0.0.1", 3128)
    assert system_proxy({"http": "no port here"}) is None and system_proxy({}) is None
    manual = ChannelSettings(
        api_id=1,
        proxy=ProxyKind.MTPROTO,
        proxy_host="p.example",
        proxy_port=443,
    )
    assert proxy_plan(manual, "ee00") == ProxyPlan(ProxyKind.MTPROTO, "p.example", 443, "", "ee00")
    assert proxy_plan(ChannelSettings(proxy=ProxyKind.NONE)) is None
    system = ChannelSettings()
    assert proxy_plan(system, proxies={"http": "127.0.0.1:3128"}) is not None


def test_the_folder_holds_the_channels_and_groups_only() -> None:
    folders = [Folder("News", (GOLD.id,)), Folder("ai  lab", (FRIEND.id, GOLD.id, BOT.id, FX.id))]
    folder = find_folder(folders, "AI Lab")
    assert folder is not None and folder.title == "ai  lab"
    assert folder_sources(folder, [FX, FRIEND, BOT, GOLD]) == [GOLD, FX]
    assert find_folder(folders, "Signals") is None
    assert clean_text("a\x00b" + "x" * 5000) == ("ab" + "x" * 5000)[:TEXT_LIMIT]
    assert preview("one\ntwo   three") == "one two three"
    assert len(preview("word " * 100)) == 120 and preview("word " * 100).endswith("\u2026")


def test_every_channel_gets_its_own_magic_once_and_starts_off() -> None:
    with temporary_store() as store:
        channels = ChannelRepository(store)
        found = channels.sync_folder([GOLD, FRIEND, FX], NOW)
        assert [(c.title, c.magic, c.enabled) for c in found] == [
            ("Gold Room", FIRST_MAGIC, False),
            ("FX Group", FIRST_MAGIC + 1, False),
        ]
        assert channels.read_ids() == set()
        assert channels.set_enabled(GOLD.id, True, NOW) and not channels.set_enabled(9, True, NOW)
        assert channels.read_ids() == {GOLD.id}
        later = channels.sync_folder([FX, Peer(-1003, "New", PeerKind.CHANNEL)], NOW + 60)
        state = {c.title: (c.magic, c.in_folder) for c in later}
        assert state == {
            "Gold Room": (FIRST_MAGIC, False),
            "FX Group": (FIRST_MAGIC + 1, True),
            "New": (FIRST_MAGIC + 2, True),
        }
        assert channels.read_ids() == set()  # the on channel left the folder
        back = channels.sync_folder([GOLD], NOW + 120)
        assert [(c.magic, c.in_folder) for c in back][0] == (FIRST_MAGIC, True)
        assert ChannelRepository(store).read_ids() == {GOLD.id}  # the tables already exist


def test_the_first_text_is_kept_and_edits_and_deletions_beside_it() -> None:
    with temporary_store() as store:
        channels = ChannelRepository(store)
        first = ChannelMessage(GOLD.id, 7, NOW, "XAUUSD buy 2345 sl 2335 tp 2360")
        assert channels.save_message(first, NOW)
        assert not channels.save_message(first, NOW)
        edited = ChannelMessage(GOLD.id, 7, NOW, "XAUUSD buy 2345 sl 2335 tp 2360 (TP hit)")
        assert channels.edit_message(edited, NOW + 5)
        assert not channels.edit_message(edited, NOW + 6)  # the same edit again
        reply = ChannelMessage(GOLD.id, 8, NOW + 10, "TP1 hit", reply_to=7)
        assert channels.edit_message(reply, NOW + 10)  # an unknown message is saved
        assert channels.delete_messages(GOLD.id, [7, 99], NOW + 20) == 1
        newest, oldest = channels.messages(GOLD.id)
        assert (newest.message_id, newest.reply_to, newest.deleted) == (8, 7, False)
        assert oldest.text == first.text and oldest.edited_text == edited.text
        assert oldest.deleted and oldest.date == NOW
        assert channels.counts() == {GOLD.id: 2}


def test_the_channel_reader_never_writes_to_telegram_or_trades() -> None:
    """Spec 1.7: the app reads one folder; it never posts, reacts, joins, leaves or marks
    anything as read. And no channel code sends an order."""
    writes = re.compile(
        r"send_message|send_file|send_read_acknowledge|JoinChannel|LeaveChannel|ReadHistory"
        r"|SendReaction|forward_messages|delete_messages\(|order_send|order_check",
    )
    for path in sorted((APP / "channels").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not writes.search(text), path.name
        assert "PySide6" not in text, path.name
