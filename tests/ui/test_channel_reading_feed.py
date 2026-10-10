"""The AI reads an unclear channel message (0.44.0): a checked reading becomes one order card
in a Live channel; a reading with a number the channel never wrote makes none; a result claim
never goes to the AI; without an AI connection an unclear message is only noise. A picture
goes to the AI with its message (0.44.1)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pytestqt.qtbot import QtBot

from app.channels.folder import ChannelMessage
from app.channels.policy import Action
from app.channels.reading import CLAIM_REASON
from app.ui.channel_feed import ChannelFeed
from app.ui.signal_card import OrderCard
from tests.ui.test_channel_feed import GOLD, LIVE, channel
from tests.ui.test_signal_card import desk_page
from tests.unit.storage_helpers import temporary_store


@dataclass(frozen=True)
class Answer:
    text: str
    json_text: str | None


@dataclass
class FakeAi:
    """An AI that answers every reading with one JSON object."""

    reply: Mapping[str, Any]
    asked: list[Sequence[Mapping[str, str]]] = field(default_factory=list)

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        schema: Mapping[str, Any] | None = None,
        max_tokens: int = 0,
    ) -> Answer:
        self.asked.append(messages)
        text = json.dumps(self.reply)
        return Answer(text, text)


def carded(page: Any) -> bool:
    """The last thing in the chat is an order card."""
    extras = page.chat.extras
    return bool(extras) and isinstance(extras[-1], OrderCard)


def reading_feed(desk: Any, repository: Any, now: float, ai: FakeAi) -> ChannelFeed:
    return ChannelFeed(desk, repository, utc_now=lambda: now, ai=lambda: ai)


def idea(price: float) -> tuple[str, dict[str, Any]]:
    sl, one, two = f"{price - 0.002:.5f}", f"{price + 0.0025:.5f}", f"{price + 0.004:.5f}"
    text = f"EURUSD idea: area {price:.5f}, invalidation {sl}, objectives {one} and {two}"
    reply = {
        "kind": "signal",
        "symbol": "EURUSD",
        "side": "buy",
        "order": "market",
        "entry": [],
        "sl": float(sl),
        "tps": [float(one), float(two)],
        "why": "a buy idea",
    }
    return text, reply


def test_a_checked_reading_becomes_one_card(qtbot: QtBot, tmp_path: Path) -> None:
    page, desk, _signals, now, price = desk_page(qtbot, tmp_path)
    text, reply = idea(price)
    ai = FakeAi(reply)
    with temporary_store() as store:
        repository, _magic = channel(store, LIVE)
        feed = reading_feed(desk, repository, now, ai)
        lines: list[str] = []
        feed.noted.connect(lambda _title, line: lines.append(line))
        found = feed.handle(ChannelMessage(GOLD.id, 1, now, text))
        assert found is not None and found.action is Action.SKIP
        assert found.reason == "an unclear message: the AI reads it"
        qtbot.waitUntil(lambda: carded(page), timeout=5000)
        assert len(ai.asked) == 1 and text in ai.asked[0][1]["content"]
        assert lines[-1].startswith("the AI read the message as: EURUSD buy now sl ")
        assert feed.reads.left(now) == feed.reads.limit - 1
        assert feed.handle(ChannelMessage(GOLD.id, 1, now, text)) is not None
        assert len(ai.asked) == 1  # one reading per message


def test_an_invented_number_makes_no_card(qtbot: QtBot, tmp_path: Path) -> None:
    page, desk, _signals, now, price = desk_page(qtbot, tmp_path)
    text, reply = idea(price)
    ai = FakeAi({**reply, "sl": float(f"{price - 0.003:.5f}")})
    with temporary_store() as store:
        repository, _magic = channel(store, LIVE)
        feed = reading_feed(desk, repository, now, ai)
        lines: list[str] = []
        feed.noted.connect(lambda _title, line: lines.append(line))
        before = len(page.chat.extras)
        feed.handle(ChannelMessage(GOLD.id, 2, now, text))
        qtbot.waitUntil(lambda: bool(lines), timeout=5000)
        assert lines[-1] == "the AI gave a number the channel did not write"
        assert len(page.chat.extras) == before


def test_claims_and_no_ai_cost_nothing(qtbot: QtBot, tmp_path: Path) -> None:
    _page, desk, _signals, now, price = desk_page(qtbot, tmp_path)
    text, reply = idea(price)
    ai = FakeAi(reply)
    with temporary_store() as store:
        repository, _magic = channel(store, LIVE)
        feed = reading_feed(desk, repository, now, ai)
        claim = feed.handle(ChannelMessage(GOLD.id, 3, now, "+120 pips today 🔥"))
        assert claim is not None and claim.reason == CLAIM_REASON and not ai.asked
        off = ChannelFeed(desk, repository, utc_now=lambda: now, ai=lambda: "the AI is off")
        plain = off.handle(ChannelMessage(GOLD.id, 4, now, text))
        assert plain is not None and plain.reason != "an unclear message: the AI reads it"
        assert not ai.asked


def test_a_picture_signal_becomes_one_card(qtbot: QtBot, tmp_path: Path) -> None:
    page, desk, _signals, now, price = desk_page(qtbot, tmp_path)
    _text, reply = idea(price)
    ai = FakeAi(reply)
    with temporary_store() as store:
        repository, _magic = channel(store, LIVE)
        feed = reading_feed(desk, repository, now, ai)
        lines: list[str] = []
        feed.noted.connect(lambda _title, line: lines.append(line))
        photo = ChannelMessage(GOLD.id, 7, now, "", photo=b"\xff\xd8\xff\xe0 a picture")
        found = feed.handle(photo)
        assert found is not None and found.reason == "a picture: the AI reads it"
        qtbot.waitUntil(lambda: carded(page), timeout=5000)
        content = ai.asked[0][1]["content"]
        assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert lines[-1].endswith("(check the numbers on the card)")
