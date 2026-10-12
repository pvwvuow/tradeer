"""The AI stands by for a channel (phase 22j, 0.50.0): a short hint from the admin is
glanced at by the AI, a teaser opens the standby by itself, and while it lasts every new
message of the channel is read quickly by the AI and put together with the earlier ones."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pytestqt.qtbot import QtBot

from app.channels.folder import ChannelMessage
from app.channels.policy import Action
from app.channels.standby import GLANCE_TOKENS, QUICK_READ_TOKENS
from app.ui.signal_card import OrderCard
from app.ui.standby_feed import StandbyFeed
from tests.ui.test_channel_feed import GOLD, LIVE, channel
from tests.ui.test_channel_reading_feed import idea
from tests.ui.test_signal_card import desk_page
from tests.unit.storage_helpers import temporary_store

HINT = "Good evening everyone, watching the dollar closely"
NOISE = "Look at this one carefully everyone"


@dataclass(frozen=True)
class Answer:
    text: str
    json_text: str | None


@dataclass
class TwoFaced:
    """An AI that answers a glance with `glance` and a reading with `read`."""

    glance: Mapping[str, Any]
    read: Mapping[str, Any]
    calls: list[tuple[str, int, str]] = field(default_factory=list)  # what, tokens, system

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        schema: Mapping[str, Any] | None = None,
        max_tokens: int = 0,
    ) -> Answer:
        glancing = schema is not None and "standby" in schema["properties"]
        self.calls.append(("glance" if glancing else "read", max_tokens, messages[0]["content"]))
        text = json.dumps(self.glance if glancing else self.read)
        return Answer(text, text)


def stands(feed: StandbyFeed) -> bool:
    return feed.standing(GOLD.id)


def make(desk: Any, repository: Any, clock: list[float], ai: Any) -> StandbyFeed:
    return StandbyFeed(desk, repository, utc_now=lambda: clock[0], ai=lambda: ai)


def carded(page: Any) -> bool:
    extras = page.chat.extras
    return bool(extras) and isinstance(extras[-1], OrderCard)


def other() -> dict[str, Any]:
    return {
        "kind": "other",
        "symbol": "",
        "side": "",
        "order": "",
        "entry": [],
        "sl": None,
        "tps": [],
        "why": "chatter",
    }


def test_a_hint_means_standby_and_the_pieces_make_a_card(qtbot: QtBot, tmp_path: Path) -> None:
    page, desk, _signals, now, price = desk_page(qtbot, tmp_path)
    text, reply = idea(price)
    ai = TwoFaced({"standby": True, "symbol": "usd", "why": "he talks about the dollar"}, reply)
    with temporary_store() as store:
        repository, _magic = channel(store, LIVE)
        feed = make(desk, repository, [now], ai)
        lines: list[str] = []
        feed.noted.connect(lambda _title, line: lines.append(line))
        first = feed.handle(ChannelMessage(GOLD.id, 1, now, HINT))
        assert first is not None and first.reason == "the AI glances at it: is a signal coming?"
        qtbot.waitUntil(lambda: stands(feed), timeout=5000)
        assert lines[-1].startswith("standby (USD): he talks about the dollar")
        assert ai.calls[0][:2] == ("glance", GLANCE_TOKENS)
        second = feed.handle(ChannelMessage(GOLD.id, 2, now + 20, text))
        assert second is not None and second.reason == "an unclear message: the AI reads it"
        qtbot.waitUntil(lambda: carded(page), timeout=5000)
        assert ai.calls[1][:2] == ("read", QUICK_READ_TOKENS)
        assert "on standby" in ai.calls[1][2] and "he named USD" in ai.calls[1][2]
        assert not stands(feed)  # the signal came: the standby is over


def test_a_teaser_opens_the_standby_and_a_plain_message_is_read(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    _page, desk, _signals, now, _price = desk_page(qtbot, tmp_path)
    ai = TwoFaced({"standby": False, "symbol": "", "why": ""}, other())
    clock = [now]
    with temporary_store() as store:
        repository, _magic = channel(store, LIVE)
        feed = make(desk, repository, clock, ai)
        lines: list[str] = []
        feed.noted.connect(lambda _title, line: lines.append(line))
        feed.handle(ChannelMessage(GOLD.id, 1, now, "Be ready everyone, a signal is coming soon"))
        assert stands(feed) and not ai.calls  # a teaser needs no AI to open it
        assert any(line.startswith("standby") for line in lines)
        clock[0] = now + 30
        found = feed.handle(ChannelMessage(GOLD.id, 2, now + 30, NOISE))
        assert found is not None and found.action is Action.SKIP
        assert found.reason == "standby: the AI reads it"
        qtbot.waitUntil(lambda: len(ai.calls) == 1, timeout=5000)
        assert ai.calls[0][0] == "read" and "on standby" in ai.calls[0][2]
        assert stands(feed)  # nothing came of it yet
        clock[0] = now + 11 * 60
        feed.expire_drafts()
        assert not stands(feed) and lines[-1] == "standby over: no signal came"


def test_no_ai_and_a_no_answer_leave_the_channel_alone(qtbot: QtBot, tmp_path: Path) -> None:
    _page, desk, _signals, now, _price = desk_page(qtbot, tmp_path)
    with temporary_store() as store:
        repository, _magic = channel(store, LIVE)
        off = make(desk, repository, [now], "the AI is off")
        lines: list[str] = []
        off.noted.connect(lambda _title, line: lines.append(line))
        found = off.handle(ChannelMessage(GOLD.id, 1, now, HINT))
        assert found is not None and found.reason != "the AI glances at it: is a signal coming?"
        assert not stands(off) and not lines  # a glance never complains about a missing AI
        ai = TwoFaced({"standby": False, "symbol": "", "why": "an ad"}, other())
        quiet = make(desk, repository, [now], ai)
        quiet.handle(ChannelMessage(GOLD.id, 2, now, HINT))
        qtbot.waitUntil(lambda: len(ai.calls) == 1, timeout=5000)
        qtbot.wait(50)
        assert not stands(quiet)
