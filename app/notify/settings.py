"""Notification settings (spec C14), saved per profile in `notifications.json`: which events
go to the Windows toast and to Telegram, quiet hours, and the Telegram chat whitelist. The
bot token lives in Windows Credential Manager; the PIN is kept only as a salted hash."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.notify.events import EventKind

NOTIFY_FILE_NAME = "notifications.json"
PIN_LENGTH = (4, 12)


def _all_on() -> dict[str, bool]:
    return {kind.value: True for kind in EventKind}


def _telegram_default() -> dict[str, bool]:
    quiet = {EventKind.TRADE_OPENED, EventKind.APPROVAL_NEEDED}
    return {kind.value: kind not in quiet for kind in EventKind}


def _no_chats() -> list[int]:
    return []


class NotificationSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    toast: dict[str, bool] = Field(default_factory=_all_on)
    telegram: dict[str, bool] = Field(default_factory=_telegram_default)
    quiet_hours: bool = Field(default=False, description="Hold non-urgent notices at night")
    quiet_start: str = Field(default="22:00", description="Quiet hours start (local time)")
    quiet_end: str = Field(default="07:00", description="Quiet hours end (local time)")
    telegram_enabled: bool = Field(default=False)
    chat_ids: list[int] = Field(default_factory=_no_chats)
    pin_salt: str = Field(default="")
    pin_hash: str = Field(default="")
    send_reports: bool = Field(default=True, description="Send daily and weekly reports")

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def _clock(cls, value: str) -> str:
        hours, _, minutes = value.strip().partition(":")
        if not (hours.isdigit() and minutes.isdigit()):
            raise ValueError("use HH:MM")
        if not (0 <= int(hours) < 24 and 0 <= int(minutes) < 60):
            raise ValueError("use HH:MM")
        return f"{int(hours):02d}:{int(minutes):02d}"

    def wants(self, channel: str, kind: EventKind) -> bool:
        table = self.toast if channel == "toast" else self.telegram
        return table.get(kind.value, True)

    def quiet_at(self, moment: datetime) -> bool:
        """True inside the quiet hours (local time); the window may cross midnight."""
        if not self.quiet_hours:
            return False
        now = moment.hour * 60 + moment.minute
        start = _minutes(self.quiet_start)
        end = _minutes(self.quiet_end)
        if start == end:
            return False
        return start <= now < end if start < end else now >= start or now < end

    def with_pin(self, pin: str) -> NotificationSettings:
        low, high = PIN_LENGTH
        if not (pin.isdigit() and low <= len(pin) <= high):
            raise ValueError(f"the PIN is {low} to {high} digits")
        salt = secrets.token_hex(16)
        return self.model_copy(update={"pin_salt": salt, "pin_hash": pin_digest(salt, pin)})

    def pin_matches(self, pin: str) -> bool:
        if not self.pin_hash:
            return False
        return hmac.compare_digest(pin_digest(self.pin_salt, pin.strip()), self.pin_hash)


def _minutes(text: str) -> int:
    hours, _, minutes = text.partition(":")
    return int(hours) * 60 + int(minutes)


def pin_digest(salt: str, pin: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt or "00"), 100_000).hex()


def load_notification_settings(directory: Path) -> tuple[NotificationSettings, str]:
    path = directory / NOTIFY_FILE_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return NotificationSettings(), ""
    except (OSError, ValueError):
        return NotificationSettings(), f"{NOTIFY_FILE_NAME} could not be read: using defaults"
    try:
        return NotificationSettings.model_validate(raw), ""
    except ValidationError as error:
        return NotificationSettings(), f"{NOTIFY_FILE_NAME}: {error.error_count()} invalid value(s)"


def save_notification_settings(directory: Path, settings: NotificationSettings) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / NOTIFY_FILE_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)


class NotificationSettingsSource:
    """The saved settings, shared by the UI, the notification center and the Telegram bot."""

    def __init__(self, directory: Path, note: Callable[[str], None] | None = None) -> None:
        self._directory = directory
        self._note = note
        self._lock = threading.Lock()
        settings, text = load_notification_settings(directory)
        self._settings = settings
        if text and note is not None:
            note(text)

    @property
    def settings(self) -> NotificationSettings:
        with self._lock:
            return self._settings

    def save(self, settings: NotificationSettings) -> None:
        save_notification_settings(self._directory, settings)
        with self._lock:
            self._settings = settings


def token_name(profile: str) -> str:
    """The Credential Manager name of the Telegram bot token."""
    return f"{profile}/telegram-bot-token"


def local_now() -> datetime:
    return datetime.now(UTC).astimezone()
