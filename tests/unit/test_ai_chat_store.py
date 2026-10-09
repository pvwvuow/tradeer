"""Saved AI Lab chats (docs/UI_V2.md 19b): files, order, groups, search, pin, rename."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.ai.agent import Step, Turn
from app.ai.chat_store import (
    ChatStore,
    chat_data,
    chat_from,
    date_group,
    grouped,
    start_chat,
    title_from,
)
from app.ai.cost import Usage

NOW = datetime(2026, 10, 9, 12, 0).timestamp()
DAY = 86_400


def turn(question: str, answer: str = "Fine.") -> Turn:
    steps = (
        Step("thinking", "Read the trades first."),
        Step("tool", "Read the trades", "days 30", 0.2),
    )
    usage = Usage(input_tokens=900, cached_tokens=100, output_tokens=120, reasoning_tokens=20)
    return Turn(question, answer, steps, usage, 0.004, 2, "grok-test")


def test_a_chat_round_trips_through_its_file(tmp_path: Path) -> None:
    store = ChatStore(tmp_path / "ai_chats")
    chat = start_chat(NOW).with_turn(turn("How did channel_breakout do?"), NOW + 5)
    path = store.save(chat)
    assert path.is_file() and path.suffix == ".json"
    loaded = store.load(chat.chat_id)
    assert loaded == chat
    assert loaded is not None and loaded.title == "How did channel_breakout do?"
    assert loaded.turns[0].steps[1].detail == "days 30"
    assert loaded.turns[0].usage.cached_tokens == 100
    assert "key" not in path.read_text(encoding="utf-8").lower()


def test_titles_are_one_short_line() -> None:
    assert title_from("  Why did\nI lose?  ") == "Why did I lose?"
    long = title_from("word " * 40)
    assert len(long) <= 61 and long.endswith("\u2026")
    assert title_from("   ") == "New chat"


def test_pinned_first_then_newest_in_date_groups(tmp_path: Path) -> None:
    store = ChatStore(tmp_path)
    old = start_chat(NOW - 10 * DAY).with_turn(turn("old"), NOW - 10 * DAY)
    yesterday = start_chat(NOW - DAY).with_turn(turn("yesterday"), NOW - DAY)
    today = start_chat(NOW).with_turn(turn("today"), NOW)
    week = start_chat(NOW - 3 * DAY).with_turn(turn("week"), NOW - 3 * DAY)
    for chat in (old, yesterday, today, week):
        store.save(chat)
    pinned = store.set_pinned(old.chat_id, True)
    assert pinned is not None and pinned.pinned
    titles = [chat.title for chat in store.chats()]
    assert titles == ["old", "today", "yesterday", "week"]
    groups = [name for name, _chats in grouped(store.chats(), NOW)]
    assert groups == ["Pinned", "Today", "Yesterday", "This week"]
    assert date_group(old, NOW) == "Older"


def test_search_rename_delete_and_broken_files(tmp_path: Path) -> None:
    store = ChatStore(tmp_path)
    chat = start_chat(NOW).with_turn(turn("Best session?"), NOW)
    chat = chat.with_turn(turn("And for XAUUSD?"), NOW + 1)
    store.save(chat)
    assert chat.title == "Best session?" and chat.matches("xauusd") and not chat.matches("gbp")
    renamed = store.rename(chat.chat_id, "  Sessions  ")
    assert renamed is not None and renamed.title == "Sessions"
    assert store.rename(chat.chat_id, "   ") is None
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert [found.title for found in store.chats()] == ["Sessions"]
    assert store.delete(chat.chat_id) and not store.delete(chat.chat_id)
    assert store.chats() == []
    assert chat_from({"title": "no id"}) is None
    assert chat_data(chat)["version"] == 1
