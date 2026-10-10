"""The Telegram reader, phase 21c2 (docs/SIGNAL_DESK.md 3.1 to 3.3), with a fake Telegram:
the login steps, the folder, the stored messages and the plain error words."""

import asyncio
import time
from collections.abc import Callable, Sequence

import pytest

from app.channels.folder import ChannelMessage, Folder, Peer, PeerKind
from app.channels.reader import ChannelReader, LoginError, ReaderStatus, plain_error
from app.channels.settings import ChannelSettings
from app.channels.telethon_api import peer_kind, proxy_options
from app.storage.channel_store import ChannelRepository
from tests.unit.storage_helpers import temporary_store

GOLD = Peer(-1001, "Gold Room", PeerKind.CHANNEL)
FX = Peer(-1002, "FX Group", PeerKind.GROUP)
FRIEND = Peer(55, "A friend", PeerKind.USER)
ON = ChannelSettings(enabled=True, api_id=123)


class FakeSecrets:
    def __init__(self, saved: str = "") -> None:
        self.saved = saved
        self.forgotten = False

    def session(self) -> str:
        return self.saved

    def save_session(self, value: str) -> None:
        self.saved = value

    def forget_session(self) -> None:
        self.saved, self.forgotten = "", True


class FakeTelegram:
    """Logged out at first; the code asks for the two-step password."""

    def __init__(self, folders: list[Folder], *, logged_in: bool = False) -> None:
        self._folders = folders
        self.logged_in = logged_in
        self.phone = ""
        self.new: Callable[[ChannelMessage], None] | None = None
        self.edited: Callable[[ChannelMessage], None] | None = None
        self.deleted: Callable[[int, Sequence[int]], None] | None = None
        self.stopped: asyncio.Event | None = None
        self.logged_out = False

    async def connect(self) -> None:
        self.stopped = asyncio.Event()

    async def authorized(self) -> bool:
        return self.logged_in

    async def send_code(self, phone: str) -> None:
        self.phone = phone

    async def sign_in(self, code: str) -> bool:
        if code != "12345":
            raise ValueError("wrong code")
        return False  # the two-step password is next

    async def sign_in_password(self, password: str) -> None:
        self.logged_in = password == "secret"
        if not self.logged_in:
            raise ValueError("wrong password")

    def session_string(self) -> str:
        return "session-text"

    async def folders(self) -> list[Folder]:
        return self._folders

    async def peers(self, ids: Sequence[int]) -> list[Peer]:
        known = {peer.id: peer for peer in (GOLD, FX, FRIEND)}
        return [known[peer_id] for peer_id in ids if peer_id in known]

    def listen(
        self,
        new: Callable[[ChannelMessage], None],
        edited: Callable[[ChannelMessage], None],
        deleted: Callable[[int, Sequence[int]], None],
    ) -> None:
        self.new, self.edited, self.deleted = new, edited, deleted

    async def run(self) -> None:
        assert self.stopped is not None
        await self.stopped.wait()

    async def log_out(self) -> None:
        self.logged_out = True

    async def disconnect(self) -> None:
        if self.stopped is not None:
            self.stopped.set()


def wait_for(check: Callable[[], bool], seconds: float = 5.0) -> None:
    ends = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < ends, "timed out"
        time.sleep(0.01)


def test_the_reader_logs_in_reads_the_folder_and_stores_the_on_channels() -> None:
    telegram = FakeTelegram([Folder("AI Lab", (FRIEND.id, GOLD.id, FX.id))])
    secrets = FakeSecrets()
    with temporary_store() as store:
        repository = ChannelRepository(store)
        reader = ChannelReader(lambda: ON, lambda s, saved: telegram, secrets, repository)
        assert reader.start().status is ReaderStatus.CONNECTING
        wait_for(lambda: reader.state.status is ReaderStatus.PHONE)
        with pytest.raises(LoginError):
            reader.send_code("12")
        assert reader.send_code("+98 912 000 0000").status is ReaderStatus.CODE
        assert telegram.phone == "+989120000000"
        with pytest.raises(LoginError, match="Telegram error: ValueError"):
            reader.sign_in("999")
        assert reader.sign_in("12-345").status is ReaderStatus.PASSWORD
        reader.sign_in_password("secret")
        wait_for(lambda: reader.state.status is ReaderStatus.RUNNING)
        state = reader.state
        assert state.folder_found and state.channels == 2 and state.reading == 0
        assert secrets.saved == "session-text"
        assert [channel.title for channel in repository.channels()] == ["Gold Room", "FX Group"]
        assert telegram.new is not None and telegram.deleted is not None
        telegram.new(ChannelMessage(GOLD.id, 1, 100.0, "off: not stored"))
        repository.set_enabled(GOLD.id, True, 100.0)
        assert reader.refresh().reading == 1
        telegram.new(ChannelMessage(GOLD.id, 2, 101.0, "XAUUSD buy 2345 sl 2335"))
        telegram.new(ChannelMessage(FRIEND.id, 3, 102.0, "a private chat: never stored"))
        telegram.deleted(GOLD.id, [2])
        stored = repository.messages(GOLD.id)
        assert [(m.message_id, m.deleted) for m in stored] == [(2, True)]
        assert repository.counts() == {GOLD.id: 1} and reader.state.stored == 1
        assert reader.log_out().status is ReaderStatus.OFF
        assert telegram.logged_out and secrets.forgotten and secrets.saved == ""


def test_a_saved_session_skips_the_login_and_a_missing_folder_says_so() -> None:
    telegram = FakeTelegram([Folder("News", (GOLD.id,))], logged_in=True)
    with temporary_store() as store:
        reader = ChannelReader(
            lambda: ON,
            lambda s, saved: telegram,
            FakeSecrets("old"),
            ChannelRepository(store),
        )
        reader.start()
        wait_for(lambda: reader.state.status is ReaderStatus.RUNNING)
        assert not reader.state.folder_found
        assert reader.state.message.startswith('No Telegram folder named "AI Lab"')
        reader.stop()
        wait_for(lambda: reader.state.status is ReaderStatus.OFF)


def test_the_reader_says_why_it_does_not_start() -> None:
    with temporary_store() as store:
        repository = ChannelRepository(store)
        secrets = FakeSecrets()
        off = ChannelReader(ChannelSettings, None, secrets, repository)
        assert off.start().status is ReaderStatus.OFF
        missing = ChannelReader(lambda: ON, None, secrets, repository)
        assert missing.start().status is ReaderStatus.MISSING
        unset = ChannelSettings(enabled=True)
        fake = FakeTelegram([])
        broken = ChannelReader(lambda: unset, lambda s, saved: fake, secrets, repository)
        assert broken.start().message.startswith("Enter your api_id")
        with pytest.raises(LoginError, match="not running"):
            broken.send_code("+989120000000")


class FloodWaitError(Exception):
    seconds = 300


class PhoneCodeInvalidError(Exception):
    pass


def test_errors_are_plain_words() -> None:
    assert plain_error(FloodWaitError()) == "Telegram asks to wait 5 min before trying again."
    assert plain_error(PhoneCodeInvalidError()).startswith("Wrong code")
    assert plain_error(ConnectionResetError()).startswith("No connection to Telegram")
    assert plain_error(KeyError("x")) == "Telegram error: KeyError"


class Entity:
    def __init__(self, **values: object) -> None:
        self.__dict__.update(values)


def test_the_telethon_parts_without_telethon() -> None:
    assert peer_kind(Entity(broadcast=True)) is PeerKind.CHANNEL
    assert peer_kind(Entity(broadcast=False, megagroup=True)) is PeerKind.GROUP
    assert peer_kind(Entity(participants_count=3)) is PeerKind.GROUP
    assert peer_kind(Entity(bot=True)) is PeerKind.BOT
    assert peer_kind(Entity(first_name="Ali")) is PeerKind.USER
    assert proxy_options(None) == {}
