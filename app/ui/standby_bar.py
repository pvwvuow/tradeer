"""A line over the Telegram signals tab that says which channels are on standby (phase 22k,
0.50.1): "Standby: Gold Room (XAUUSD) 8:20", with the time left, so the owner sees at a glance
that the AI is reading a channel's every message. Hidden when no channel is on standby.

It is put into the tab's layout from outside (`StandbyBar.attach`), so the tab itself is
unchanged. Read only: it shows `StandbyFeed.standby` and sends nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QBoxLayout

from app.channels.standby import StandbyBook
from app.ui.lab_parts import lab_label
from app.ui.signal_inbox import SignalInbox

BAR_MS = 1000
TITLE = "Standby"
TITLE_FA = "\u0622\u0645\u0627\u062f\u0647\u200c\u0628\u0627\u0634"  # standby, in Persian
TIP = "Every new message of this channel goes to the AI at once"
TIP_FA = (
    "\u0647\u0631 \u067e\u06cc\u0627\u0645 \u062a\u0627\u0632\u0647 "
    "\u0627\u06cc\u0646 \u06a9\u0627\u0646\u0627\u0644 \u0641\u0648\u0631\u06cc "
    "\u0628\u0647 AI \u0645\u06cc\u200c\u0631\u0648\u062f"
)
AFTER_FILTERS = 1  # the tab's first row holds the filters


class Titled(Protocol):
    @property
    def title(self) -> str: ...


class Sources(Protocol):
    def source(self, channel_id: int) -> Titled | None: ...


class Standing(Protocol):
    """What the line needs of `StandbyFeed`."""

    @property
    def now(self) -> Callable[[], float]: ...

    @property
    def standby(self) -> StandbyBook: ...

    @property
    def repository(self) -> Sources: ...


def left(seconds: float) -> str:
    whole = max(0, int(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def standby_lines(feed: Standing) -> list[str]:
    """One text per channel on standby: its title, the instrument it named, the time left."""
    now = feed.now()
    lines: list[str] = []
    for stood in feed.standby.active(now):
        source = feed.repository.source(stood.channel_id)
        title = source.title if source is not None else str(stood.channel_id)
        named = f" ({stood.hint})" if stood.hint else ""
        lines.append(f"{title}{named} {left(stood.until - now)}")
    return lines


class StandbyBar:
    def __init__(self, lines: Callable[[], list[str]], fa: bool = False) -> None:
        self.lines = lines
        self.fa = fa
        self.label = lab_label("", "badge")
        self.label.setObjectName("InboxStandby")
        self.label.setToolTip(TIP_FA if fa else TIP)
        self.label.hide()
        self.timer = QTimer(self.label)
        self.timer.setInterval(BAR_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def attach(self, inbox: SignalInbox) -> bool:
        """Put the line under the tab's filters (True when it fit)."""
        layout = inbox.layout()
        if not isinstance(layout, QBoxLayout):
            return False
        layout.insertWidget(AFTER_FILTERS, self.label, 0, Qt.AlignmentFlag.AlignLeft)
        return True

    def refresh(self) -> str:
        """Timer: show the channels on standby, or hide the line."""
        found = self.lines()
        title = TITLE_FA if self.fa else TITLE
        text = f"{title}: " + " \u00b7 ".join(found) if found else ""
        if self.label.text() != text:
            self.label.setText(text)
        self.label.setVisible(bool(found))
        return text
