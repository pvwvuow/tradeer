"""Telegram channel settings (docs/SIGNAL_DESK.md 3.1, phase 21c), saved per profile in
`channels.json`: on or off, the api_id, the folder name and the proxy.

The api_hash, the Telethon session and a proxy password live in Windows Credential Manager,
never in this file or a log. The system proxy is the default (needed in Iran): it is read
from the Windows proxy settings the same way the browser does.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CHANNELS_FILE_NAME = "channels.json"
DEFAULT_FOLDER = "AI Lab"
SOCKS_SCHEMES = ("socks5", "socks5h", "socks", "socks4")


class ProxyKind(StrEnum):
    SYSTEM = "system"  # the Windows proxy settings (the default)
    NONE = "none"
    SOCKS5 = "socks5"
    HTTP = "http"
    MTPROTO = "mtproto"


class ChannelSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(default=False, description="Read the Telegram folder")
    api_id: int = Field(default=0, ge=0, description="From my.telegram.org")
    folder: str = Field(default=DEFAULT_FOLDER, description="The Telegram folder to read")
    proxy: ProxyKind = Field(default=ProxyKind.SYSTEM)
    proxy_host: str = Field(default="")
    proxy_port: int = Field(default=0, ge=0, le=65_535)
    proxy_user: str = Field(default="")

    @field_validator("folder")
    @classmethod
    def _folder(cls, value: str) -> str:
        return " ".join(value.split()) or DEFAULT_FOLDER

    @field_validator("proxy_host", "proxy_user")
    @classmethod
    def _plain(cls, value: str) -> str:
        return value.strip()

    def problem(self) -> str:
        """Why the reader cannot start with these settings, or ""."""
        if self.api_id <= 0:
            return "Enter your api_id from my.telegram.org."
        manual = self.proxy in (ProxyKind.SOCKS5, ProxyKind.HTTP, ProxyKind.MTPROTO)
        if manual and not (self.proxy_host and self.proxy_port):
            return "Enter the proxy host and port, or use the system proxy."
        return ""


@dataclass(frozen=True)
class ProxyPlan:
    """The proxy one Telethon client uses (for MTProto `password` is the proxy secret)."""

    kind: ProxyKind
    host: str
    port: int
    username: str = ""
    password: str = ""

    def text(self) -> str:
        """For the page and the log: never the password or the secret."""
        return f"{self.kind.value} {self.host}:{self.port}"


def system_proxy(proxies: Mapping[str, str]) -> ProxyPlan | None:
    """The proxy from `urllib.request.getproxies()` (on Windows the Internet settings):
    SOCKS first, then HTTPS, then HTTP; None without one."""
    for key in ("socks", "https", "http"):
        address = proxies.get(key, "").strip()
        if not address:
            continue
        if "://" not in address:
            address = f"{'socks5' if key == 'socks' else 'http'}://{address}"
        try:
            parts = urlsplit(address)
            port = parts.port
        except ValueError:
            continue
        if not parts.hostname or not port:
            continue
        socks = parts.scheme.lower() in SOCKS_SCHEMES
        kind = ProxyKind.SOCKS5 if socks else ProxyKind.HTTP
        return ProxyPlan(kind, parts.hostname, port, parts.username or "", parts.password or "")
    return None


def proxy_plan(
    settings: ChannelSettings,
    password: str = "",
    proxies: Mapping[str, str] | None = None,
) -> ProxyPlan | None:
    """The proxy to use: the system one, none, or the one entered on the page (for MTProto
    the password field holds the secret)."""
    kind = settings.proxy
    if kind is ProxyKind.NONE:
        return None
    if kind is ProxyKind.SYSTEM:
        return system_proxy(proxies or {})
    if not (settings.proxy_host and settings.proxy_port):
        return None
    return ProxyPlan(kind, settings.proxy_host, settings.proxy_port, settings.proxy_user, password)


def load_channel_settings(directory: Path) -> tuple[ChannelSettings, str]:
    path = directory / CHANNELS_FILE_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ChannelSettings(), ""
    except (OSError, ValueError):
        return ChannelSettings(), f"{CHANNELS_FILE_NAME} could not be read: using defaults"
    try:
        return ChannelSettings.model_validate(raw), ""
    except ValidationError as error:
        return ChannelSettings(), f"{CHANNELS_FILE_NAME}: {error.error_count()} invalid value(s)"


def save_channel_settings(directory: Path, settings: ChannelSettings) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / CHANNELS_FILE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)


class ChannelSettingsSource:
    """The saved settings, shared by the page and the reader."""

    def __init__(self, directory: Path, note: Callable[[str], None] | None = None) -> None:
        self._directory = directory
        self._lock = threading.Lock()
        settings, text = load_channel_settings(directory)
        self._settings = settings
        if text and note is not None:
            note(text)

    @property
    def settings(self) -> ChannelSettings:
        with self._lock:
            return self._settings

    def save(self, settings: ChannelSettings) -> None:
        save_channel_settings(self._directory, settings)
        with self._lock:
            self._settings = settings


def api_hash_name(profile: str) -> str:
    """The Credential Manager name of the api_hash."""
    return f"{profile}/telegram-api-hash"


def session_name(profile: str) -> str:
    """The Credential Manager name of the Telethon session (a StringSession)."""
    return f"{profile}/telegram-session"


def proxy_secret_name(profile: str) -> str:
    """The Credential Manager name of the proxy password or MTProto secret."""
    return f"{profile}/telegram-proxy-secret"
