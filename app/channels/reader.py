"""The Telegram reader (docs/SIGNAL_DESK.md 3.1 to 3.3, phase 21c2): the user's own account,
read only, in its own thread with its own asyncio loop.

`ChannelReader` logs in once (phone, the code Telegram sends, the two-step password if set),
keeps the session in Credential Manager, finds the folder, gives every channel in it its
magic (`ChannelRepository.sync_folder`) and stores every new message, edit and deletion of
the channels that are on. The folder is read again every 10 minutes. It never posts, joins,
leaves, reacts or marks anything as read, and nothing here can send an order.

Telegram itself is behind `TelegramApi` (`app.channels.telethon_api` with Telethon, an
optional dependency); the tests use a fake one.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol, TypeVar

from app.channels.folder import ChannelMessage, Folder, Peer, find_folder, folder_sources
from app.channels.settings import ChannelSettings
from app.storage.channel_store import ChannelRepository

FOLDER_SECONDS = 600.0
THREAD_NAME = "telegram-channels"
WAIT_SECONDS = 60.0
Result = TypeVar("Result")
Log = Callable[[str, str], None]
Listener = Callable[["ReaderState"], None]


class ReaderStatus(StrEnum):
    OFF = "off"
    MISSING = "missing"  # Telethon is not installed
    CONNECTING = "connecting"
    PHONE = "phone"  # waiting for the phone number
    CODE = "code"  # waiting for the code Telegram sent
    PASSWORD = "password"  # waiting for the two-step password
    RUNNING = "running"
    ERROR = "error"


@dataclass(frozen=True)
class ReaderState:
    status: ReaderStatus
    message: str = ""
    folder_found: bool = False
    channels: int = 0  # channels and groups in the folder
    reading: int = 0  # of them on
    stored: int = 0  # messages stored since the start
    updated_at: float = 0.0


class LoginError(Exception):
    """A login step failed; the text is for the page (plain words, no secrets)."""


class TelegramApi(Protocol):
    """What the reader needs from Telegram (read only)."""

    async def connect(self) -> None: ...

    async def authorized(self) -> bool: ...

    async def send_code(self, phone: str) -> None: ...

    async def sign_in(self, code: str) -> bool: ...  # False: the two-step password is next

    async def sign_in_password(self, password: str) -> None: ...

    def session_string(self) -> str: ...

    async def folders(self) -> list[Folder]: ...

    async def peers(self, ids: Sequence[int]) -> list[Peer]: ...

    def listen(
        self,
        new: Callable[[ChannelMessage], None],
        edited: Callable[[ChannelMessage], None],
        deleted: Callable[[int, Sequence[int]], None],
    ) -> None: ...

    async def run(self) -> None: ...  # until disconnected

    async def log_out(self) -> None: ...

    async def disconnect(self) -> None: ...


class Secrets(Protocol):
    def session(self) -> str: ...

    def save_session(self, value: str) -> None: ...

    def forget_session(self) -> None: ...


ApiFactory = Callable[[ChannelSettings, str], TelegramApi]  # settings, saved session


def _quiet(level: str, message: str) -> None:
    return None


class ChannelReader:
    def __init__(
        self,
        settings: Callable[[], ChannelSettings],
        make_api: ApiFactory | None,
        secrets: Secrets,
        repository: ChannelRepository,
        *,
        log: Log = _quiet,
        utc_now: Callable[[], float] = time.time,
        folder_seconds: float = FOLDER_SECONDS,
    ) -> None:
        self._settings = settings
        self._make_api = make_api
        self._secrets = secrets
        self._repository = repository
        self._log = log
        self._now = utc_now
        self._folder_seconds = folder_seconds
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._state = ReaderState(ReaderStatus.OFF)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._api: TelegramApi | None = None
        self._authorized: asyncio.Event | None = None  # made inside the loop
        self._task: asyncio.Task[Any] | None = None
        self._stored = 0

    # State --------------------------------------------------------------------------------
    @property
    def state(self) -> ReaderState:
        with self._lock:
            return self._state

    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def _set(self, status: ReaderStatus, message: str = "", **changes: Any) -> None:
        with self._lock:
            self._state = replace(
                self._state,
                status=status,
                message=message,
                updated_at=self._now(),
                **changes,
            )
            state = self._state
            listeners = list(self._listeners)
        for listener in listeners:
            with contextlib.suppress(Exception):
                listener(state)

    # Start and stop -----------------------------------------------------------------------
    def start(self) -> ReaderState:
        """Start the reader thread when the settings say so; the state says why not."""
        if self._thread is not None and self._thread.is_alive():
            return self.state
        settings = self._settings()
        if not settings.enabled:
            self._set(ReaderStatus.OFF, "Telegram channels are off.")
            return self.state
        if self._make_api is None:
            self._set(ReaderStatus.MISSING, "Telethon is not installed: see the page for how.")
            return self.state
        problem = settings.problem()
        if problem:
            self._set(ReaderStatus.ERROR, problem)
            return self.state
        self._set(ReaderStatus.CONNECTING, "Connecting to Telegram...")
        self._thread = threading.Thread(target=self._run, name=THREAD_NAME, daemon=True)
        self._thread.start()
        return self.state

    def stop(self, timeout: float = 10.0) -> None:
        """Disconnect and end the reader thread, also while it waits for a login step."""
        loop, api, task = self._loop, self._api, self._task
        if loop is not None and loop.is_running():
            if api is not None:
                with contextlib.suppress(Exception):
                    asyncio.run_coroutine_threadsafe(api.disconnect(), loop).result(timeout)
            if task is not None:
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(task.cancel)
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except asyncio.CancelledError:
            self._set(ReaderStatus.OFF, "Telegram channels stopped.")
        except Exception as error:
            self._set(ReaderStatus.ERROR, plain_error(error))
            self._log("WARNING", f"Telegram channels stopped: {plain_error(error)}")
        finally:
            self._loop = None
            self._task = None
            with contextlib.suppress(Exception):
                self._repository.store.db.release()  # this thread's database connection

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        self._authorized = asyncio.Event()
        settings = self._settings()
        if self._make_api is None:
            return
        api = self._make_api(settings, self._secrets.session())
        self._api = api
        await api.connect()
        if await api.authorized():
            self._authorized.set()
        else:
            self._set(ReaderStatus.PHONE, "Log in: enter your phone number.")
            await self._authorized.wait()
        self._secrets.save_session(api.session_string())
        api.listen(self._on_new, self._on_edit, self._on_delete)
        await self.read_folder()
        refresher = asyncio.create_task(self._refresh_folder())
        try:
            await api.run()
        finally:
            refresher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresher
        self._set(ReaderStatus.OFF, "Disconnected from Telegram.")

    async def _refresh_folder(self) -> None:
        while True:
            await asyncio.sleep(self._folder_seconds)
            try:
                await self.read_folder()
            except Exception as error:
                self._log("WARNING", f"Telegram folder not read: {plain_error(error)}")

    async def read_folder(self) -> ReaderState:
        """Read the folder again: its channels get their magic, the counts are updated."""
        api = self._api
        if api is None:
            return self.state
        name = self._settings().folder
        folder = find_folder(await api.folders(), name)
        if folder is None:
            self._set(
                ReaderStatus.RUNNING,
                f'No Telegram folder named "{name}": make one and add the channels to it.',
                folder_found=False,
                channels=0,
                reading=0,
            )
            return self.state
        sources = folder_sources(folder, await api.peers(folder.peers))
        channels = self._repository.sync_folder(sources, self._now())
        reading = len(self._repository.read_ids())
        in_folder = sum(1 for channel in channels if channel.in_folder)
        self._set(
            ReaderStatus.RUNNING,
            f'Reading {reading} of {in_folder} channels in "{name}".',
            folder_found=True,
            channels=in_folder,
            reading=reading,
        )
        return self.state

    # Login (called from the UI thread) ----------------------------------------------------
    def _call(self, job: Coroutine[Any, Any, Result]) -> Result:
        loop = self._loop
        if loop is None or not loop.is_running():
            job.close()
            raise LoginError("The reader is not running: turn it on and Save first.")
        future = asyncio.run_coroutine_threadsafe(job, loop)
        try:
            return future.result(WAIT_SECONDS)
        except LoginError:
            raise
        except Exception as error:
            raise LoginError(plain_error(error)) from None

    def send_code(self, phone: str) -> ReaderState:
        number = "".join(char for char in phone if char.isdigit() or char == "+")
        if len(number.lstrip("+")) < 6:
            raise LoginError("Enter the phone number with the country code, like +98...")
        self._call(self._send_code(number))
        self._set(ReaderStatus.CODE, "Enter the code Telegram sent you.")
        return self.state

    async def _send_code(self, phone: str) -> None:
        if self._api is not None:
            await self._api.send_code(phone)

    def sign_in(self, code: str) -> ReaderState:
        clean = "".join(char for char in code if char.isdigit())
        if not clean:
            raise LoginError("Enter the code Telegram sent you.")
        if self._call(self._sign_in(clean)):
            self._done()
        else:
            self._set(ReaderStatus.PASSWORD, "Two-step verification: enter your password.")
        return self.state

    async def _sign_in(self, code: str) -> bool:
        return await self._api.sign_in(code) if self._api is not None else False

    def sign_in_password(self, password: str) -> ReaderState:
        if not password:
            raise LoginError("Enter your two-step verification password.")
        self._call(self._password(password))
        self._done()
        return self.state

    async def _password(self, password: str) -> None:
        if self._api is not None:
            await self._api.sign_in_password(password)

    def _done(self) -> None:
        self._set(ReaderStatus.CONNECTING, "Logged in: reading the folder...")
        loop, event = self._loop, self._authorized
        if loop is not None and event is not None:
            loop.call_soon_threadsafe(event.set)

    def log_out(self) -> ReaderState:
        """End the session on Telegram's side and forget it here."""
        with contextlib.suppress(LoginError):
            self._call(self._log_out())
        self._secrets.forget_session()
        self.stop()
        self._set(ReaderStatus.OFF, "Logged out: the session was removed.")
        return self.state

    async def _log_out(self) -> None:
        if self._api is not None:
            await self._api.log_out()

    def refresh(self) -> ReaderState:
        """Read the folder again now (after a channel was turned on or off)."""
        return self._call(self.read_folder())

    # Messages (in the reader thread) ------------------------------------------------------
    def _wanted(self, channel_id: int) -> bool:
        return channel_id in self._repository.read_ids()

    def _on_new(self, message: ChannelMessage) -> None:
        if self._wanted(message.channel_id) and self._repository.save_message(message, self._now()):
            self._count()

    def _on_edit(self, message: ChannelMessage) -> None:
        if self._wanted(message.channel_id):
            self._repository.edit_message(message, self._now())

    def _on_delete(self, channel_id: int, message_ids: Sequence[int]) -> None:
        if self._wanted(channel_id):
            self._repository.delete_messages(channel_id, message_ids, self._now())

    def _count(self) -> None:
        self._stored += 1
        state = self.state
        self._set(state.status, state.message, stored=self._stored)


def plain_error(error: BaseException) -> str:
    """A Telegram or network error in plain words (never a number, a code or a hash)."""
    name = type(error).__name__
    seconds = getattr(error, "seconds", None)
    if "FloodWait" in name and isinstance(seconds, int):
        return f"Telegram asks to wait {max(1, round(seconds / 60))} min before trying again."
    words = {
        "PhoneNumberInvalid": "This phone number is not valid: use the country code (+98...).",
        "PhoneCodeInvalid": "Wrong code: check the code Telegram sent and try again.",
        "PhoneCodeExpired": "The code expired: ask for a new one.",
        "PasswordHashInvalid": "Wrong two-step verification password.",
        "ApiIdInvalid": "The api_id or api_hash is wrong: check them on my.telegram.org.",
        "AuthKeyUnregistered": "The session ended: log in again.",
        "SessionRevoked": "The session was ended from another device: log in again.",
    }
    for key, text in words.items():
        if key in name:
            return text
    if isinstance(error, OSError):
        return "No connection to Telegram: check the internet, the VPN or the proxy."
    return f"Telegram error: {name}"
