"""Telegram signals get their own place in the AI Lab (docs/TELEGRAM_SIGNALS.md 1, 0.47.0).

Until 0.46 a channel's order card, its follow-ups and the feed's lines went into whichever
chat was open, and a new chat took them away. Now the AI Lab has two tabs, Chat and Telegram
signals (`LabTabs`): every channel card and line goes to the second one (`SignalInbox`),
newest first, and stays there for the whole session. The tab and the sidebar's AI Lab show
how many wait for you. Review in chat asks the AI about one signal in the chat, only when
you press it. A signal you paste in the chat stays in the chat.

Nothing here sends anything: the cards keep their own hold buttons.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.ai.usage_store import short_tokens
from app.ui.channel_cards import HoldCard
from app.ui.lab_parts import LTR, apply_tree, lab_button, lab_label
from app.ui.lab_soft import fade_in
from app.ui.signal_card import OrderCard
from app.ui.theme import DEFAULT, ThemeTokens

KEEP = 200  # entries kept; the oldest finished ones go first
COUNT_MS = 1000
ORDER, FOLLOW, NOTE = "order", "follow", "note"
FILTERS = ("all", "waiting", "notes")
CHAT_TAB, SIGNALS_TAB = 0, 1

INBOX_FA: dict[str, str] = {
    "Chat": "گفتگو",
    "Telegram signals": "سیگنال‌های تلگرام",
    "All": "همه",
    "Waiting for you": "منتظر شما",
    "Notes": "یادداشت‌ها",
    "Clear finished": "پاک کردن تمام‌شده‌ها",
    "Review in chat": "بررسی در چت",
    "ORDER": "سفارش",
    "FOLLOW-UP": "پیگیری",
    "NOTE": "یادداشت",
    "No channel signals yet": "هنوز سیگنالی از کانال‌ها نیامده",
    "Turn a channel on in Settings > Telegram channels: its signals and notes show up here.": (
        "یک کانال را در تنظیمات > کانال‌های تلگرام روشن کنید: سیگنال‌ها و یادداشت‌هایش اینجا می‌آیند."
    ),
    "{waiting} waiting for you \u00b7 {today} today": "{waiting} منتظر شما \u00b7 {today} امروز",
    "AI: {reads} reading(s) \u00b7 {tokens} tokens": "AI: {reads} خواندن \u00b7 {tokens} توکن",
    "The AI's readings of unclear channel messages today": (
        "خواندن‌های امروز AI از پیام‌های نامفهوم کانال‌ها"
    ),
}
KIND_TAGS = {ORDER: "ORDER", FOLLOW: "FOLLOW-UP", NOTE: "NOTE"}


def inbox_words(fa: bool) -> Callable[[str], str]:
    def word(english: str) -> str:
        return INBOX_FA.get(english, english) if fa else english

    return word


def review_question(channel: str, text: str, fa: bool) -> str:
    """The question Review in chat asks (a question, so the chat sends it to the AI and does
    not read it as a pasted signal)."""
    shown = text if len(text) <= 600 else text[:600] + " \u2026"
    if fa:
        return (
            f"این سیگنال تلگرام از «{channel}» را بررسی کن: ارزش گرفتن دارد؟ "
            f"ریسک‌ها و نکته‌هایش چیست؟\n\n{shown}"
        )
    return (
        f"Review this Telegram signal from {channel}: is it worth taking? "
        f"What are its risks?\n\n{shown}"
    )


def needs_you(widget: QWidget) -> bool:
    """A card that waits for your hold: an order card that waits, or a follow-up or settings
    card that is not done and has something to do."""
    if isinstance(widget, OrderCard):
        return widget.state == "waiting"
    if isinstance(widget, HoldCard):
        return not widget.done and widget.hold_button.isEnabled()
    return False


def finished(widget: QWidget) -> bool:
    if isinstance(widget, OrderCard):
        return widget.state not in ("checking", "waiting", "sending")
    if isinstance(widget, HoldCard):
        return widget.done or not widget.hold_button.isEnabled()
    return True


def clock(at: float) -> str:
    return datetime.fromtimestamp(at).strftime("%H:%M")


@dataclass
class Entry:
    kind: str
    channel: str
    at: float
    frame: QFrame
    card: QWidget | None  # None: a note line
    head: QHBoxLayout
    review: QPushButton | None = None


class SignalInbox(QFrame):
    """The Telegram signals tab: channel cards and lines, newest first."""

    counted = Signal(int)  # cards waiting for you, when the number changes
    review = Signal(str)  # a question for the chat (Review in chat)
    wants_chat = Signal()  # a card's Edit puts its text in the chat's box
    added = Signal(str, str, object)  # the kind, the channel and the card (None: a note)

    def __init__(self, fa: bool = False, utc_now: Callable[[], float] = time.time) -> None:
        super().__init__()
        self.setObjectName("SignalInbox")
        self.fa = fa
        self.word = inbox_words(fa)
        self.now = utc_now
        self.tokens: ThemeTokens = DEFAULT
        self.entries: list[Entry] = []
        self.filter = "all"
        self.waiting = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.filter_buttons: dict[str, QPushButton] = {}
        for key, name in zip(FILTERS, ("All", "Waiting for you", "Notes"), strict=True):
            button = lab_button(self.word(name), "chip")
            button.setObjectName(f"InboxFilter_{key}")
            button.setCheckable(True)
            button.setChecked(key == "all")
            button.clicked.connect(lambda _checked=False, k=key: self.set_filter(k))
            self.filter_buttons[key] = button
            bar.addWidget(button)
        bar.addStretch(1)
        self.ai_line = lab_label("", "muted")
        self.ai_line.setObjectName("InboxAiSpend")
        self.ai_line.setToolTip(self.word("The AI's readings of unclear channel messages today"))
        self.ai_line.hide()
        bar.addWidget(self.ai_line)
        self.summary = lab_label("", "muted")
        self.summary.setObjectName("InboxSummary")
        bar.addWidget(self.summary)
        self.clear_button = lab_button(self.word("Clear finished"), "ghost")
        self.clear_button.setObjectName("InboxClear")
        self.clear_button.clicked.connect(self.clear_finished)
        bar.addWidget(self.clear_button)
        outer.addLayout(bar)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("InboxScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("InboxBody")
        self.rows = QVBoxLayout(body)
        self.rows.setContentsMargins(0, 0, 8, 0)
        self.rows.setSpacing(14)
        self.empty = QFrame()
        self.empty.setObjectName("InboxEmpty")
        self.empty.setProperty("inbox", "empty")
        empty = QVBoxLayout(self.empty)
        empty.setContentsMargins(24, 28, 24, 28)
        empty.setSpacing(6)
        title = lab_label(self.word("No channel signals yet"), "heading")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = self.word(
            "Turn a channel on in Settings > Telegram channels: its signals and notes show up "
            "here.",
        )
        note = lab_label(hint, "muted", wrap=True)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.addWidget(title)
        empty.addWidget(note)
        self.rows.addWidget(self.empty)
        self.rows.addStretch(1)
        self.scroll_area.setWidget(body)
        outer.addWidget(self.scroll_area, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(COUNT_MS)
        self.timer.timeout.connect(self.recount)
        self.timer.start()
        self.recount()

    # Adding -----------------------------------------------------------------------------
    def add_order(self, card: OrderCard, channel: str, text: str) -> Entry:
        """A channel's order card; Review in chat asks the AI about its text."""
        entry = self._add(ORDER, channel, card)
        review = lab_button(self.word("Review in chat"), "link")
        review.setObjectName("InboxReview")
        question = review_question(channel, text, self.fa)
        review.clicked.connect(lambda _checked=False, q=question: self.review.emit(q))
        entry.review = review
        entry.head.addWidget(review)
        card.edit.connect(self.wants_chat.emit)
        return entry

    def add_card(self, card: QWidget, channel: str, kind: str = FOLLOW) -> Entry:
        return self._add(kind, channel, card)

    def note(self, channel: str, text: str) -> Entry:
        """One line of the feed (a half signal waits, a Paper trial read it, the AI read it)."""
        line = lab_label(text, "text", wrap=True)
        line.setObjectName("InboxNote")
        line.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return self._add(NOTE, channel, None, line)

    def _add(
        self,
        kind: str,
        channel: str,
        card: QWidget | None,
        line: QLabel | None = None,
    ) -> Entry:
        at = self.now()
        frame = QFrame()
        frame.setObjectName("InboxEntry")
        frame.setProperty("inbox", "note" if card is None else "entry")
        layout = QVBoxLayout(frame)
        if card is None:
            layout.setContentsMargins(14, 10, 14, 10)
            layout.setSpacing(6)
        else:
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        tag = lab_label(self.word(KIND_TAGS.get(kind, "NOTE")), "kind")
        tag.setObjectName("InboxKind")
        tag.setProperty("kind", kind)
        name = lab_label(channel, "channel")
        name.setObjectName("InboxChannel")
        when = lab_label(clock(at), "mono")
        when.setLayoutDirection(LTR)
        head.addWidget(tag)
        head.addWidget(name)
        head.addWidget(when)
        head.addStretch(1)
        layout.addLayout(head)
        shown: QWidget | None = card if card is not None else line
        if shown is not None:
            layout.addWidget(shown)
        apply_tree(frame, self.tokens)
        if card is not None:
            apply = getattr(card, "apply_tokens", None)
            if callable(apply):
                apply(self.tokens)
        entry = Entry(kind, channel, at, frame, card, head)
        self.entries.insert(0, entry)
        self.rows.insertWidget(0, frame)
        self.empty.setVisible(False)
        self._show(entry)
        fade_in(frame, self.tokens)
        self._trim()
        self.recount()
        bar = self.scroll_area.verticalScrollBar()
        if bar is not None:
            bar.setValue(0)
        self.added.emit(kind, channel, card)
        return entry

    def _trim(self) -> None:
        while len(self.entries) > KEEP:
            old = next(
                (e for e in reversed(self.entries) if e.card is None or finished(e.card)),
                self.entries[-1],
            )
            self._remove(old)

    def _remove(self, entry: Entry) -> None:
        self.entries.remove(entry)
        self.rows.removeWidget(entry.frame)
        entry.frame.hide()
        entry.frame.deleteLater()

    # Showing ----------------------------------------------------------------------------
    def set_filter(self, key: str) -> None:
        self.filter = key if key in FILTERS else "all"
        for name, button in self.filter_buttons.items():
            button.setChecked(name == self.filter)
        for entry in self.entries:
            self._show(entry)

    def _show(self, entry: Entry) -> None:
        if self.filter == "waiting":
            visible = entry.card is not None and needs_you(entry.card)
        elif self.filter == "notes":
            visible = entry.card is None
        else:
            visible = True
        entry.frame.setVisible(visible)

    def clear_finished(self) -> int:
        """Remove the notes and the cards that are over (sent, skipped, expired, refused)."""
        gone = [e for e in self.entries if e.card is None or finished(e.card)]
        for entry in gone:
            self._remove(entry)
        self.empty.setVisible(not self.entries)
        self.recount()
        return len(gone)

    def waiting_cards(self) -> list[QWidget]:
        found: list[QWidget] = []
        for entry in self.entries:
            card = entry.card
            if card is not None and needs_you(card):
                found.append(card)
        return found

    def recount(self) -> int:
        """Timer: the cards waiting for you; `counted` when the number changed."""
        waiting = len(self.waiting_cards())
        day = datetime.fromtimestamp(self.now()).date()
        today = sum(
            1
            for e in self.entries
            if e.card is not None and datetime.fromtimestamp(e.at).date() == day
        )
        self.summary.setText(
            self.word("{waiting} waiting for you \u00b7 {today} today").format(
                waiting=waiting,
                today=today,
            ),
        )
        if self.filter == "waiting":
            for entry in self.entries:
                self._show(entry)
        if waiting != self.waiting:
            self.waiting = waiting
            self.counted.emit(waiting)
        return waiting

    def set_ai_spend(self, reads: int, tokens: int, cost: float) -> str:
        """Today's AI readings of channel messages: how many, the tokens and the cost."""
        text = self.word("AI: {reads} reading(s) \u00b7 {tokens} tokens").format(
            reads=reads,
            tokens=short_tokens(tokens),
        )
        if cost > 0:
            text += f" \u00b7 ${cost:.4f}"
        self.ai_line.setText(text)
        self.ai_line.setVisible(reads > 0)
        return text

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.setStyleSheet(inbox_qss(tokens))


class LabTabs(QFrame):
    """Chat | Telegram signals over the AI Lab's chat column."""

    def __init__(self, inbox: SignalInbox, fa: bool = False) -> None:
        super().__init__()
        self.setObjectName("AiLabTabs")
        self.inbox = inbox
        word = inbox_words(fa)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.chat_tab = lab_button(word("Chat"), "tab")
        self.chat_tab.setObjectName("AiLabChatTab")
        self.signals_tab = lab_button(word("Telegram signals"), "tab")
        self.signals_tab.setObjectName("AiLabSignalsTab")
        self.badge = lab_label("", "badge")
        self.badge.setObjectName("AiLabSignalsBadge")
        self.badge.setLayoutDirection(LTR)
        self.badge.setVisible(False)
        for button in (self.chat_tab, self.signals_tab):
            button.setCheckable(True)
        self.chat_tab.clicked.connect(lambda _checked=False: self.show_tab(CHAT_TAB))
        self.signals_tab.clicked.connect(lambda _checked=False: self.show_tab(SIGNALS_TAB))
        bar.addWidget(self.chat_tab)
        bar.addWidget(self.signals_tab)
        bar.addWidget(self.badge)
        bar.addStretch(1)
        outer.addLayout(bar)
        self.stack = QStackedWidget()
        self.stack.setObjectName("AiLabTabStack")
        self.stack.addWidget(QWidget())  # the chat's place until `set_chat`
        self.stack.addWidget(inbox)
        outer.addWidget(self.stack, 1)
        inbox.counted.connect(self.set_count)
        inbox.wants_chat.connect(lambda: self.show_tab(CHAT_TAB))
        self.show_tab(CHAT_TAB)

    def set_chat(self, chat: QWidget) -> None:
        old = self.stack.widget(CHAT_TAB)
        self.stack.insertWidget(CHAT_TAB, chat)
        if old is not None:
            self.stack.removeWidget(old)
            old.deleteLater()
        self.show_tab(CHAT_TAB)

    def show_tab(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.chat_tab.setChecked(index == CHAT_TAB)
        self.signals_tab.setChecked(index == SIGNALS_TAB)

    def set_count(self, count: int) -> None:
        self.badge.setText(str(count) if count else "")
        self.badge.setVisible(count > 0)

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.setStyleSheet(tabs_qss(tokens))


def holder_of(layout: QLayout | None, widget: QWidget) -> QLayout | None:
    """The layout (or nested layout) that holds `widget` directly."""
    if layout is None:
        return None
    if layout.indexOf(widget) >= 0:
        return layout
    for index in range(layout.count()):
        item = layout.itemAt(index)
        found = holder_of(item.layout() if item is not None else None, widget)
        if found is not None:
            return found
    return None


def mount(
    page: QWidget,
    chat: QWidget,
    inbox: SignalInbox,
    fa: bool = False,
    tokens: ThemeTokens | None = None,
) -> LabTabs | None:
    """Put the tabs where the chat was (its stretch kept) and the chat in the first tab."""
    layout = holder_of(page.layout(), chat)
    if layout is None:
        return None
    tabs = LabTabs(inbox, fa)
    stretch = 0
    if isinstance(layout, QBoxLayout):
        stretch = layout.stretch(layout.indexOf(chat))
    if layout.replaceWidget(chat, tabs) is None:
        tabs.deleteLater()
        return None
    tabs.set_chat(chat)
    if isinstance(layout, QBoxLayout):
        layout.setStretchFactor(tabs, max(1, stretch))
    if tokens is not None:
        tabs.apply_tokens(tokens)
        inbox.apply_tokens(tokens)
    return tabs


def tabs_qss(tokens: ThemeTokens) -> str:
    t = tokens
    return f"""
QPushButton[lab="tab"] {{
    background-color: transparent;
    color: {t.text_secondary};
    border: 1px solid transparent;
    border-radius: 15px;
    padding: 6px 16px;
    font-weight: 600;
}}
QPushButton[lab="tab"]:hover {{
    background-color: {t.hover};
    color: {t.text};
}}
QPushButton[lab="tab"]:checked {{
    background-color: {t.accent};
    color: {t.accent_text};
}}
QLabel[lab="badge"] {{
    background-color: {t.warning};
    color: {t.surface};
    border-radius: 10px;
    padding: 1px 8px;
    font-weight: 700;
}}
"""


def inbox_qss(tokens: ThemeTokens) -> str:
    t = tokens
    return f"""
QFrame#SignalInbox, QWidget#InboxBody {{
    background-color: transparent;
}}
QFrame[inbox="empty"] {{
    background-color: {t.surface};
    border: 1px dashed {t.border_strong};
    border-radius: 14px;
}}
QFrame[inbox="note"] {{
    background-color: {t.surface};
    border: 1px solid {t.border};
    border-radius: 12px;
}}
QPushButton[lab="chip"] {{
    background-color: {t.surface};
    color: {t.text_secondary};
    border: 1px solid {t.border};
    border-radius: 13px;
    padding: 4px 12px;
}}
QPushButton[lab="chip"]:hover {{
    color: {t.text};
    border-color: {t.border_strong};
}}
QPushButton[lab="chip"]:checked {{
    background-color: {t.accent_soft};
    color: {t.text};
    border-color: {t.border_strong};
}}
QLabel[lab="kind"] {{
    background-color: {t.accent_soft};
    color: {t.text};
    border-radius: 8px;
    padding: 1px 8px;
    font-weight: 700;
}}
QLabel[lab="kind"][kind="order"] {{
    background-color: {t.warning_soft};
    color: {t.warning};
}}
QLabel[lab="kind"][kind="follow"] {{
    background-color: {t.profit_soft};
    color: {t.profit};
}}
QLabel[lab="channel"] {{
    color: {t.text};
    font-weight: 600;
}}
"""
