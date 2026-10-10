"""Follow-ups of channel signals and settings asked for in the chat (docs/SIGNAL_DESK.md 3.4
and 3.6, phases 21d2 and 21e): plain words in, one change out, nothing sent by itself."""

from app.channels.chat_settings import find_title, read_change
from app.channels.policy import ChannelMode, ChannelPolicy
from app.channels.updates import Earlier, Update, UpdateKind, link, read_update
from app.domain.signals import Direction

TITLES = ("Gold Room", "FX Pro Signals")


def test_the_update_words() -> None:
    cases = {
        "TP1 HIT ✅ +40 pips": UpdateKind.TP_HIT,
        "XAUUSD move SL to entry": UpdateKind.BREAK_EVEN,
        "سیو سود کنید": UpdateKind.BREAK_EVEN,
        "XAUUSD close now": UpdateKind.CLOSE,
        "cancel the buy limit": UpdateKind.CANCEL,
        "SL hit ❌": UpdateKind.SL_HIT,
        "طلا تارگت دوم خورد": UpdateKind.TP_HIT,
    }
    for text, kind in cases.items():
        found = read_update(text)
        assert found is not None and found.kind is kind, text
    moved = read_update("EURUSD new sl 1.0950")
    assert moved == Update(UpdateKind.NEW_SL, "EURUSD", None, 1.095)
    persian = read_update("حد ضرر رو ببرید ۲۳۴۰")
    assert persian is not None and persian.kind is UpdateKind.NEW_SL and persian.price == 2340.0
    assert read_update("new tp 2380") == Update(UpdateKind.NEW_TP, "", None, 2380.0)
    assert read_update("good morning traders") is None
    gold = read_update("طلا تارگت دوم خورد")
    assert gold is not None and gold.symbol == "XAUUSD" and not gold.acts
    cancel = read_update("cancel the buy limit")
    assert cancel is not None and cancel.direction is Direction.LONG and cancel.acts


def test_an_update_belongs_to_the_signal_it_replies_to_or_the_newest_alike() -> None:
    earlier = [
        Earlier(1, 1_000.0, "XAUUSD", Direction.LONG),
        Earlier(2, 2_000.0, "XAUUSD", Direction.SHORT),
        Earlier(3, 3_000.0, "EURUSD", Direction.LONG),
    ]
    close = Update(UpdateKind.CLOSE, "XAUUSD")
    assert link(close, 1, 5_000.0, earlier) == earlier[0]
    assert link(close, None, 5_000.0, earlier) == earlier[1]
    buys = Update(UpdateKind.CLOSE, "XAUUSD", Direction.LONG)
    assert link(buys, None, 5_000.0, earlier) == earlier[0]
    assert link(close, None, 2_000.0 + 25 * 3600, earlier) is None  # older than 24 hours
    assert link(Update(UpdateKind.CLOSE), None, 5_000.0, earlier) is None
    assert link(close, 99, 5_000.0, earlier) is None


def test_settings_in_the_chat_need_a_channel_name_and_a_value() -> None:
    cases = {
        "Give Gold Room 100 dollars": ("budget", 100.0),
        "به کانال Gold Room ۱۰۰ دلار بده": ("budget", 100.0),
        "gold room $250": ("budget", 250.0),
        "set gold room risk to 1%": ("risk_percent", 1.0),
        "ریسک کانال FX Pro Signals رو ۱.۵ درصد کن": ("risk_percent", 1.5),
        "Gold Room live": ("mode", ChannelMode.LIVE),
        "کانال Gold Room رو آزمایشی کن": ("mode", ChannelMode.PAPER),
    }
    for text, (field, value) in cases.items():
        change = read_change(text, TITLES)
        assert change is not None and (change.field, change.value) == (field, value), text
    assert read_change("what about Gold Room?", TITLES) is None
    assert read_change("XAUUSD buy 2345 sl 2335", TITLES) is None
    assert find_title("news from fx pro signals and gold room", TITLES) == "FX Pro Signals"
    change = read_change("Give Gold Room 100 dollars", TITLES)
    assert change is not None
    assert change.old_new(ChannelPolicy()) == ("none", "100.00")
    assert change.apply(ChannelPolicy()).budget == 100.0
