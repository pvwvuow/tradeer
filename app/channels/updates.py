"""Follow-ups of an earlier channel signal (docs/SIGNAL_DESK.md 3.3 and 3.6, phase 21e).
Pure: the text of an update message in, what it asks for out.

"TP1 hit", "move SL to entry", "close now", "cancel the order", "new SL 2340": an update is
linked to its signal by the reply, else by the same symbol (and side, when it says one)
within 24 hours. A follow-up of a signal you took becomes a small card that needs one hold;
one of a signal you did not take only changes its shadow result. Text is data: an update
never sends anything by itself.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain.signals import Direction
from app.signals.words import normalise, resolve_symbol, tokens

LINK_SECONDS = 24 * 3600

CANCEL = re.compile(r"\bcancel(?:l?ed)?\b|\bdelete\b|(?<!\w)(کنسل|لغو|حذف)(?!\w)")
CLOSE = re.compile(r"\bclose(?:d)?\b|\bexit\b|(?<!\w)(ببندید|ببندین|بسته|خارج)(?!\w)")
BREAK_EVEN = re.compile(
    r"\b(?:breakeven|break\s*even|risk\s*free|(?:sl|stop)\s+(?:to|at)\s+(?:entry|be))\b"
    r"|\b(?:to|at)\s+be\b|(?<!\w)(ریسک\s*فری|سیو|نقطه\s*ورود)(?!\w)",
)
SL_HIT = re.compile(r"\b(?:sl|stop(?:ped)?)\s*(?:hit|out)\b|❌|🛑|(?<!\w)استاپ\s*خورد(?!\w)")
TP_HIT = re.compile(
    r"\b(?:tp\s*\d?|target\s*\d?)\s*(?:hit|done|reached)\b|✅|🎯|💰|(?<!\w)خورد(?!\w)",
)
NEW_WORDS = re.compile(r"\b(?:new|move|change|update)\b|(?<!\w)(جدید|ببرید|تغییر|جابجا)(?!\w)")


class UpdateKind(StrEnum):
    TP_HIT = "tp_hit"
    SL_HIT = "sl_hit"
    BREAK_EVEN = "sl_to_entry"
    CLOSE = "close"
    CANCEL = "cancel"
    NEW_SL = "new_sl"
    NEW_TP = "new_tp"


@dataclass(frozen=True)
class Update:
    kind: UpdateKind
    symbol: str = ""
    direction: Direction | None = None
    price: float | None = None  # the new SL or TP

    @property
    def acts(self) -> bool:
        """It asks for a change (a card), not only news about the result."""
        return self.kind not in (UpdateKind.TP_HIT, UpdateKind.SL_HIT)


def read_update(
    text: str,
    symbols: tuple[str, ...] = (),
    aliases: Mapping[str, str] | None = None,
) -> Update | None:
    clean = normalise(text)
    items = tokens(clean)
    symbol = ""
    direction: Direction | None = None
    for item in items:
        if item.number is None and not item.label and not symbol:
            symbol = resolve_symbol(item.text, symbols, aliases)
        if item.label == "buy":
            direction = Direction.LONG
        elif item.label == "sell":
            direction = Direction.SHORT
    if CANCEL.search(clean):
        return Update(UpdateKind.CANCEL, symbol, direction)
    if BREAK_EVEN.search(clean):
        return Update(UpdateKind.BREAK_EVEN, symbol, direction)
    if CLOSE.search(clean):
        return Update(UpdateKind.CLOSE, symbol, direction)
    if SL_HIT.search(text) or SL_HIT.search(clean):
        return Update(UpdateKind.SL_HIT, symbol, direction)
    changed = NEW_WORDS.search(clean) is not None
    for index, item in enumerate(items):
        if item.label not in ("sl", "tp") or not changed:
            continue
        price = next((later.number for later in items[index + 1 :] if later.number), None)
        if price is not None:
            kind = UpdateKind.NEW_SL if item.label == "sl" else UpdateKind.NEW_TP
            return Update(kind, symbol, direction, price)
    if TP_HIT.search(text) or TP_HIT.search(clean):
        return Update(UpdateKind.TP_HIT, symbol, direction)
    return None


@dataclass(frozen=True)
class Earlier:
    """A signal of the same channel the update may belong to."""

    message_id: int
    date: float
    symbol: str
    direction: Direction | None


def link(
    update: Update,
    reply_to: int | None,
    date: float,
    earlier: Sequence[Earlier],
) -> Earlier | None:
    """The signal an update belongs to: the one it replies to, else the newest one with the
    same symbol (and side, when the update says one) in the last 24 hours."""
    if reply_to is not None:
        return next((signal for signal in earlier if signal.message_id == reply_to), None)
    if not update.symbol:
        return None
    found = [
        signal
        for signal in earlier
        if signal.symbol.upper() == update.symbol.upper()
        and 0 <= date - signal.date <= LINK_SECONDS
        and (update.direction is None or signal.direction is update.direction)
    ]
    return max(found, key=lambda signal: signal.date, default=None)
