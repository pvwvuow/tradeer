"""The words of trading signals in English and Persian, and the text normaliser.

Signal messages mix languages, digits and symbols: "طلا فروش ۲۳۶۱ استاپ ۲۳۶۸",
"XAU/USD BUY 2345-2340 SL 2335 TP1 2350". `normalise` turns them into one simple form
(Latin digits, lower case, one English word per meaning) and `tokens` splits that into words
and numbers, so the parser and the prefilter read every message the same way. Pure: no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.engine.currency_guard import legs

DIGITS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩يك",
    "01234567890123456789یک",
)
# Persian phrases first (longest first), each to one English word with spaces around it.
PHRASES: tuple[tuple[str, str], ...] = (
    ("بای استاپ", "buy stop"),
    ("سل استاپ", "sell stop"),
    ("بای لیمیت", "buy limit"),
    ("سل لیمیت", "sell limit"),
    ("خرید استاپ", "buy stop"),
    ("فروش استاپ", "sell stop"),
    ("استاپ لاس", "sl"),
    ("حد ضرر", "sl"),
    ("حدضرر", "sl"),
    ("استاپ", "sl"),
    ("حد سود", "tp"),
    ("حدسود", "tp"),
    ("تارگت", "tp"),
    ("هدف", "tp"),
    ("تی پی", "tp"),
    ("نقطه ورود", "entry"),
    ("ورود", "entry"),
    ("خرید", "buy"),
    ("فروش", "sell"),
    ("بای", "buy"),
    ("سل", "sell"),
    ("لیمیت", "limit"),
    ("مارکت", "now"),
    ("الان", "now"),
    ("فوری", "now"),
    ("باز", "open"),
)
ENGLISH: tuple[tuple[str, str], ...] = (
    (r"stop\s*loss", "sl"),
    (r"s\s*[/.]\s*l\b", "sl"),
    (r"take\s*profits?", "tp"),
    (r"t\s*[/.]\s*p\b", "tp"),
    (r"targets?", "tp"),
    (r"\blong\b", "buy"),
    (r"\bshort\b", "sell"),
    (r"\b(market|cmp)\b", "now"),
    (r"\b(enter|price|zone|at)\b", "entry"),
)
TIME = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)")
NOT_PRICES = re.compile(
    r"[+-]?\d+(?:\.\d+)?\s*(?:pips?|points?|pts|پیپ|پوینت|%|r\b|x\b)",
)
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
PAIR = re.compile(r"\b([a-z]{3})\s*/\s*([a-z]{3})\b")
TOKEN = re.compile(r"\d+(?:\.\d+)?|[^\W\d_]+\d*")
LABELS = frozenset({"buy", "sell", "limit", "stop", "sl", "tp", "entry", "now", "open"})
# GOLD, XAU, طلا -> XAUUSD and the like; the broker suffix is found in `resolve_symbol`.
DEFAULT_ALIASES: Mapping[str, str] = {
    "GOLD": "XAUUSD",
    "XAU": "XAUUSD",
    "طلا": "XAUUSD",
    "گلد": "XAUUSD",
    "اونس": "XAUUSD",
    "SILVER": "XAGUSD",
    "XAG": "XAGUSD",
    "نقره": "XAGUSD",
}


def _word(pattern: str) -> re.Pattern[str]:
    """A Persian phrase as a whole word: "سل" must not match inside "سلام"."""
    return re.compile(rf"(?<!\w){re.escape(pattern)}(?!\w)")


PERSIAN = tuple((_word(source), target) for source, target in PHRASES)
ENGLISH_RULES = tuple((re.compile(source), target) for source, target in ENGLISH)


def normalise(text: str) -> str:
    """Latin digits, lower case, one English word per meaning, no times or pip counts."""
    found = text.translate(DIGITS).replace("٫", ".").replace("٬", "")
    found = THOUSANDS.sub("", found).lower()
    for pattern, target in PERSIAN:
        found = pattern.sub(f" {target} ", found)
    for rule, target in ENGLISH_RULES:
        found = rule.sub(f" {target} ", found)
    found = PAIR.sub(r"\1\2", found)
    found = TIME.sub(" ", found)
    found = NOT_PRICES.sub(" ", found)
    return found.replace("@", " entry ")


@dataclass(frozen=True)
class Token:
    text: str
    number: float | None = None

    @property
    def label(self) -> str:
        """The signal word ("tp" for tp1, tp2...), "" for other words and numbers."""
        if self.number is not None:
            return ""
        word = self.text.rstrip("0123456789")
        return word if word in LABELS else ""


def tokens(text: str) -> list[Token]:
    """The words and numbers of a normalised text, in order."""
    found: list[Token] = []
    for match in TOKEN.finditer(text):
        part = match.group(0)
        if part[0].isdigit():
            found.append(Token(part, float(part)))
        else:
            found.append(Token(part))
    return found


def _keys(name: str) -> set[str]:
    upper = name.upper()
    keys = {upper, re.split(r"[.\-_#]", upper)[0]}
    forex = re.match(r"[A-Z]{6}", upper)
    if forex is not None:
        keys.add(forex.group(0))
    return keys


def resolve_symbol(
    word: str,
    symbols: tuple[str, ...] = (),
    aliases: Mapping[str, str] | None = None,
) -> str:
    """The broker's symbol for a word ("gold" -> "XAUUSD.r"), "" when it is no symbol.

    Without a symbol list (no MT5 yet) a known alias or a pair of two known currencies is
    returned in capitals.
    """
    names = {**DEFAULT_ALIASES, **{k.upper(): v.upper() for k, v in (aliases or {}).items()}}
    upper = word.upper()
    target = names.get(upper, upper)
    if symbols:
        for name in symbols:
            if target in _keys(name):
                return name
        return ""
    if upper in names or (len(target) == 6 and legs(target) is not None):
        return target
    return ""
