"""Auto-take for a Telegram channel (phase 23f, 0.50.5): opt-in per channel, off by default.

When it is on, a Live channel's order card that waits for you is confirmed by itself after
`AUTO_TAKE_SECONDS`, as your hold would; Skip on the card stops it. Everything else stays:
the card was sized with the channel's budget and checked by the risk manager, and the engine
checks the price, the spread and every limit again before it sends. Never on a real account,
never for numbers read from a picture, at most `AUTO_PER_DAY` trades a day per channel; the
channel's daily loss and drawdown stops still end its cards. The AI never sends an order:
only this rule you turned on does.

Turn it on in the AI Lab chat ("Gold Room auto-take on", or in Persian with the words
below); a settings card asks for one hold first. 0.51.1: also a checkbox on the Channels
page, and the chat line is read the same with a typographic dash, a no-break space, the
invisible direction marks a Persian keyboard adds, or Arabic letter forms (`plain`). Pure:
the text and the titles in.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

AUTO_TAKE_SECONDS = 10
AUTO_PER_DAY = 5

AUTO = re.compile(r"auto[\s-]*take|autotake|\bauto\b|خودکار|اتوماتیک|اتو\s*تیک")
OFF = re.compile(r"\boff\b|\bstop\b|خاموش|غیر\s*فعال")
ON = re.compile(r"\bon\b|\bstart\b|روشن|فعال")

DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"
INVISIBLE = "\u200b\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff\u0640"
SPACES = "\u00a0\u200c\u2007\u202f"
LETTERS = {"\u064a": "\u06cc", "\u0649": "\u06cc", "\u0643": "\u06a9"}  # Arabic forms
PLAIN = str.maketrans(
    {
        **dict.fromkeys(DASHES, "-"),
        **dict.fromkeys(INVISIBLE, ""),
        **dict.fromkeys(SPACES, " "),
        **LETTERS,
    },
)


def plain(text: str) -> str:
    """The text as the matching sees it: one kind of dash and space, no invisible marks,
    Persian letter forms, no case."""
    return " ".join(text.translate(PLAIN).split()).casefold()


def short_title(title: str) -> str:
    """The part of a title before " | " (channels often add a slogan after it)."""
    return title.split("|", 1)[0].strip()


def channel_named(text: str, titles: Sequence[str]) -> str:
    """The channel the text names: the longest whole title in it, else the one title whose
    first part is in the text ("" for none or two)."""
    lower = plain(text)
    whole = [title for title in titles if plain(title) and plain(title) in lower]
    if whole:
        return max(whole, key=lambda title: len(plain(title)))
    found = [title for title in titles if len(plain(short_title(title))) >= 3]
    found = [title for title in found if plain(short_title(title)) in lower]
    return found[0] if len(found) == 1 else ""


def read_auto(text: str, titles: Sequence[str]) -> tuple[str, bool] | None:
    """(title, on) when the text turns a channel's Auto-take on or off, else None."""
    title = channel_named(text, titles)
    if not title:
        return None
    rest = plain(text).replace(plain(title), " ").replace(plain(short_title(title)), " ")
    if not AUTO.search(rest):
        return None
    if OFF.search(rest):
        return title, False
    if ON.search(rest):
        return title, True
    return None
