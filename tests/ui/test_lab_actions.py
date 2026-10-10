"""The AI Lab chat can do more (0.43.1): it reads the channels, the trades, the risk limits
and the app guide, checks the app for problems, and a proposal shows a card that changes
nothing until you hold it."""

from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

from app.ai.agent import Tool
from app.channels.folder import ChannelMessage, Peer, PeerKind
from app.channels.policy import ChannelMode, ChannelPolicy
from app.storage.channel_store import ChannelRepository
from app.strategies.registry import STRATEGIES, on_by_default
from app.ui.ai_lab_page import AiLabPage
from app.ui.channel_cards import HoldCard
from app.ui.channel_feed import ChannelFeed
from app.ui.lab_actions import LabActions
from tests.ui.test_signal_card import desk_page, lab
from tests.unit.storage_helpers import temporary_store
from tests.unit.test_signal_desk import buy_text

GOLD = Peer(-1001, "Gold Room", PeerKind.CHANNEL)


def tools_of(page: AiLabPage) -> dict[str, Tool]:
    return {tool.name: tool for tool in page.chat.tools()}


def test_the_chat_gets_the_new_tools_and_a_proposal_waits_for_the_hold(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    page = AiLabPage(lab(tmp_path))
    qtbot.addWidget(page)
    bare = set(tools_of(page))
    actions = LabActions(page)
    tools = tools_of(page)
    added = {"diagnose", "channels", "positions", "risk_settings", "app_guide", "propose"}
    assert set(tools) == bare | added
    assert "not part of this app run" in tools["channels"].run({})
    assert "Telegram channels" in tools["app_guide"].run({"topic": "channels"})
    assert "No strategy is on" not in tools["diagnose"].run({})
    name = next(name for name in STRATEGIES if not on_by_default(name))
    answer = tools["propose"].run({"kind": "strategy", "target": name, "changes": {"on": True}})
    assert answer.startswith("Proposed") and "hold" in answer
    page.chat.turn_done.emit(object())
    card = page.chat.extras[-1]
    assert isinstance(card, HoldCard) and card in actions.cards
    assert card.objectName() == "AiProposalCard"
    context = page.context
    assert context is not None
    assert not context.strategies.settings.entry(name).enabled  # nothing before the hold
    card.held.emit()
    assert card.done and context.strategies.settings.entry(name).enabled
    assert card.status.text().startswith("Saved")
    card.held.emit()  # a second hold does nothing
    refused = tools["propose"].run({"kind": "mode", "value": "auto"})
    assert refused.startswith("Not proposed")
    page.chat.turn_done.emit(object())
    assert page.chat.extras[-1] is card  # no card for a refused proposal


def test_the_chat_reads_the_channels_and_sets_one_live_after_the_hold(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    page = AiLabPage(lab(tmp_path))
    qtbot.addWidget(page)
    with temporary_store() as store:
        repository = ChannelRepository(store)
        repository.sync_folder([GOLD], 100.0)
        repository.set_enabled(GOLD.id, True, 100.0)
        actions = LabActions(page, repository=repository, utc_now=lambda: 200.0)
        tools = tools_of(page)
        text = tools["channels"].run({})
        assert "Gold Room (magic" in text and "paper, budget 0" in text
        diagnosis = tools["diagnose"].run({})
        assert "Paper trial: Gold Room" in diagnosis
        changes = {"budget": 100, "mode": "live"}
        args = {"kind": "channel", "target": "Gold Room", "changes": changes}
        assert tools["propose"].run(args).startswith("Proposed")
        page.chat.turn_done.emit(object())
        card = actions.cards[-1]
        assert repository.policy(GOLD.id) == ChannelPolicy()
        card.held.emit()
        saved = repository.policy(GOLD.id)
        assert saved.mode is ChannelMode.LIVE and saved.budget == 100.0


def test_a_paper_trial_signal_shows_one_line_in_the_chat(qtbot: QtBot, tmp_path: Path) -> None:
    page, desk, _signals, now, price = desk_page(qtbot, tmp_path)
    with temporary_store() as store:
        repository = ChannelRepository(store)
        repository.sync_folder([GOLD], 100.0)
        repository.set_enabled(GOLD.id, True, 100.0)
        feed = ChannelFeed(desk, repository, utc_now=lambda: now)
        LabActions(page, repository=repository, feed=feed, utc_now=lambda: now)
        before = len(page.chat.extras)
        feed.arrived.emit(ChannelMessage(GOLD.id, 1, now, buy_text(price)))
        qtbot.waitUntil(lambda: len(page.chat.extras) > before)
        note = page.chat.extras[-1]
        assert note.objectName() == "AiNoteCard" and feed.count(GOLD.id).paper == 1
        feed.arrived.emit(ChannelMessage(GOLD.id, 2, now, "Join our VIP group today"))
        qtbot.waitUntil(lambda: feed.count(GOLD.id).skipped == 1)
        qtbot.wait(20)
        assert page.chat.extras[-1] is note  # noise shows nothing
