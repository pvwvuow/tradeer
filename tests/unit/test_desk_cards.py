"""Order cards after docs/AI_AUTOPILOT.md 23a (0.50.2): one failed leg does not hide an open
one, a refused card says why and what to do, ids never repeat, AI market readings stay
market orders."""

from __future__ import annotations

from dataclasses import replace

from app.channels.reading import ReadResult, canonical
from app.domain.signals import OrderType, SignalRecord, SignalState
from app.engine.desk_cards import desk_state, desk_words, fix_hint, leg_reasons, refusal
from app.engine.signal_desk import DeskResult, new_request
from app.signals.parse import parse
from tests.unit.signal_helpers import MORNING, make_record

STEPS = {
    SignalState.PENDING_APPROVAL: (SignalState.PENDING_APPROVAL,),
    SignalState.APPROVED: (SignalState.PENDING_APPROVAL, SignalState.APPROVED),
    SignalState.SENT: (SignalState.PENDING_APPROVAL, SignalState.APPROVED, SignalState.SENT),
    SignalState.FILLED: (
        SignalState.PENDING_APPROVAL,
        SignalState.APPROVED,
        SignalState.SENT,
        SignalState.FILLED,
    ),
    SignalState.FAILED: (
        SignalState.PENDING_APPROVAL,
        SignalState.APPROVED,
        SignalState.SENT,
        SignalState.FAILED,
    ),
    SignalState.RISK_REJECTED: (SignalState.RISK_REJECTED,),
    SignalState.FILTERED_OUT: (SignalState.FILTERED_OUT,),
}
WORD = desk_words(False)


def leg(name: str, state: SignalState, reason: str = "test") -> SignalRecord:
    record = make_record(id=name)
    signal = record.signal
    for step in STEPS[state]:
        signal = signal.with_state(step, float(MORNING), reason)
    rejected = state in (SignalState.RISK_REJECTED, SignalState.FILTERED_OUT)
    return replace(record, signal=signal, reject_reason=reason if rejected else "")


def result(ok: bool, message: str, ids: tuple[str, ...] = ()) -> DeskResult:
    return DeskResult("r1", ok, message, signal_ids=ids)


def test_one_failed_leg_does_not_hide_an_open_one() -> None:
    legs = [leg("a", SignalState.FAILED), leg("b", SignalState.FILLED)]
    assert desk_state(result(True, "2 orders wait"), legs, sent=True) == "filled"
    legs = [leg("a", SignalState.FAILED), leg("b", SignalState.SENT)]
    assert desk_state(result(True, "2 orders wait"), legs, sent=True) == "sent"
    alone = [leg("a", SignalState.FAILED)]
    assert desk_state(result(True, "order waits"), alone, sent=True) == "failed"


def test_waiting_sending_and_expired_by_the_time() -> None:
    waiting = [leg("a", SignalState.PENDING_APPROVAL)]
    found = result(True, "order waits for your confirmation")
    assert desk_state(found, waiting) == "waiting"
    assert desk_state(found, waiting, sent=True) == "sending"
    late = waiting[0].signal.expires_at + 1
    assert desk_state(found, waiting, now=late) == "expired"
    assert desk_state(found, waiting, now=late - 60) == "waiting"
    assert desk_state(None, []) == "checking"


def test_a_card_refused_after_it_was_shown_says_why() -> None:
    first = result(True, "order waits for your confirmation", ("a",))
    reason = "the budget too small: the smallest lot risks 5.25 USD, 2.00 allowed"
    legs = [leg("a", SignalState.RISK_REJECTED, reason)]
    assert desk_state(first, legs, sent=True) == "refused"
    why = refusal(first, legs, WORD)
    assert why == reason
    assert "waits" not in why
    assert "bigger budget" in fix_hint(why)
    assert leg_reasons(legs) == [reason]


def test_a_desk_refusal_keeps_its_message_and_an_old_one_is_not_repeated() -> None:
    refused = result(False, "price has not moved: 3.20 from the signal")
    assert refusal(refused, [], WORD) == refused.message
    assert "moved away" in fix_hint(refused.message)
    stale = result(True, "order waits for your confirmation")
    assert "waits" not in refusal(stale, [], WORD)
    assert fix_hint("something new") == ""


def test_fixes_in_persian() -> None:
    word = desk_words(True)
    hint = fix_hint("one bet per currency and direction")
    assert word(hint) != hint
    assert word("unknown words") == "unknown words"


def test_two_identical_signals_get_two_ids() -> None:
    parsed = parse("XAUUSD buy 4175 sl 4170 tp 4180")
    one = new_request(parsed, 1000.0, "channel:26071001")
    two = new_request(parsed, 1000.0, "channel:26071001")
    assert one.id != two.id


def test_an_ai_market_reading_with_one_price_stays_a_market_order() -> None:
    texts = ["Gold buy now 4175, stop 4170, targets 4180 and 4185"]
    market = ReadResult("signal", "XAUUSD", "buy", "market", (4175.0,), 4170.0, (4180.0, 4185.0))
    text, why = canonical(market, texts)
    assert why == "" and text == "XAUUSD buy now 4175 sl 4170 tp 4180 tp 4185"
    assert parse(text).order is OrderType.MARKET
    zone = ReadResult("signal", "XAUUSD", "buy", "market", (4170.0, 4175.0), 4165.0, (4180.0,))
    texts = ["Gold buy 4170-4175, stop 4165, target 4180"]
    assert canonical(zone, texts)[0] == "XAUUSD buy 4170 4175 sl 4165 tp 4180"
