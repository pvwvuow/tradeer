"""The Telegram signals tab remembers (0.49.1): after the app was closed and opened again the
lines and cards of the channels are back as history, and Clear finished removes them for
good."""

from __future__ import annotations

import re

from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from app.storage.inbox_store import InboxStore
from app.ui.channel_cards import FollowUpCard
from app.ui.signal_inbox import EARLIER, INBOX_FA, NOTE, SignalInbox
from tests.unit.storage_helpers import temporary_store

NOW = 1_800_000_000.0
DAY = 86_400.0


def label_texts(inbox: SignalInbox) -> list[str]:
    return [label.text() for label in inbox.findChildren(QLabel)]


def test_notes_come_back_after_a_restart_and_clear_removes_them(qtbot: QtBot) -> None:
    with temporary_store() as store:
        history = InboxStore(store)
        first = SignalInbox(utc_now=lambda: NOW)
        qtbot.addWidget(first)
        assert first.attach_history(history) == 0
        first.note("Gold Room", "waiting for the rest of the signal (no stop loss yet)")
        first.note("FX Room", "the AI read the message as: EURUSD buy now sl 1.08 tp 1.09")
        assert [row.channel for row in history.recent(10)] == ["Gold Room", "FX Room"]

        second = SignalInbox(utc_now=lambda: NOW + 2 * DAY)  # the app opened two days later
        qtbot.addWidget(second)
        assert second.attach_history(history) == 2
        assert [(e.kind, e.channel) for e in second.entries] == [
            (NOTE, "FX Room"),
            (NOTE, "Gold Room"),
        ]  # newest first, as when they came
        assert all(e.card is None and e.row_id for e in second.entries)
        assert second.empty.isHidden()
        texts = label_texts(second)
        assert any(text.startswith(EARLIER) for text in texts)
        assert any(re.fullmatch(r"\d\d-\d\d \d\d:\d\d", text) for text in texts)  # another day
        assert second.clear_finished() == 2 and not second.entries
        assert len(history.recent(10)) == 0

        third = SignalInbox(utc_now=lambda: NOW + 3 * DAY)
        qtbot.addWidget(third)
        assert third.attach_history(history) == 0 and not third.empty.isHidden()


def test_a_card_is_kept_with_the_state_it_ended_in(qtbot: QtBot) -> None:
    with temporary_store() as store:
        history = InboxStore(store)
        inbox = SignalInbox(utc_now=lambda: NOW)
        qtbot.addWidget(inbox)
        inbox.attach_history(history)
        card = FollowUpCard("Gold Room", "close now", ["close 1 trade"], fa=False)
        entry = inbox.add_card(card, "Gold Room")
        assert entry.row_id and history.recent(5)[-1].state
        assert "close now" in history.recent(5)[-1].text
        card.finish("Done: sent to the engine.")
        inbox.recount()
        assert history.recent(5)[-1].state == "done"

        later = SignalInbox(utc_now=lambda: NOW + DAY)
        qtbot.addWidget(later)
        later.attach_history(history)
        assert len(later.entries) == 1 and later.entries[0].card is None
        assert later.entries[0].channel == "Gold Room"


def test_the_earlier_entries_speak_persian_in_the_persian_app(qtbot: QtBot) -> None:
    with temporary_store() as store:
        history = InboxStore(store)
        history.add("order", "Gold Room", NOW, "XAUUSD buy now sl 4180 tp 4205", "waiting")
        inbox = SignalInbox(fa=True, utc_now=lambda: NOW + DAY)
        qtbot.addWidget(inbox)
        assert inbox.attach_history(history) == 1
        assert any(text.startswith(INBOX_FA[EARLIER]) for text in label_texts(inbox))
