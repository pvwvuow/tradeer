"""Saved prompts of the AI Lab (docs/NOCURVE_V2.md 20e3, the inspector's Prompts panel).

One JSON file in the data folder (`ai_prompts.json`). Until the user saves one, the panel
offers the design's starting prompts; each is only text: a click puts it in the composer and
nothing is sent until the user presses Send. Titles and texts are cut to a safe length, and
a broken file is ignored, never fatal.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROMPT_FILE = "ai_prompts.json"
TEXT_CHARS = 4000
TITLE_CHARS = 80
NOTE_CHARS = 120
MAX_PROMPTS = 50
VERSION = 1


@dataclass(frozen=True)
class Prompt:
    prompt_id: str
    title: str
    text: str
    note: str = ""
    created: float = 0.0
    updated: float = 0.0
    builtin: bool = False


# The design's starting prompts (English, Persian title, Persian note).
STARTERS: tuple[tuple[str, str, str], ...] = (
    (
        "Build the report for the AI",
        "گزارش را برای AI بساز",
        "خروجی ۹۰ روزه‌ی استراتژی فعال",
    ),
    ("Compare the suggestion", "پیشنهاد را مقایسه کن", "دو بک‌تست کامل با حکم"),
    ("EUR/USD candle chart", "نمودار کندلی EUR/USD", "سیگنال‌ها روی قیمت"),
    ("Parameter sensitivity map", "نقشه‌ی حساسیت پارامتر", "نقشه‌ی دو پارامتر"),
    ("Run the Monte Carlo", "مونت‌کارلو را اجرا کن", "۴۰ مسیر تصادفی"),
    ("Why was the suggestion refused?", "چرا پیشنهاد رد شد؟", "دلیل‌های ممکن"),
)
STARTER_NOTES_EN = (
    "A 90-day export of the active strategy",
    "Two full backtests with a verdict",
    "The signals on the price",
    "A map of two parameters",
    "40 random paths",
    "The possible reasons",
)


def clean(text: str, limit: int) -> str:
    """One line for titles and notes; the text keeps its lines. Cut at `limit`."""
    return text.strip()[:limit]


def one_line(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


def starters(fa: bool) -> list[Prompt]:
    found: list[Prompt] = []
    for index, (english, persian, note) in enumerate(STARTERS):
        title = persian if fa else english
        found.append(
            Prompt(
                prompt_id=f"starter-{index}",
                title=title,
                text=title,
                note=note if fa else STARTER_NOTES_EN[index],
                builtin=True,
            ),
        )
    return found


def prompt_data(prompt: Prompt) -> dict[str, Any]:
    return {
        "id": prompt.prompt_id,
        "title": prompt.title,
        "text": prompt.text,
        "note": prompt.note,
        "created": prompt.created,
        "updated": prompt.updated,
    }


def _text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else ""


def _number(data: Mapping[str, Any], key: str) -> float:
    value = data.get(key)
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def prompt_from(data: Mapping[str, Any]) -> Prompt | None:
    prompt_id = _text(data, "id")
    text = clean(_text(data, "text"), TEXT_CHARS)
    if not prompt_id or not text:
        return None
    return Prompt(
        prompt_id=prompt_id,
        title=one_line(_text(data, "title"), TITLE_CHARS) or one_line(text, TITLE_CHARS),
        text=text,
        note=one_line(_text(data, "note"), NOTE_CHARS),
        created=_number(data, "created"),
        updated=_number(data, "updated"),
    )


class PromptStore:
    """The saved prompts in one file; the starters while none is saved."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    @property
    def path(self) -> Path:
        return self.folder / PROMPT_FILE

    def saved(self) -> list[Prompt]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("prompts") if isinstance(data, dict) else None
        found = [prompt_from(item) for item in items or [] if isinstance(item, dict)]
        return [prompt for prompt in found if prompt is not None][:MAX_PROMPTS]

    def prompts(self, fa: bool = False) -> list[Prompt]:
        """The newest saved prompt first, then the design's starters."""
        saved = sorted(self.saved(), key=lambda prompt: -prompt.updated)
        return [*saved, *starters(fa)]

    def save(self, text: str, now: float, title: str = "", note: str = "") -> Prompt | None:
        """Save the composer's text as a prompt; None when it is empty."""
        body = clean(text, TEXT_CHARS)
        if not body:
            return None
        prompt = Prompt(
            prompt_id=f"p{int(now)}_{secrets.token_hex(3)}",
            title=one_line(title, TITLE_CHARS) or one_line(body, TITLE_CHARS),
            text=body,
            note=one_line(note, NOTE_CHARS),
            created=now,
            updated=now,
        )
        kept = [item for item in self.saved() if item.text != body]
        self._write([prompt, *kept][:MAX_PROMPTS])
        return prompt

    def delete(self, prompt_id: str) -> bool:
        saved = self.saved()
        kept = [item for item in saved if item.prompt_id != prompt_id]
        if len(kept) == len(saved):
            return False
        self._write(kept)
        return True

    def _write(self, prompts: Sequence[Prompt]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        data = {"version": VERSION, "prompts": [prompt_data(prompt) for prompt in prompts]}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))
        os.replace(temporary, self.path)
