"""Telegram signals get their own tab in the AI Lab (docs/TELEGRAM_SIGNALS.md 1, 0.47.0): a
channel's cards and lines go there, stay after a new chat and are counted while they wait
for you; a pasted signal stays in the chat."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QVBoxLayout, QWidget
from pytestqt.qtbot import QtBot

from app.channels.folder import ChannelMessage
from app.channels.policy import Action, ChannelPolicy
from app.ui.channel_feed import ChannelFeed
from app.ui.lab_actions import LabActions
from app.ui.signal_card import OrderCard
from app.ui.signal_inbox import (
    CHAT_TAB,
    NOTE,
    ORDER,
    SIGNALS_TAB,
    SignalInbox,
    holder_of,
    inbox_qss,
    mount,
    review_question,
)
from app.ui.theme import DARK
from tests.ui.test_channel_feed import GOLD, LIVE, channel
from tests.ui.test_signal_card import desk_page, pasted
from tests.unit.storage_helpers import temporary_store
from tests.unit.test_signal_desk import buy_text


def test_a_channel_card_goes_to_the_signals_tab_and_stays(qtbot: QtBot, tmp_path: Path) -> None:
    page, desk, signals, now, price = desk_page(qtbot, tmp_path)
    inbox = SignalInbox(utc_now=lambda: now)
    tabs = mount(page, page.chat, inbox)
    assert tabs is not None and tabs.stack.widget(CHAT_TAB) is page.chat
    assert tabs.chat_tab.isChecked() and not tabs.signals_tab.isChecked()
    desk.inbox = inbox
    counts: list[int] = []
    inbox.counted.connect(counts.append)
    with temporary_store() as store:
        repository, _magic = channel(store, LIVE)
        feed = ChannelFeed(desk, repository, utc_now=lambda: now)
        before = len(page.chat.extras)
        found = feed.handle(ChannelMessage(GOLD.id, 7, now, buy_text(price)))
        assert found is not None and found.action is Action.CARD
        assert len(page.chat.extras) == before  # not in the chat
        entry = inbox.entries[0]
        shown = entry.card
        assert isinstance(shown, OrderCard)
        card: OrderCard = shown
        assert (entry.kind, entry.channel) == (ORDER, "Gold Room")
        signals.on_cycle(now + 1)
        qtbot.waitUntil(lambda: card.state == "waiting")
        assert inbox.recount() == 1 and counts[-1:] == [1]
        assert tabs.badge.text() == "1"
        page.chat.new_chat()
        assert inbox.entries[0].card is card  # a new chat keeps it
        questions: list[str] = []
        inbox.review.connect(questions.append)
        assert entry.review is not None
        entry.review.click()
        assert questions and "Gold Room" in questions[0] and "?" in questions[0]
        tabs.show_tab(SIGNALS_TAB)
        card.edit.emit()  # Edit puts the text in the chat's box: the chat tab opens
        assert tabs.stack.currentIndex() == CHAT_TAB
        desk.skip(card.request_id)
        assert inbox.recount() == 0 and counts[-1] == 0 and tabs.badge.isHidden()
        assert inbox.clear_finished() == 1 and not inbox.entries
        assert not inbox.empty.isHidden()


def test_feed_lines_go_to_the_tab_and_a_pasted_signal_stays_in_the_chat(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    page, desk, _signals, now, price = desk_page(qtbot, tmp_path)
    inbox = SignalInbox(utc_now=lambda: now)
    assert mount(page, page.chat, inbox) is not None
    desk.inbox = inbox
    with temporary_store() as store:
        repository, _magic = channel(store, ChannelPolicy())  # a Paper trial
        feed = ChannelFeed(desk, repository, utc_now=lambda: now)
        LabActions(page, repository=repository, feed=feed, utc_now=lambda: now)
        feed.arrived.emit(ChannelMessage(GOLD.id, 1, now, buy_text(price)))
        qtbot.waitUntil(lambda: len(inbox.entries) > 0)
        note = inbox.entries[0]
        assert (note.kind, note.channel, note.card) == (NOTE, "Gold Room", None)
        inbox.set_filter("waiting")
        assert note.frame.isHidden()
        inbox.set_filter("notes")
        assert not note.frame.isHidden()
        inbox.set_filter("all")
        card = pasted(page, buy_text(price))
        assert page.chat.extras[-1] is card
        assert all(entry.card is not card for entry in inbox.entries)


def test_mounting_needs_the_chat_in_a_layout(qtbot: QtBot) -> None:
    page = QWidget()
    qtbot.addWidget(page)
    outer = QVBoxLayout(page)
    inner = QVBoxLayout()
    chat = QWidget()
    inner.addWidget(chat, 1)
    outer.addLayout(inner)
    assert holder_of(outer, chat) is inner
    assert holder_of(outer, QWidget()) is None
    inbox = SignalInbox(fa=True)
    tabs = mount(page, chat, inbox, fa=True, tokens=DARK)
    assert tabs is not None and inner.indexOf(tabs) == 0 and inner.stretch(0) == 1
    assert tabs.signals_tab.text() == "سیگنال‌های تلگرام"
    assert DARK.warning_soft in inbox_qss(DARK) and DARK.surface in inbox.styleSheet()
    assert mount(QWidget(), QWidget(), SignalInbox()) is None
    assert "؟" in review_question("Gold Room", "XAUUSD buy", True)
