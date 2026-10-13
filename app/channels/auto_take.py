"""Auto-take for a Telegram channel (phase 23f, 0.50.5): opt-in per channel, off by default.

When it is on, a Live channel's order card that waits for you is confirmed by itself after
`AUTO_TAKE_SECONDS`, as your hold would; Skip on the card stops it. Everything else stays:
the card was sized with the channel's budget and checked by the risk manager, and the engine
checks the price, the spread and every limit again before it sends. Never on a real account,
never for numbers read from a picture, at most `AUTO_PER_DAY` trades a day per channel; the
channel's daily loss and drawdown stops still end its cards. The AI never sends an order:
only this rule you turned on does.

Turn it on in the AI Lab chat ("Gold Room auto-take on", or in Persian with the words
below); a settings card asks for one hold first. Pure: the text and the titles in.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.channels.chat_settings import find_title

AUTO_TAKE_SECONDS = 10
AUTO_PER_DAY = 5

AUTO = re.compile(r"auto[\s-]*take|autotake|\bauto\b|خودکار|اتوماتیک|اتو\s*تیک")
OFF = re.compile(r"\boff\b|\bstop\b|خاموش|غیر\s*فعال")
ON = re.compile(r"\bon\b|\bstart\b|روشن|فعال")


def short_title(title: str) -> str:
    """The part of a title before " | " (channels often add a slogan after it)."""
    return title.split("|", 1)[0].strip()


def channel_named(text: str, titles: Sequence[str]) -> str:
    """The channel the text names: its whole title, else the one title whose first part is
    in the text ("" for none or two)."""
    whole = find_title(text, titles)
    if whole:
        return whole
    lower = text.casefold()
    found = [title for title in titles if len(short_title(title)) >= 3]
    found = [title for title in found if short_title(title).casefold() in lower]
    return found[0] if len(found) == 1 else ""


def read_auto(text: str, titles: Sequence[str]) -> tuple[str, bool] | None:
    """(title, on) when the text turns a channel's Auto-take on or off, else None."""
    title = channel_named(text, titles)
    if not title:
        return None
    lower = text.casefold()
    rest = lower.replace(title.casefold(), " ").replace(short_title(title).casefold(), " ")
    if not AUTO.search(rest):
        return None
    if OFF.search(rest):
        return title, False
    if ON.search(rest):
        return title, True
    return None
