"""Telethon behind `TelegramApi` (docs/SIGNAL_DESK.md 3.1, phase 21c2).

Telethon is an optional dependency: without it `api_factory` gives None and the page says
how to install it. It is loaded with `import_module`, so the rest of the app never needs it.
Read only: no method here posts, joins, leaves, reacts or marks anything as read.
"""

from __future__ import annotations

import urllib.request
from collections.abc import Callable, Mapping, Sequence
from importlib import import_module
from importlib.util import find_spec
from typing import Any

from app.channels.folder import ChannelMessage, Folder, Peer, PeerKind
from app.channels.reader import ApiFactory
from app.channels.settings import ChannelSettings, ProxyKind, ProxyPlan, proxy_plan

TELETHON = "telethon"


def telethon_installed() -> bool:
    return find_spec(TELETHON) is not None


def _title(value: object) -> str:
    """A folder title: a plain string, or TextWithEntities in newer Telegram layers."""
    text = getattr(value, "text", value)
    return str(text or "")


def proxy_options(proxy: ProxyPlan | None) -> dict[str, Any]:
    """The TelegramClient keyword arguments of a proxy."""
    if proxy is None:
        return {}
    if proxy.kind is ProxyKind.MTPROTO:
        network = import_module("telethon.network")
        return {
            "connection": network.ConnectionTcpMTProxyRandomizedIntermediate,
            "proxy": (proxy.host, proxy.port, proxy.password),
        }
    return {
        "proxy": {
            "proxy_type": "socks5" if proxy.kind is ProxyKind.SOCKS5 else "http",
            "addr": proxy.host,
            "port": proxy.port,
            "username": proxy.username or None,
            "password": proxy.password or None,
            "rdns": True,
        },
    }


def peer_kind(entity: object) -> PeerKind:
    if getattr(entity, "broadcast", False):
        return PeerKind.CHANNEL
    if getattr(entity, "megagroup", False) or hasattr(entity, "participants_count"):
        return PeerKind.GROUP
    if getattr(entity, "bot", False):
        return PeerKind.BOT
    return PeerKind.USER


class TelethonApi:
    def __init__(
        self,
        settings: ChannelSettings,
        session: str,
        api_hash: str,
        proxy: ProxyPlan | None,
    ) -> None:
        telethon = import_module(TELETHON)
        sessions = import_module("telethon.sessions")
        self._client: Any = telethon.TelegramClient(
            sessions.StringSession(session or None),
            settings.api_id,
            api_hash,
            **proxy_options(proxy),
        )
        self._events: Any = import_module("telethon.events")
        self._errors: Any = import_module("telethon.errors")
        self._requests: Any = import_module("telethon.tl.functions.messages")
        self._utils: Any = import_module("telethon.utils")
        self._inputs: dict[int, Any] = {}
        self._phone = ""
        self._code_hash = ""

    async def connect(self) -> None:
        await self._client.connect()

    async def authorized(self) -> bool:
        return bool(await self._client.is_user_authorized())

    async def send_code(self, phone: str) -> None:
        sent = await self._client.send_code_request(phone)
        self._phone, self._code_hash = phone, str(sent.phone_code_hash)

    async def sign_in(self, code: str) -> bool:
        try:
            hashed = self._code_hash
            await self._client.sign_in(phone=self._phone, code=code, phone_code_hash=hashed)
        except self._errors.SessionPasswordNeededError:
            return False
        return True

    async def sign_in_password(self, password: str) -> None:
        await self._client.sign_in(password=password)

    def session_string(self) -> str:
        return str(self._client.session.save())

    async def folders(self) -> list[Folder]:
        result = await self._client(self._requests.GetDialogFiltersRequest())
        found: list[Folder] = []
        for item in getattr(result, "filters", result) or []:
            if not hasattr(item, "include_peers"):
                continue  # the built-in "All chats"
            peers = [*(getattr(item, "pinned_peers", None) or []), *(item.include_peers or [])]
            ids: list[int] = []
            for peer in peers:
                peer_id = int(self._utils.get_peer_id(peer))
                self._inputs[peer_id] = peer
                ids.append(peer_id)
            found.append(Folder(_title(getattr(item, "title", "")), tuple(ids)))
        return found

    async def peers(self, ids: Sequence[int]) -> list[Peer]:
        found: list[Peer] = []
        for peer_id in ids:
            entity = await self._client.get_entity(self._inputs.get(peer_id, peer_id))
            title = getattr(entity, "title", None) or getattr(entity, "first_name", None) or ""
            username = getattr(entity, "username", None) or ""
            found.append(Peer(peer_id, str(title), peer_kind(entity), str(username)))
        return found

    def _message(self, event: Any) -> ChannelMessage:
        message = event.message
        return ChannelMessage(
            channel_id=int(event.chat_id),
            message_id=int(message.id),
            date=float(message.date.timestamp()),
            text=str(message.message or ""),
            reply_to=getattr(message, "reply_to_msg_id", None),
        )

    def listen(
        self,
        new: Callable[[ChannelMessage], None],
        edited: Callable[[ChannelMessage], None],
        deleted: Callable[[int, Sequence[int]], None],
    ) -> None:
        async def on_new(event: Any) -> None:
            if event.chat_id is not None:
                new(self._message(event))

        async def on_edit(event: Any) -> None:
            if event.chat_id is not None:
                edited(self._message(event))

        async def on_delete(event: Any) -> None:
            if event.chat_id is not None:
                deleted(int(event.chat_id), [int(item) for item in event.deleted_ids])

        self._client.add_event_handler(on_new, self._events.NewMessage())
        self._client.add_event_handler(on_edit, self._events.MessageEdited())
        self._client.add_event_handler(on_delete, self._events.MessageDeleted())

    async def run(self) -> None:
        await self._client.run_until_disconnected()

    async def log_out(self) -> None:
        await self._client.log_out()

    async def disconnect(self) -> None:
        await self._client.disconnect()


def api_factory(
    api_hash: Callable[[], str],
    proxy_secret: Callable[[], str],
    proxies: Callable[[], Mapping[str, str]] = urllib.request.getproxies,
) -> ApiFactory | None:
    """The reader's way to make a Telethon client, or None without Telethon."""
    if not telethon_installed():
        return None

    def make(settings: ChannelSettings, session: str) -> TelethonApi:
        plan = proxy_plan(settings, proxy_secret(), proxies())
        return TelethonApi(settings, session, api_hash(), plan)

    return make
