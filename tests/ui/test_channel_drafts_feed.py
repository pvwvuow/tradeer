"""A Live channel's half signal waits for its rest and then becomes one order card (0.44.0):
a part, then the stop loss and targets; an edit that completes it; a draft that expires."""

from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

from app.channels.folder import ChannelMessage
from app.channels.policy import Action
from app.ui.channel_feed import ChannelFeed
from app.ui.signal_card import OrderCard
from tests.ui.test_channel_feed import GOLD, LIVE, channel
from tests.ui.test_signal_card import desk_page
from tests.unit.storage_helpers import temporary_store


def rest(price: float) -> str:
    return f"sl {price - 0.002:.5f} tp {price + 0.0025:.5f} tp {price + 0.004:.5f}"


def test_a_part_and_its_rest_become_one_card(qtbot: QtBot, tmp_path: Path) -> None:
    page, desk, _signals, now, price = desk_page(qtbot, tmp_path)
    with temporary_store() as store:
        repository, _magic = channel(store, LIVE)
        feed = ChannelFeed(desk, repository, utc_now=lambda: now)
        lines: list[tuple[str, str]] = []
        feed.noted.connect(lambda title, text: lines.append((title, text)))
        before = len(page.chat.extras)
        waiting = feed.handle(ChannelMessage(GOLD.id, 1, now, "EURUSD buy"))
        assert waiting is not None and waiting.action is Action.SKIP
        assert waiting.reason.startswith("waiting for the rest")
        assert len(page.chat.extras) == before and lines[-1][0] == "Gold Room"
        done = feed.handle(ChannelMessage(GOLD.id, 2, now + 30, rest(price)))
        assert done is not None and done.action is Action.CARD, done
        assert isinstance(page.chat.extras[-1], OrderCard)
        assert lines[-1] == ("Gold Room", "2 messages joined into one signal")
        assert len(feed.drafts) == 0


def test_an_edit_completes_the_signal(qtbot: QtBot, tmp_path: Path) -> None:
    page, desk, _signals, now, price = desk_page(qtbot, tmp_path)
    with temporary_store() as store:
        repository, _magic = channel(store, LIVE)
        feed = ChannelFeed(desk, repository, utc_now=lambda: now)
        feed.handle(ChannelMessage(GOLD.id, 5, now, "EURUSD buy"))
        assert feed.handle_edit(ChannelMessage(GOLD.id, 9, now, "anything")) is None
        found = feed.handle_edit(ChannelMessage(GOLD.id, 5, now, f"EURUSD buy {rest(price)}"))
        assert found is not None and found.action is Action.CARD
        assert isinstance(page.chat.extras[-1], OrderCard)


def test_an_incomplete_draft_expires_without_a_card(qtbot: QtBot, tmp_path: Path) -> None:
    page, desk, _signals, now, _price = desk_page(qtbot, tmp_path)
    clock = [now]
    with temporary_store() as store:
        repository, _magic = channel(store, LIVE)
        feed = ChannelFeed(desk, repository, utc_now=lambda: clock[0])
        lines: list[str] = []
        feed.noted.connect(lambda _title, text: lines.append(text))
        feed.handle(ChannelMessage(GOLD.id, 1, now, "EURUSD buy"))
        before = len(page.chat.extras)
        clock[0] = now + 16 * 60
        assert [draft.channel_id for draft in feed.expire_drafts()] == [GOLD.id]
        assert lines[-1].startswith("the signal stayed incomplete (no stop loss")
        assert len(page.chat.extras) == before
