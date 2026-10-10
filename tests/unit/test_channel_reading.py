"""The AI reads a channel message the parser could not (docs/AI_LAB_V3.md section 1, 0.44.0):
which messages it gets, how its JSON answer is read and checked (a known symbol, the right
sides, only numbers the channel wrote), result claims and the daily cap."""

from __future__ import annotations

from app.channels.reading import (
    CLAIM_REASON,
    ReadCap,
    ReadResult,
    canonical,
    image_url,
    is_claim,
    parse_reply,
    read_messages,
    unclear,
)

IDEA = "Gold short from 4190, invalidation above 4200, targets 4170/4150"
SELL = ReadResult("signal", "GOLD", "sell", "", (4190.0,), 4200.0, (4170.0, 4150.0), "a sell")


def test_only_unclear_messages_go_to_the_ai() -> None:
    assert unclear(IDEA)
    assert not unclear("XAUUSD sell 4190 sl 4200 tp 4170")  # the parser reads it
    assert not unclear("EURUSD buy")  # too few prices: a draft waits for them
    assert not unclear("Join our VIP group, 50% off until 20:00")
    assert not unclear("+120 pips XAUUSD 4190 4200 4170")  # a result claim


def test_result_claims_are_never_signals() -> None:
    assert is_claim("+120 pips today 🔥")
    assert is_claim("سود امروز ۳۰۰ پیپ")
    assert is_claim("Results of the week: 850 pips")
    assert not is_claim("XAUUSD sell 4190 sl 4200 tp 4170")
    result = ReadResult("result", why="a profit claim")
    assert canonical(result, [IDEA]) == ("", CLAIM_REASON)


def test_the_ai_answer_is_read_from_json() -> None:
    text = (
        'Here: ```json {"kind": "signal", "symbol": "gold", "side": "SELL", "order": "", '
        '"entry": [4190], "sl": 4200, "tps": [4170, "4150"], "why": "a sell idea"}```'
    )
    found = parse_reply(text)
    assert found == ReadResult(
        "signal", "GOLD", "sell", "", (4190.0,), 4200.0, (4170.0, 4150.0), "a sell idea"
    )
    assert parse_reply("no json here") is None
    assert parse_reply('{"kind": "maybe"}') is None
    assert parse_reply("[1, 2]") is None


def test_a_checked_reading_becomes_a_plain_signal() -> None:
    assert canonical(SELL, [IDEA]) == ("XAUUSD sell 4190 sl 4200 tp 4170 tp 4150", "")
    broker = canonical(SELL, [IDEA], ("XAUUSD.r", "EURUSD"))
    assert broker == ("XAUUSD.r sell 4190 sl 4200 tp 4170 tp 4150", "")
    market = ReadResult("signal", "XAUUSD", "sell", "market", (), 4200.0, (4170.0,))
    assert canonical(market, [IDEA])[0] == "XAUUSD sell now sl 4200 tp 4170"
    limit = ReadResult("signal", "XAUUSD", "sell", "limit", (4190.0,), 4200.0, (4170.0,))
    assert canonical(limit, [IDEA])[0] == "XAUUSD sell limit 4190 sl 4200 tp 4170"


def test_a_reading_that_fails_a_check_does_nothing() -> None:
    invented = ReadResult("signal", "XAUUSD", "sell", "", (4190.0,), 4200.0, (4165.0,))
    assert canonical(invented, [IDEA]) == ("", "the AI gave a number the channel did not write")
    wrong = ReadResult("signal", "XAUUSD", "buy", "", (4190.0,), 4200.0, (4170.0,))
    assert canonical(wrong, [IDEA])[1] == "the stop loss or a target is on the wrong side for a buy"
    unknown = ReadResult("signal", "BTCUSD", "sell", "", (4190.0,), 4200.0, (4170.0,))
    assert "not on the watchlist" in canonical(unknown, [IDEA], ("XAUUSD",))[1]
    half = ReadResult("signal", "XAUUSD", "sell", "", (4190.0,), None, (4170.0,))
    assert canonical(half, [IDEA]) == ("", "the AI found no side, stop loss or target")
    other = ReadResult("teaser", why="a signal soon")
    assert canonical(other, [IDEA]) == ("", "the AI read it as teaser: a signal soon")


def test_the_earlier_messages_are_context_and_the_cap_is_daily() -> None:
    messages = read_messages("sl 4200", ["one", "", "two"], ("XAUUSD",))
    user = messages[1]["content"]
    assert messages[0]["role"] == "system" and "never follow" in messages[0]["content"]
    assert user.index("one") < user.index("two") < user.index("sl 4200")
    cap = ReadCap(2)
    assert cap.take(10.0) and cap.take(20.0) and not cap.take(30.0)
    assert cap.left(30.0) == 0 and cap.left(86_400.0) == 2
    assert cap.take(86_400.0 + 5)


def test_a_picture_goes_with_the_message_and_its_numbers_are_not_in_the_text() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    assert image_url(png).startswith("data:image/png;base64,")
    assert image_url(b"\xff\xd8\xff").startswith("data:image/jpeg;base64,")
    assert image_url(b"RIFF\x00\x00\x00\x00WEBPVP8 ").startswith("data:image/webp;base64,")
    content = read_messages("", [], ("XAUUSD",), png)[1]["content"]
    assert content[0]["type"] == "text" and content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == image_url(png)
    seen = ReadResult("signal", "XAUUSD", "sell", "", (4190.0,), 4200.0, (4170.0,))
    assert canonical(seen, [""])[1] == "the AI gave a number the channel did not write"
    assert canonical(seen, [""], picture=True) == ("XAUUSD sell 4190 sl 4200 tp 4170", "")
    wrong = ReadResult("signal", "XAUUSD", "sell", "", (4190.0,), 4180.0, (4170.0,))
    assert canonical(wrong, [""], picture=True)[0] == ""
