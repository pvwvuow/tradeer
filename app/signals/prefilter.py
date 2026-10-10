"""Is this message a signal, an update of one, or neither? (docs/SIGNAL_DESK.md 4.1)

Free and local, so most channel messages (ads, chatter, charts) never cost an AI call, and
the AI Lab composer can tell a pasted signal from a question. A signal names a symbol and a
side and has at least two prices; an update ("TP1 hit", "move SL to entry", "close now",
✅) has an update word or mark and a symbol, or is a reply to an earlier message.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum

from app.signals.words import normalise, resolve_symbol, tokens

UPDATE_WORDS = re.compile(
    r"\b(hit|hits|closed?|cancel(?:l?ed)?|breakeven|(?:to|at)\s+be|risk\s*free|move\s+sl|secure|"
    r"booked?|running)\b"
    r"|(?<!\w)(خورد|بسته|ببندید|ببندین|کنسل|لغو|ریسک\s*فری|سیو|سیو\s*سود|فعال\s*شد)(?!\w)",
)
UPDATE_MARKS = ("✅", "❌", "🎯", "💰", "🛑")
SIDES = frozenset({"buy", "sell"})


class Kind(StrEnum):
    SIGNAL = "signal"
    UPDATE = "update"
    NOISE = "noise"


def classify(
    text: str,
    symbols: tuple[str, ...] = (),
    aliases: Mapping[str, str] | None = None,
    *,
    reply: bool = False,
) -> Kind:
    """The kind of a message; see the module docstring."""
    clean = normalise(text)
    items = tokens(clean)
    words = [item for item in items if item.number is None]
    symbol = any(not item.label and resolve_symbol(item.text, symbols, aliases) for item in words)
    side = any(item.label in SIDES for item in words)
    prices = sum(1 for item in items if item.number is not None)
    if symbol and side and prices >= 2:
        return Kind.SIGNAL
    marked = any(mark in text for mark in UPDATE_MARKS) or UPDATE_WORDS.search(clean) is not None
    if marked and (symbol or reply):
        return Kind.UPDATE
    return Kind.NOISE


def looks_like_signal(
    text: str,
    symbols: tuple[str, ...] = (),
    aliases: Mapping[str, str] | None = None,
) -> bool:
    return classify(text, symbols, aliases) is Kind.SIGNAL
