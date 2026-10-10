"""Half signals of a channel (docs/AI_LAB_V3.md section 1, 0.44.0). Pure: no I/O, no clock.

Channels often post a signal in pieces: "get ready, gold soon" (a teaser), "XAUUSD buy
now" (a part: no stop loss yet), then "SL 4180 TP 4205" a minute later, as a reply, or by
editing the first message. One `Draft` per channel collects the pieces for a while (15
minutes by default); every new piece is joined to it and the joined text is parsed again.
Only a complete signal leaves the draft; an incomplete one never makes an order card, and
when the time is up it expires with what was still missing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

from app.signals.parse import ParsedSignal, parse
from app.signals.words import normalise, tokens

DRAFT_MINUTES = 15.0
WAITING = "waiting for the rest of the signal"
TEASER = re.compile(
    r"\b(get\s+ready|be\s+ready|stay\s+tuned|soon|coming\s+up|coming|wait|waiting|loading|"
    r"next\s+signal|details?\s+(?:soon|later|below)|(?:sl|tp)\s+(?:soon|later))\b"
    r"|(?<!\w)(آماده\s*باشید|آماده\s*باشین|آماده\s*باش|صبر\s*کنید|صبر\s*کنین|منتظر\s*باشید|"
    r"منتظر\s*باشین|به\s*زودی|بزودی|تکمیل|سیگنال\s*بعدی|لحظاتی\s*دیگر|دقایقی\s*دیگر|"
    r"در\s*حال\s*آماده|بعدا\s*میگم|بعدا\s*می\s*گم|اعلام\s*میشه|اعلام\s*می\s*شود)(?!\w)",
    re.IGNORECASE,
)
FIELDS = frozenset({"sl", "tp", "entry"})
Aliases = Mapping[str, str] | None


class Piece(StrEnum):
    COMPLETE = "complete"  # a whole signal in one message
    PART = "part"  # a symbol and a side, something else missing
    TEASER = "teaser"  # "get ready", "wait, I will complete it"
    FILLER = "filler"  # prices with their labels, no side: the rest of a draft
    OTHER = "other"


def piece(
    text: str,
    symbols: tuple[str, ...] = (),
    aliases: Mapping[str, str] | None = None,
) -> tuple[Piece, ParsedSignal]:
    """What one message (or a joined text) is, with what the parser found in it."""
    parsed = parse(text, symbols, aliases)
    if parsed.complete:
        return Piece.COMPLETE, parsed
    if parsed.symbol and parsed.direction is not None:
        return Piece.PART, parsed
    if TEASER.search(text) or TEASER.search(normalise(text)):
        return Piece.TEASER, parsed
    items = tokens(normalise(text))
    labelled = any(item.label in FIELDS for item in items)
    if labelled and any(item.number is not None for item in items):
        return Piece.FILLER, parsed
    return Piece.OTHER, parsed


@dataclass(frozen=True)
class Draft:
    channel_id: int
    started: float
    message_ids: tuple[int, ...]
    texts: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.texts)

    def with_part(self, message_id: int, text: str) -> Draft:
        return replace(
            self,
            message_ids=(*self.message_ids, message_id),
            texts=(*self.texts, text),
        )

    def with_edit(self, message_id: int, text: str) -> Draft:
        texts = tuple(
            text if found == message_id else old
            for found, old in zip(self.message_ids, self.texts, strict=True)
        )
        return replace(self, texts=texts)


@dataclass(frozen=True)
class Step:
    action: str  # "open", "wait", "complete" or "pass" (not part of a draft)
    text: str  # the text to handle: the joined draft when it is complete
    missing: tuple[str, ...] = ()
    draft: Draft | None = None

    def reason(self) -> str:
        """The line for the chat and the log while a draft waits."""
        what = ", ".join(self.missing) or "the details"
        return f"{WAITING} (no {what} yet)"


class Drafts:
    """The open draft of each channel (one at a time per channel)."""

    def __init__(self, minutes: float = DRAFT_MINUTES) -> None:
        self.minutes = minutes
        self._open: dict[int, Draft] = {}

    def __len__(self) -> int:
        return len(self._open)

    def get(self, channel_id: int, now: float) -> Draft | None:
        draft = self._open.get(channel_id)
        if draft is None or now - draft.started > self.minutes * 60:
            return None
        return draft

    def put(self, draft: Draft) -> None:
        self._open[draft.channel_id] = draft

    def drop(self, channel_id: int) -> Draft | None:
        return self._open.pop(channel_id, None)

    def expired(self, now: float) -> list[Draft]:
        """The drafts whose time is up, taken out."""
        gone = [d for d in self._open.values() if now - d.started > self.minutes * 60]
        for draft in gone:
            self._open.pop(draft.channel_id, None)
        return gone

    def all(self) -> list[Draft]:
        return list(self._open.values())


def _same_symbol(
    draft: Draft,
    parsed: ParsedSignal,
    symbols: tuple[str, ...],
    aliases: Aliases,
) -> bool:
    before = parse(draft.text, symbols, aliases)
    return not before.symbol or not parsed.symbol or before.symbol == parsed.symbol


def _gains(old: Draft, new: Draft, symbols: tuple[str, ...], aliases: Aliases) -> bool:
    """The message fills something the draft missed ("Gold soon" then "buy now")."""
    before = parse(old.text, symbols, aliases).missing
    return len(parse(new.text, symbols, aliases).missing) < len(before)


def join(
    drafts: Drafts,
    channel_id: int,
    message_id: int,
    text: str,
    now: float,
    symbols: tuple[str, ...] = (),
    aliases: Mapping[str, str] | None = None,
    reply_to: int | None = None,
) -> Step:
    """One new message of a channel against its open draft."""
    current = drafts.get(channel_id, now)
    kind, parsed = piece(text, symbols, aliases)
    if current is not None:
        replying = reply_to is not None and reply_to in current.message_ids
        if kind is Piece.COMPLETE and not replying:
            drafts.drop(channel_id)  # a whole new signal: the old pieces are given up
            return Step("pass", text)
        new_part = kind is Piece.PART and not _same_symbol(current, parsed, symbols, aliases)
        if new_part:
            draft = Draft(channel_id, now, (message_id,), (text,))
            drafts.put(draft)
            return Step("open", text, parsed.missing, draft)
        grown = current.with_part(message_id, text)
        fits = kind in (Piece.PART, Piece.FILLER, Piece.TEASER)
        if replying or fits or _gains(current, grown, symbols, aliases):
            return _grow(drafts, grown, symbols, aliases)
        return Step("pass", text)
    if kind in (Piece.PART, Piece.TEASER):
        draft = Draft(channel_id, now, (message_id,), (text,))
        drafts.put(draft)
        return Step("open", text, parsed.missing, draft)
    return Step("pass", text)


def edit(
    drafts: Drafts,
    channel_id: int,
    message_id: int,
    text: str,
    now: float,
    symbols: tuple[str, ...] = (),
    aliases: Mapping[str, str] | None = None,
) -> Step | None:
    """An edited message: None when it is not part of the channel's open draft."""
    current = drafts.get(channel_id, now)
    if current is None or message_id not in current.message_ids:
        return None
    return _grow(drafts, current.with_edit(message_id, text), symbols, aliases)


def _grow(
    drafts: Drafts,
    draft: Draft,
    symbols: tuple[str, ...],
    aliases: Mapping[str, str] | None,
) -> Step:
    kind, parsed = piece(draft.text, symbols, aliases)
    if kind is Piece.COMPLETE:
        drafts.drop(draft.channel_id)
        return Step("complete", draft.text, (), draft)
    drafts.put(draft)
    return Step("wait", draft.text, parsed.missing, draft)


def expired_reason(draft: Draft, symbols: tuple[str, ...] = (), aliases: Aliases = None) -> str:
    missing = parse(draft.text, symbols, aliases).missing
    what = ", ".join(missing) or "the details"
    return f"the signal stayed incomplete (no {what}), nothing done"


def worth_telling(draft: Draft, symbols: tuple[str, ...] = (), aliases: Aliases = None) -> bool:
    """An expired draft is told in the chat when it named a symbol or had several pieces (a
    lone "soon" of an advert is not worth a line)."""
    return len(draft.texts) > 1 or bool(parse(draft.text, symbols, aliases).symbol)
