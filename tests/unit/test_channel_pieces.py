"""A channel that names the pair first and gives the rest later (0.48.1, phase 22h): a short
message with only the symbol opens a draft, the pieces join, and what the parser cannot read
(two prices, one with its label) goes to the AI with the channel's earlier messages."""

from __future__ import annotations

from app.channels.drafts import Drafts, Piece, join, piece
from app.channels.reading import DAILY_READS, SYSTEM, unclear


def test_a_short_message_that_only_names_the_pair_is_a_part() -> None:
    assert piece("EURUSD")[0] is Piece.PART
    assert piece("XAUUSD")[0] is Piece.PART
    assert piece("XAUUSD TP1 hit")[0] is not Piece.PART  # news of an earlier trade
    assert piece("XAUUSD +120 pips today")[0] is not Piece.PART  # the channel's own claim
    long = piece("XAUUSD analysis for the week: a long and quiet range is expected")[0]
    assert long is Piece.OTHER
    assert piece("Join our VIP group today")[0] is Piece.OTHER


def test_the_pair_then_the_side_then_the_prices_make_one_signal() -> None:
    drafts = Drafts()
    opened = join(drafts, 1, 1, "EURUSD", 0.0)
    assert opened.action == "open" and len(drafts) == 1
    waiting = join(drafts, 1, 2, "buy limit 1.0850", 20.0)
    assert waiting.action == "wait" and "side" not in waiting.missing
    done = join(drafts, 1, 3, "SL 1.0800 TP 1.0900", 40.0)
    assert done.action == "complete" and done.draft is not None
    assert done.draft.message_ids == (1, 2, 3) and len(drafts) == 0


def test_two_prices_with_a_label_or_a_name_go_to_the_ai() -> None:
    assert unclear("SL 4180 TP 4205")  # prices with their labels, nothing else
    assert unclear("gold buy 4190 4200")  # a name and two prices, no whole signal
    assert not unclear("TP1 hit")  # one number only
    assert not unclear("Join our VIP group, 50% off until 20:00")


def test_the_ai_is_told_to_put_the_pieces_together() -> None:
    assert "in pieces" in SYSTEM and "Put the earlier messages and this one together" in SYSTEM
    assert DAILY_READS == 100
