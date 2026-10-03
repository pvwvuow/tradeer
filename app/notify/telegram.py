"""The optional Telegram bot (spec C14): notices out, a few commands in.

Only whitelisted chat ids are answered; everything else is ignored and logged. A chat must
unlock with `/pin <PIN>` (valid 15 minutes); five wrong PINs lock it for 15 minutes. Commands:
`/status`, `/positions`, `/pnl`, `/pause`, `/resume`, `/approve <id>`, `/killswitch` (asks
for `/killswitch CONFIRM` within 60 seconds). Every command is audit-logged with source
`telegram`. The bot talks to the Bot API over HTTPS with long polling (no webhook, nothing
listens on this PC); the transport is injectable, so the tests never touch the network.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.notify.events import Notice
from app.notify.settings import NotificationSettings

API = "https://api.telegram.org"
UNLOCK_SECONDS = 15 * 60
CONFIRM_SECONDS = 60.0
MAX_PIN_TRIES = 5
LOCK_SECONDS = 15 * 60
POLL_SECONDS = 25
MAX_TEXT = 3900
HELP = (
    "Commands (unlock first with /pin <PIN>):\n"
    "/status - mode, connection, bot state\n"
    "/positions - open positions\n"
    "/pnl - today's result\n"
    "/pause - stop new entries\n"
    "/resume - allow entries again after /pause\n"
    "/approve <id> - approve a waiting signal\n"
    "/killswitch - close the bot's positions and stop (asks to confirm)"
)

Transport = Callable[[str, Mapping[str, Any], float], Mapping[str, Any]]
Log = Callable[[str, str], None]
Audit = Callable[[str, str, str], None]


class TelegramError(RuntimeError):
    pass


def http_transport(url: str, payload: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
    data = json.dumps(dict(payload)).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            found: Mapping[str, Any] = json.loads(response.read().decode())
            return found
    except urllib.error.HTTPError as error:
        try:
            body: Mapping[str, Any] = json.loads(error.read().decode())
        except ValueError:
            body = {"ok": False, "description": f"HTTP {error.code}"}
        return body
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TelegramError(f"Telegram cannot be reached: {error}") from error


class TelegramApi:
    def __init__(self, token: str, transport: Transport = http_transport) -> None:
        self._token = token
        self._transport = transport

    def call(self, method: str, payload: Mapping[str, Any], timeout: float = 15.0) -> Any:
        url = f"{API}/bot{self._token}/{method}"
        answer = self._transport(url, payload, timeout)
        if not answer.get("ok"):
            raise TelegramError(str(answer.get("description") or "Telegram refused the request"))
        return answer.get("result")

    def send(self, chat_id: int, text: str) -> None:
        self.call("sendMessage", {"chat_id": chat_id, "text": text[:MAX_TEXT]})

    def updates(self, offset: int, timeout: int = POLL_SECONDS) -> list[Mapping[str, Any]]:
        payload = {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]}
        result = self.call("getUpdates", payload, timeout + 10.0)
        return list(result) if isinstance(result, list) else []

    def me(self) -> str:
        result = self.call("getMe", {})
        return str(result.get("username", "")) if isinstance(result, Mapping) else ""


class RemoteControl(Protocol):
    def status(self) -> str: ...

    def positions(self) -> str: ...

    def pnl(self) -> str: ...

    def pause(self) -> str: ...

    def resume(self) -> str: ...

    def approve(self, signal_id: str) -> str: ...

    def kill(self) -> str: ...


@dataclass
class _Chat:
    unlocked_until: float = 0.0
    wrong: int = 0
    locked_until: float = 0.0
    kill_asked: float = field(default=-1e18)


def _quiet(level: str, message: str) -> None:
    return None


def _no_audit(action: str, before: str, after: str) -> None:
    return None


class TelegramBot:
    def __init__(
        self,
        api: TelegramApi,
        settings: Callable[[], NotificationSettings],
        control: RemoteControl,
        *,
        log: Log = _quiet,
        audit: Audit = _no_audit,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api = api
        self._settings = settings
        self._control = control
        self._log = log
        self._audit = audit
        self._clock = clock
        self._chats: dict[int, _Chat] = {}
        self._offset = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # Out ------------------------------------------------------------------------------
    def notify(self, notice: Notice) -> None:
        self.broadcast(f"{notice.title}\n{notice.text}")

    def broadcast(self, text: str) -> int:
        sent = 0
        for chat in self._settings().chat_ids:
            try:
                self._api.send(chat, text)
                sent += 1
            except TelegramError as error:
                self._log("WARNING", f"Telegram message failed: {error}")
        return sent

    # In -------------------------------------------------------------------------------
    def poll_once(self, timeout: int = POLL_SECONDS) -> int:
        updates = self._api.updates(self._offset, timeout)
        for update in updates:
            self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
            message = update.get("message")
            if isinstance(message, Mapping):
                self.handle(message)
        return len(updates)

    def handle(self, message: Mapping[str, Any]) -> str:
        chat = message.get("chat") or {}
        chat_id = int(chat.get("id", 0)) if isinstance(chat, Mapping) else 0
        text = str(message.get("text") or "").strip()
        settings = self._settings()
        if chat_id not in settings.chat_ids:
            self._log("WARNING", f"Telegram message from a chat that is not allowed ({chat_id})")
            return ""
        reply = self._answer(chat_id, text, settings)
        if reply:
            try:
                self._api.send(chat_id, reply)
            except TelegramError as error:
                self._log("WARNING", f"Telegram reply failed: {error}")
        return reply

    def _answer(self, chat_id: int, text: str, settings: NotificationSettings) -> str:
        now = self._clock()
        state = self._chats.setdefault(chat_id, _Chat())
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        argument = argument.strip()
        if command in ("/start", "/help"):
            return HELP
        if command == "/pin":
            if now < state.locked_until:
                return "Too many wrong PINs. Try again later."
            if settings.pin_matches(argument):
                state.unlocked_until = now + UNLOCK_SECONDS
                state.wrong = 0
                self._audit("telegram unlocked", "", f"chat {chat_id}")
                return "Unlocked for 15 minutes."
            state.wrong += 1
            self._log("WARNING", f"Wrong Telegram PIN from chat {chat_id} ({state.wrong})")
            if state.wrong >= MAX_PIN_TRIES:
                state.locked_until = now + LOCK_SECONDS
                state.wrong = 0
                return "Wrong PIN. Locked for 15 minutes."
            return "Wrong PIN."
        if not settings.pin_hash:
            return "Set a PIN in the app first (Settings, Notifications)."
        if now >= state.unlocked_until:
            return "Locked. Send /pin <PIN> first."
        return self._command(chat_id, state, command, argument, now)

    def _command(self, chat_id: int, state: _Chat, command: str, argument: str, now: float) -> str:
        actions: dict[str, Callable[[], str]] = {
            "/status": self._control.status,
            "/positions": self._control.positions,
            "/pnl": self._control.pnl,
            "/pause": self._control.pause,
            "/resume": self._control.resume,
        }
        if command in actions:
            reply = actions[command]()
        elif command == "/approve":
            if len(argument) < 6:
                return "Send /approve <id> with at least the first 6 characters of the id."
            reply = self._control.approve(argument)
        elif command == "/killswitch":
            if argument.upper() != "CONFIRM":
                state.kill_asked = now
                return (
                    "This closes every bot position, cancels its orders and stops new entries. "
                    "Send /killswitch CONFIRM within 60 seconds."
                )
            if now - state.kill_asked > CONFIRM_SECONDS:
                return "Send /killswitch first, then /killswitch CONFIRM within 60 seconds."
            state.kill_asked = -1e18
            reply = self._control.kill()
        else:
            return "Unknown command. Send /help."
        self._audit(f"telegram {command}", argument, f"chat {chat_id}: {reply[:200]}")
        return reply

    # Thread ---------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="telegram", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def _run(self) -> None:
        delay = 2.0
        while not self._stop.is_set():
            try:
                self.poll_once()
                delay = 2.0
            except TelegramError as error:
                self._log("WARNING", f"Telegram: {error}; trying again in {delay:.0f} s")
                self._stop.wait(delay)
                delay = min(delay * 2, 300.0)
            except Exception as error:
                self._log("ERROR", f"Telegram bot failed: {type(error).__name__}: {error}")
                self._stop.wait(60.0)
