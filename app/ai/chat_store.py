"""Saved AI Lab chats (docs/UI_V2.md 19b, docs/AI_LAB_AGENT.md 18c).

Each chat is one JSON file in the app's data folder (`ai_chats/<id>.json`): its title, when
it started and changed, whether it is pinned, and the finished turns (question, steps,
answer, tokens, cost and model). No API key and no tool results are stored, only what the
chat showed. The history rail lists the chats pinned first, then newest first, in date
groups (Today, Yesterday, This week, Older), and searches titles and questions.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from app.ai.agent import Step, Turn
from app.ai.cost import Usage

CHAT_FOLDER = "ai_chats"
VERSION = 1
TITLE_CHARS = 60
GROUPS = ("Pinned", "Today", "Yesterday", "This week", "Older")


def title_from(question: str) -> str:
    """The chat's title: its first question on one line, cut at a word."""
    text = " ".join(question.split())
    if len(text) <= TITLE_CHARS:
        return text or "New chat"
    cut = text[:TITLE_CHARS].rsplit(" ", 1)[0] or text[:TITLE_CHARS]
    return cut + "\u2026"


def new_id(now: float) -> str:
    stamp = datetime.fromtimestamp(now).strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{secrets.token_hex(3)}"


@dataclass(frozen=True)
class Chat:
    chat_id: str
    title: str
    created: float
    updated: float
    pinned: bool = False
    turns: tuple[Turn, ...] = field(default=())

    @property
    def questions(self) -> list[str]:
        return [turn.question for turn in self.turns]

    @property
    def cost(self) -> float:
        return sum(turn.cost for turn in self.turns)

    def with_turn(self, turn: Turn, now: float) -> Chat:
        title = self.title if self.turns else title_from(turn.question)
        return replace(self, title=title, updated=now, turns=(*self.turns, turn))

    def matches(self, text: str) -> bool:
        wanted = text.strip().lower()
        if not wanted:
            return True
        return any(wanted in part.lower() for part in (self.title, *self.questions))


def start_chat(now: float) -> Chat:
    return Chat(new_id(now), "New chat", now, now)


def step_data(step: Step) -> dict[str, Any]:
    return {
        "kind": step.kind,
        "title": step.title,
        "detail": step.detail,
        "seconds": round(step.seconds, 3),
        "ok": step.ok,
    }


def turn_data(turn: Turn) -> dict[str, Any]:
    usage = turn.usage
    return {
        "question": turn.question,
        "answer": turn.answer,
        "steps": [step_data(step) for step in turn.steps],
        "usage": {
            "input": usage.input_tokens,
            "cached": usage.cached_tokens,
            "output": usage.output_tokens,
            "reasoning": usage.reasoning_tokens,
        },
        "cost": turn.cost,
        "calls": turn.calls,
        "model": turn.model,
        "stopped": turn.stopped,
    }


def _text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else ""


def _number(data: Mapping[str, Any], key: str) -> float:
    value = data.get(key)
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def step_from(data: Mapping[str, Any]) -> Step:
    return Step(
        kind=_text(data, "kind") or "tool",
        title=_text(data, "title"),
        detail=_text(data, "detail"),
        seconds=_number(data, "seconds"),
        ok=data.get("ok") is not False,
    )


def turn_from(data: Mapping[str, Any]) -> Turn:
    raw_usage = data.get("usage")
    usage_data: Mapping[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    usage = Usage(
        input_tokens=int(_number(usage_data, "input")),
        cached_tokens=int(_number(usage_data, "cached")),
        output_tokens=int(_number(usage_data, "output")),
        reasoning_tokens=int(_number(usage_data, "reasoning")),
    )
    raw_steps = data.get("steps")
    items = raw_steps if isinstance(raw_steps, list) else []
    steps = [step_from(item) for item in items if isinstance(item, dict)]
    return Turn(
        question=_text(data, "question"),
        answer=_text(data, "answer"),
        steps=tuple(steps),
        usage=usage,
        cost=_number(data, "cost"),
        calls=int(_number(data, "calls")),
        model=_text(data, "model"),
        stopped=_text(data, "stopped"),
    )


def chat_data(chat: Chat) -> dict[str, Any]:
    return {
        "version": VERSION,
        "id": chat.chat_id,
        "title": chat.title,
        "created": chat.created,
        "updated": chat.updated,
        "pinned": chat.pinned,
        "turns": [turn_data(turn) for turn in chat.turns],
    }


def chat_from(data: Mapping[str, Any]) -> Chat | None:
    chat_id = _text(data, "id")
    if not chat_id:
        return None
    raw_turns = data.get("turns")
    items = raw_turns if isinstance(raw_turns, list) else []
    turns = [turn_from(item) for item in items if isinstance(item, dict)]
    return Chat(
        chat_id=chat_id,
        title=_text(data, "title") or "New chat",
        created=_number(data, "created"),
        updated=_number(data, "updated"),
        pinned=data.get("pinned") is True,
        turns=tuple(turns),
    )


def date_group(chat: Chat, now: float) -> str:
    """The rail's group of a chat: Pinned, Today, Yesterday, This week or Older."""
    if chat.pinned:
        return "Pinned"
    today = datetime.fromtimestamp(now).date()
    day = datetime.fromtimestamp(chat.updated).date()
    age = (today - day).days
    if age <= 0:
        return "Today"
    if age == 1:
        return "Yesterday"
    if age < 7:
        return "This week"
    return "Older"


def ordered(chats: Sequence[Chat]) -> list[Chat]:
    """Pinned first, then the newest change first."""
    return sorted(chats, key=lambda chat: (not chat.pinned, -chat.updated))


def grouped(chats: Sequence[Chat], now: float) -> list[tuple[str, list[Chat]]]:
    found: dict[str, list[Chat]] = {}
    for chat in ordered(chats):
        found.setdefault(date_group(chat, now), []).append(chat)
    return [(name, found[name]) for name in GROUPS if name in found]


class ChatStore:
    """The chats in one folder; a broken file is skipped, never fatal."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def path(self, chat_id: str) -> Path:
        safe = "".join(char for char in chat_id if char.isalnum() or char in "_-")
        return self.folder / f"{safe or 'chat'}.json"

    def save(self, chat: Chat) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.path(chat.chat_id)
        temporary = path.with_suffix(".tmp")
        text = json.dumps(chat_data(chat), ensure_ascii=False, indent=1)
        temporary.write_bytes(text.encode("utf-8"))
        os.replace(temporary, path)
        return path

    def load(self, chat_id: str) -> Chat | None:
        return self._read(self.path(chat_id))

    def chats(self) -> list[Chat]:
        if not self.folder.is_dir():
            return []
        found = [self._read(path) for path in self.folder.glob("*.json")]
        return ordered([chat for chat in found if chat is not None])

    def delete(self, chat_id: str) -> bool:
        path = self.path(chat_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def set_pinned(self, chat_id: str, pinned: bool) -> Chat | None:
        chat = self.load(chat_id)
        if chat is None:
            return None
        changed = replace(chat, pinned=pinned)
        self.save(changed)
        return changed

    def rename(self, chat_id: str, title: str) -> Chat | None:
        chat = self.load(chat_id)
        clean = " ".join(title.split())
        if chat is None or not clean:
            return None
        changed = replace(chat, title=clean[: TITLE_CHARS * 2])
        self.save(changed)
        return changed

    def _read(self, path: Path) -> Chat | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return chat_from(data) if isinstance(data, dict) else None
