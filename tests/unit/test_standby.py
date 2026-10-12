"""Standby (phase 22j, 0.50.0): a hint from a channel's admin puts the channel on standby
for a while, a glance at a short message is cheap and limited, and a standby reading tells
the AI that the message may be a piece of a signal."""

from __future__ import annotations

from app.channels.reading import SYSTEM
from app.channels.standby import (
    GLANCE_CONTEXT,
    GLANCE_SCHEMA,
    GlanceCap,
    StandbyBook,
    glance_messages,
    parse_glance,
    standby_messages,
    worth_glance,
)


def test_a_standby_lasts_for_its_time_and_is_never_longer_than_the_longest() -> None:
    book = StandbyBook(minutes=10, longest=30, reads=12)
    assert book.get(1, 0) is None
    first = book.open(1, 0, "XAUUSD", "he named gold")
    assert first.until == 600 and book.get(1, 599) is not None and book.get(1, 600) is None
    again = book.open(1, 500)  # another hint keeps it going and keeps what it knew
    assert (again.since, again.until, again.hint) == (0, 1100, "XAUUSD")
    assert again.reason == "he named gold"
    assert book.open(1, 1000).until == 1600
    assert book.open(1, 1500).until == 1800  # 30 minutes from the first hint, no more
    assert book.get(1, 1799) is not None and book.get(1, 1800) is None


def test_a_standby_ends_after_its_readings_and_is_taken_out_once() -> None:
    book = StandbyBook(minutes=10, longest=30, reads=3)
    book.open(7, 0)
    for _ in range(3):
        book.count_read(7, 10)
    assert book.get(7, 10) is None  # the readings are used before the time is
    assert [stood.channel_id for stood in book.expired(10)] == [7] and not book.expired(10)
    assert len(book) == 0
    book.open(8, 0)
    assert book.close(8) is not None and book.close(8) is None


def test_glances_are_limited_per_channel_and_per_day() -> None:
    cap = GlanceCap(per_hour=2, daily=4)
    assert cap.take(1, 0) and cap.take(1, 10) and not cap.take(1, 20)
    assert cap.take(2, 30) and cap.take(2, 40) and not cap.take(2, 50)  # daily: 4 used
    assert not cap.take(1, 4000)  # an hour later, but the day's glances are used
    assert cap.take(1, 86_400 + 5)  # a new day


def test_only_a_short_message_with_words_is_worth_a_glance() -> None:
    assert worth_glance("the dollar is moving")
    assert worth_glance("\u062f\u0644\u0627\u0631")
    assert not worth_glance("+120 pips today")  # the channel's own result claim
    assert not worth_glance("see https://t.me/somewhere")
    assert not worth_glance("12345 678")
    assert not worth_glance("   ")
    assert not worth_glance("word " * 40)


def test_a_glance_answer_is_read_or_refused() -> None:
    found = parse_glance('Sure: {"standby": true, "symbol": "xauusd", "why": "he  names gold"}')
    assert found is not None and found.standby
    assert (found.symbol, found.why) == ("XAUUSD", "he names gold")
    no = parse_glance('{"standby": false, "symbol": "", "why": ""}')
    assert no is not None and not no.standby
    assert parse_glance('{"standby": "yes"}') is None and parse_glance("no json") is None
    assert set(GLANCE_SCHEMA["required"]) == {"standby", "symbol", "why"}


def test_the_messages_of_a_glance_and_of_a_standby_reading() -> None:
    earlier = [f"message {number}" for number in range(6)]
    glance = glance_messages("get ready", earlier, ("EURUSD", "XAUUSD"))
    user = str(glance[1]["content"])
    assert user.count("<<<") == GLANCE_CONTEXT + 1  # the last three, then this one
    assert "message 5" in user and "message 2" not in user and "get ready" in user
    reading = standby_messages("sl 4180", ["gold buy now"], ("XAUUSD",), "XAUUSD")
    system = str(reading[0]["content"])
    assert system.startswith(SYSTEM) and "on standby" in system and "he named XAUUSD" in system
    assert "gold buy now" in str(reading[1]["content"])
    assert "he named" not in str(standby_messages("x", [], (), "")[0]["content"])
