"""The chat's controls are icons as in every chat app (0.47.0): Copy, Regenerate, the thumbs,
the round send button; a chat turn and a channel reading are logged, and a reading's tokens
are counted."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from pytestqt.qtbot import QtBot

from app.ai.usage_store import UsageRecord
from app.channels.folder import ChannelMessage
from app.ui.ai_chat import TurnView, short
from app.ui.channel_feed import ChannelFeed, Reading, spent
from app.ui.chat_controls import (
    ICONS,
    CopyIconButton,
    IconButton,
    RoundSendButton,
    control_words,
    icon_path,
)
from app.ui.signal_inbox import SignalInbox
from tests.ui.test_channel_feed import GOLD, LIVE, channel
from tests.ui.test_signal_card import desk_page
from tests.unit.storage_helpers import temporary_store


def test_every_icon_is_drawn_and_chevrons_mirror(qtbot: QtBot) -> None:
    for name in ICONS:
        assert not icon_path(name).isEmpty(), name
    left = icon_path("chevron_left").boundingRect()
    mirrored = icon_path("chevron_left", mirror=True).boundingRect()
    assert math.isclose(mirrored.left(), 24 - left.right(), abs_tol=0.01)
    button = IconButton("chevron_left", "Earlier prompts")
    qtbot.addWidget(button)
    button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    button.grab()  # paints right to left without trouble
    assert button.toolTip() == button.accessibleName() == "Earlier prompts"
    assert button.width() == button.height() == 30 and button.text() == ""


def test_copy_is_an_icon_that_turns_into_a_check(qtbot: QtBot) -> None:
    button = CopyIconButton(lambda: "EURUSD buy 1.0850", fa=True, name="Copy answer")
    qtbot.addWidget(button)
    assert button.text() == "" and button.toolTip() == "کپی پاسخ"
    button.click()
    clipboard = QGuiApplication.clipboard()
    assert clipboard is not None and clipboard.text() == "EURUSD buy 1.0850"
    assert button.icon_name == "check" and button.toolTip() == "کپی شد"
    qtbot.waitUntil(lambda: button.icon_name == "copy", timeout=3000)
    assert control_words(False)("Copied") == "Copied"


def test_the_send_button_is_round_and_stops_while_busy(qtbot: QtBot) -> None:
    button = RoundSendButton()
    qtbot.addWidget(button)
    button.set_look(True, False)
    button.grab()
    button.set_look(False, True)
    button.grab()
    assert button.busy and not button.has_text


def test_the_thumbs_rate_an_answer_once(qtbot: QtBot) -> None:
    view = TurnView("How am I doing?")
    qtbot.addWidget(view)
    ratings: list[str] = []
    view.rated.connect(ratings.append)
    view.good_button.click()
    assert view.rating == "good" and view.good_button.isChecked()
    view.bad_button.click()
    assert view.rating == "bad" and not view.good_button.isChecked()
    view.bad_button.click()
    assert view.rating == "" and ratings == ["good", "bad", "none"]
    assert view.copy_button.text() == "" and view.retry_button.text() == ""
    assert short("a  b\nc", 10) == "a b c" and short("x" * 20, 5) == "xxxx\u2026"


def test_a_reading_costs_tokens_and_shows_in_the_signals_tab(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    page, desk, _signals, now, _price = desk_page(qtbot, tmp_path)
    inbox = SignalInbox(utc_now=lambda: now)
    qtbot.addWidget(inbox)
    desk.inbox = inbox
    records: list[UsageRecord] = []
    lines: list[str] = []
    with temporary_store() as store:
        repository, _magic = channel(store, LIVE)
        feed = ChannelFeed(
            desk,
            repository,
            utc_now=lambda: now,
            usage=records.append,
            log=lambda _level, text: lines.append(text),
        )
        source = repository.source(GOLD.id)
        assert source is not None
        message = ChannelMessage(GOLD.id, 9, now, "gold now")
        answer = SimpleNamespace(
            model="gpt-test",
            usage=SimpleNamespace(input_tokens=420, cached_tokens=0, output_tokens=60),
            cost=0.0012,
            text='{"kind": "noise"}',
        )
        reading = spent(Reading(GOLD.id, message, ("gold now",), "x"), answer, 1.25)
        assert (reading.input_tokens, reading.output_tokens, reading.model) == (
            420,
            60,
            "gpt-test",
        )
        feed.account(source, reading)
        assert records and records[0].kind == "channel" and records[0].input_tokens == 420
        assert "1.2 s" in lines[-1] and "420+60 tokens" in lines[-1] and "noise" in lines[-1]
        assert feed.spend.reads == 1 and feed.spend.tokens == 480
        assert "480 tokens" in inbox.ai_line.text() and not inbox.ai_line.isHidden()
        empty = spent(Reading(GOLD.id, message, (), "failed"), object(), 0.5)
        feed.account(source, empty)
        assert len(records) == 1  # a failed call spent nothing
    assert page.chat is not None
