"""The notification center (spec C14): every notice goes through here, from any thread.

It checks the settings (which channels want the event), holds non-urgent notices during
quiet hours (urgent ones always go out), drops a repeat of the same notice within ten
minutes, and hands the rest to the channels: the Windows toast (tray) and Telegram. Every
notice is logged and kept in a short history for the UI.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.notify.events import Notice
from app.notify.settings import NotificationSettings, local_now

REPEAT_SECONDS = 600.0
HISTORY = 200

Sink = Callable[[Notice], None]
Log = Callable[[str, str], None]


def _quiet(level: str, message: str) -> None:
    return None


@dataclass(frozen=True)
class Delivered:
    notice: Notice
    channels: tuple[str, ...]  # "toast", "telegram"; empty when held or off
    held: bool = False  # quiet hours


class NotificationCenter:
    def __init__(
        self,
        settings: Callable[[], NotificationSettings],
        *,
        toast: Sink | None = None,
        telegram: Sink | None = None,
        log: Log = _quiet,
        clock: Callable[[], float] = time.monotonic,
        local: Callable[[], datetime] = local_now,
    ) -> None:
        self._settings = settings
        self._toast = toast
        self._telegram = telegram
        self._log = log
        self._clock = clock
        self._local = local
        self._lock = threading.Lock()
        self._sent: dict[str, float] = {}
        self._history: deque[Delivered] = deque(maxlen=HISTORY)

    def set_toast(self, sink: Sink | None) -> None:
        self._toast = sink

    def set_telegram(self, sink: Sink | None) -> None:
        self._telegram = sink

    @property
    def history(self) -> list[Delivered]:
        with self._lock:
            return list(self._history)

    def publish_all(self, notices: Sequence[Notice]) -> list[Delivered]:
        return [self.publish(notice) for notice in notices]

    def publish(self, notice: Notice) -> Delivered:
        settings = self._settings()
        now = self._clock()
        with self._lock:
            key = notice.key or f"{notice.kind.value}:{notice.text}"
            last = self._sent.get(key)
            if last is not None and now - last < REPEAT_SECONDS:
                return Delivered(notice, ())
            self._sent[key] = now
        held = not notice.kind.urgent and settings.quiet_at(self._local())
        channels: list[str] = []
        if not held:
            if self._toast is not None and settings.wants("toast", notice.kind):
                channels.append("toast")
            telegram_on = settings.telegram_enabled and bool(settings.chat_ids)
            telegram_on = telegram_on and settings.wants("telegram", notice.kind)
            if self._telegram is not None and telegram_on:
                channels.append("telegram")
        for channel in channels:
            sink = self._toast if channel == "toast" else self._telegram
            try:
                if sink is not None:
                    sink(notice)
            except Exception as error:
                why = f"{type(error).__name__}: {error}"
                self._log("WARNING", f"Notification by {channel} failed: {why}")
        where = ", ".join(channels) or ("held (quiet hours)" if held else "not sent (off)")
        level = "WARNING" if notice.kind.urgent else "INFO"
        self._log(level, f"Notice {notice.kind.value}: {notice.text} [{where}]")
        delivered = Delivered(notice, tuple(channels), held)
        with self._lock:
            self._history.append(delivered)
        return delivered
