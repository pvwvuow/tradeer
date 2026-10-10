"""Half signals of a channel (docs/AI_LAB_V3.md section 1, 0.44.0): a teaser or a part
waits in the channel's draft, later pieces, replies and edits join it, and only a whole
signal leaves it; an incomplete one expires with what was missing."""

from __future__ import annotations

from app.channels.drafts import (
    WAITING,
    Draft,
    Drafts,
    Piece,
    edit,
    expired_reason,
    join,
    piece,
    worth_telling,
)

GOLD_LINE = "XAUUSD buy now"
REST = "SL 4180 TP 4205 TP 4220"


def test_what_one_message_is() -> None:
    assert piece("XAUUSD buy 4190 SL 4180 TP 4205")[0] is Piece.COMPLETE
    assert piece(GOLD_LINE)[0] is Piece.PART
    assert piece("آماده باشید، سیگنال طلا داریم")[0] is Piece.TEASER
    assert piece("Get ready, gold signal soon")[0] is Piece.TEASER
    assert piece("صبر کنید تکمیلش می‌کنم")[0] is Piece.TEASER
    assert piece(REST)[0] is Piece.FILLER
    assert piece("Join our VIP group today")[0] is Piece.OTHER
    assert piece("+120 pips TP1 hit")[0] is Piece.OTHER


def test_a_teaser_a_part_and_the_rest_make_one_signal() -> None:
    drafts = Drafts()
    opened = join(drafts, 1, 10, "آماده باشید سیگنال طلا داریم", 0.0)
    assert opened.action == "open" and len(drafts) == 1
    waiting = join(drafts, 1, 11, GOLD_LINE, 60.0)
    assert waiting.action == "wait" and waiting.missing == ("stop loss", "target")
    assert waiting.reason() == f"{WAITING} (no stop loss, target yet)"
    done = join(drafts, 1, 12, REST, 120.0)
    assert done.action == "complete" and done.draft is not None
    assert done.draft.message_ids == (10, 11, 12) and done.text.endswith(REST)
    assert len(drafts) == 0
    assert join(drafts, 1, 13, "XAUUSD buy 4190 SL 4180 TP 4205", 130.0).action == "pass"


def test_a_reply_or_an_edit_completes_a_part() -> None:
    drafts = Drafts()
    join(drafts, 1, 20, GOLD_LINE, 0.0)
    assert join(drafts, 1, 21, "سود بگیرید", 30.0, reply_to=99).action == "pass"
    replied = join(drafts, 1, 22, "4180 / 4205", 60.0, reply_to=20)
    assert replied.action == "wait"  # joined, still without labels
    edited = edit(drafts, 1, 20, f"{GOLD_LINE} {REST}", 90.0)
    assert edited is not None and edited.action == "complete"
    assert edit(drafts, 1, 20, "anything", 95.0) is None  # no draft any more


def test_a_message_that_fills_a_gap_joins_and_a_new_symbol_starts_again() -> None:
    drafts = Drafts()
    join(drafts, 1, 1, "Gold soon", 0.0)
    filled = join(drafts, 1, 2, "buy now", 30.0)
    assert filled.action == "wait" and "side" not in filled.missing
    other = join(drafts, 1, 3, "EURUSD sell now", 40.0)
    assert other.action == "open" and other.draft is not None
    assert other.draft.message_ids == (3,)


def test_drafts_expire_with_what_was_missing() -> None:
    drafts = Drafts(minutes=15)
    join(drafts, 1, 1, GOLD_LINE, 0.0)
    join(drafts, 2, 1, "VIP signals soon", 0.0)
    assert drafts.get(1, 15 * 60) is not None and drafts.get(1, 15 * 60 + 1) is None
    gone = drafts.expired(15 * 60 + 1)
    assert sorted(draft.channel_id for draft in gone) == [1, 2] and len(drafts) == 0
    gold = next(draft for draft in gone if draft.channel_id == 1)
    reason = "the signal stayed incomplete (no stop loss, target), nothing done"
    assert expired_reason(gold) == reason
    advert = next(draft for draft in gone if draft.channel_id == 2)
    assert worth_telling(gold) and not worth_telling(advert)
    assert worth_telling(Draft(3, 0.0, (1, 2), ("soon", "really soon")))
