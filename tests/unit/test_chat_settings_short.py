"""Chat settings find a channel by the part of its title before " | " (0.51.2)."""

from app.channels.chat_settings import read_change
from app.channels.policy import ChannelMode

TITLES = ("Araz Forex | \u0633\u06cc\u06af\u0646\u0627\u0644 \U0001f4c8", "Mmd Jvd | MSJ Trade")


def test_the_short_name_is_enough() -> None:
    risk = read_change("Araz Forex risk 1%", TITLES)
    assert risk is not None and (risk.title, risk.field, risk.value) == (
        TITLES[0],
        "risk_percent",
        1.0,
    )
    money = read_change("give mmd jvd 100 dollars", TITLES)
    assert money is not None and (money.title, money.field, money.value) == (
        TITLES[1],
        "budget",
        100.0,
    )
    mode = read_change("\u200fAraz\u00a0Forex live", TITLES)
    assert mode is not None and mode.value is ChannelMode.LIVE


def test_the_whole_title_still_works_and_no_name_is_nothing() -> None:
    whole = read_change(f"{TITLES[0]} paper", TITLES)
    assert whole is not None and whole.value is ChannelMode.PAPER
    assert read_change("risk 1%", TITLES) is None
