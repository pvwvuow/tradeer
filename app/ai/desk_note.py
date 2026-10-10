"""The AI's note on a pasted signal (docs/SIGNAL_DESK.md 2.5, phase 21b3).

Three short sentences written only from the lines the card already shows: the plan, the
context now and the full check. The note never adds a number, never forecasts and never
tells you to buy or sell; the AI never sends an order either (only the hold does). The
model gets the facts and nothing else: no account number, no balance, no history.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.ai.transport import Message

NOTE_TOKENS = 300
NOTE_CHARACTERS = 600
SENTENCES = 3
FACT_LINES = 40
SENTENCE_END = re.compile(r"(?<=[.!?\u061f])\s+")

SYSTEM = (
    "You are the note writer of a trading desk. A user pasted a trade signal and the app "
    "checked it. Write exactly three short sentences about it, using only the facts you are "
    "given: what supports it, what speaks against it, and the biggest risk in the facts. Do "
    "not add numbers that are not in the facts, do not forecast the price, do not tell the "
    "user to buy, sell or skip, and do not say that you placed an order: you cannot. Plain "
    "text, no lists, no markdown."
)


def note_messages(
    signal: str,
    facts: Sequence[str],
    *,
    persian: bool = False,
) -> list[Message]:
    """The two messages of one note request."""
    language = "Answer in Persian." if persian else "Answer in English."
    lines = [line.strip() for line in facts if line.strip()][:FACT_LINES]
    user = "\n".join(["The pasted signal:", signal.strip(), "", "The facts:", *lines])
    return [
        {"role": "system", "content": f"{SYSTEM} {language}"},
        {"role": "user", "content": user},
    ]


def clean_note(text: str) -> str:
    """At most three sentences on one line, without markdown, cut to a sane length."""
    flat = " ".join(text.replace("*", "").replace("#", "").split())
    sentences = [part for part in SENTENCE_END.split(flat) if part]
    note = " ".join(sentences[:SENTENCES])
    if len(note) > NOTE_CHARACTERS:
        note = note[: NOTE_CHARACTERS - 1].rstrip() + "\u2026"
    return note
