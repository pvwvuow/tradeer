"""Small cards of the Telegram channels in the AI Lab chat (docs/SIGNAL_DESK.md 3.4 and 3.6).

- `SettingsCard`: a channel's setting asked for in the chat ("give Gold Room 100 dollars"):
  the old and the new value, saved only after Hold to save.
- `FollowUpCard`: a channel's follow-up of a signal you took ("close now", "move SL to
  entry", "new SL 2340"): what it would do, done only after Hold to apply.
- An AI proposal (`app.ui.lab_actions`): a change the chat asked for, applied after Hold
  to apply.
All use the order card's hold button; letting go early does nothing.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from app.ui.lab_parts import LabCard, lab_button, lab_label
from app.ui.signal_card import HoldButton
from app.ui.theme import ThemeTokens

CARD_FA: dict[str, str] = {
    "AI proposal": "پیشنهاد هوش مصنوعی",
    "Channel setting": "تنظیم کانال",
    "Follow-up": "پیگیری سیگنال",
    "Hold to save": "برای ذخیره نگه دارید",
    "Hold to apply": "برای اجرا نگه دارید",
    "Skip": "رد کردن",
    "Saved.": "ذخیره شد.",
    "Done: sent to the engine.": "انجام شد: به موتور معاملات رفت.",
    "Skipped.": "رد شد.",
    "Nothing changes until you hold the button.": "تا دکمه را نگه ندارید چیزی عوض نمی‌شود.",
}


def card_words(fa: bool) -> dict[str, str]:
    return CARD_FA if fa else {}


class HoldCard(LabCard):
    held = Signal()
    skip = Signal()

    def __init__(self, title: str, note: str, hold: str, *, fa: bool = False) -> None:
        words = card_words(fa)
        super().__init__("paste", words.get(title, title), note)
        self.words = words
        body = QWidget()
        self.lines = QVBoxLayout(body)
        self.lines.setContentsMargins(16, 12, 16, 12)
        self.lines.setSpacing(6)
        self.status = lab_label("", "text", wrap=True)
        self.lines.addWidget(self.status)
        self.add_section(body)
        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.skip_button = lab_button(self.word("Skip"), "ghost")
        self.skip_button.clicked.connect(self._skip)
        self.hold_button = HoldButton(self.word(hold))
        self.hold_button.setAccessibleName(hold)
        self.hold_button.held.connect(self.held.emit)
        row.addWidget(self.skip_button)
        row.addStretch(1)
        row.addWidget(self.hold_button)
        self.add_section(actions, (16, 12, 16, 12))
        self.add_footer(self.word("Nothing changes until you hold the button."))
        self.done = False

    def word(self, english: str) -> str:
        return self.words.get(english, english)

    def add_line(self, text: str) -> None:
        self.lines.insertWidget(self.lines.count() - 1, lab_label(text, "text", wrap=True))

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.hold_button.apply_tokens(tokens)

    def finish(self, text: str) -> None:
        self.done = True
        self.status.setText(text)
        self.hold_button.setEnabled(False)
        self.skip_button.setEnabled(False)

    def _skip(self) -> None:
        self.finish(self.word("Skipped."))
        self.skip.emit()


class SettingsCard(HoldCard):
    def __init__(self, channel: str, field: str, old: str, new: str, *, fa: bool = False) -> None:
        super().__init__("Channel setting", channel, "Hold to save", fa=fa)
        self.setObjectName("AiChannelSettingsCard")
        self.add_line(f"{field.replace('_', ' ')}: {old} \u2192 {new}")


class FollowUpCard(HoldCard):
    def __init__(self, channel: str, text: str, actions: list[str], *, fa: bool = False) -> None:
        super().__init__("Follow-up", channel, "Hold to apply", fa=fa)
        self.setObjectName("AiFollowUpCard")
        self.add_line(text)
        for action in actions:
            self.add_line(f"\u2022 {action}")
        self.hold_button.setEnabled(bool(actions))
