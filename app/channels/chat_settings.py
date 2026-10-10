"""A channel's settings asked for in the AI Lab chat (docs/SIGNAL_DESK.md 3.4, phase 21d2).
Pure: the text and the channel names in, the one change it asks for out.

"Give Gold Room 100 dollars", "به کانال Gold Room ۱۰۰ دلار بده", "set Gold Room risk to 1%",
"Gold Room live". The chat then shows a settings card with the old and the new value; it is
saved only after your hold-to-confirm. Nothing here (and not the AI) can save a setting.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.channels.policy import ChannelMode, ChannelPolicy
from app.signals.words import normalise

MONEY = re.compile(
    r"(?:\$\s*(\d+(?:\.\d+)?))|(\d+(?:\.\d+)?)\s*(?:\$|dollars?|usd|دلار|تتر|usdt)",
)
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|٪|percent|درصد)")
RISK = re.compile(r"\brisk\b|ریسک")
BUDGET = re.compile(r"\b(?:budget|give|allow)\b|بودجه|بده|اختصاص")
MODES: tuple[tuple[re.Pattern[str], ChannelMode], ...] = (
    (re.compile(r"\blive\b|لایو|واقعی"), ChannelMode.LIVE),
    (re.compile(r"\bpaper\b|\btrial\b|آزمایشی|کاغذی"), ChannelMode.PAPER),
    (re.compile(r"\b(?:off|stop)\b|خاموش"), ChannelMode.OFF),
)


@dataclass(frozen=True)
class Change:
    title: str  # the channel's title, as the store has it
    field: str  # budget, risk_percent or mode
    value: float | ChannelMode

    def apply(self, policy: ChannelPolicy) -> ChannelPolicy:
        return ChannelPolicy.model_validate({**policy.model_dump(), self.field: self.value})

    def old_new(self, policy: ChannelPolicy) -> tuple[str, str]:
        old = getattr(policy, self.field)
        return _shown(self.field, old), _shown(self.field, self.value)


def _shown(field: str, value: object) -> str:
    if isinstance(value, ChannelMode):
        return value.value
    if field == "risk_percent" and isinstance(value, int | float):
        return f"{value:g}%"
    if isinstance(value, int | float):
        return f"{value:,.2f}" if value else "none"
    return str(value)


def find_title(text: str, titles: Sequence[str]) -> str:
    """The longest channel title in the text (case does not matter), "" for none."""
    lower = text.casefold()
    found = [title for title in titles if title.strip() and title.casefold() in lower]
    return max(found, key=len, default="")


def read_change(text: str, titles: Sequence[str]) -> Change | None:
    title = find_title(text, titles)
    if not title:
        return None
    rest = normalise(text.casefold().replace(title.casefold(), " "))
    raw = text.casefold().replace(title.casefold(), " ")
    percent = PERCENT.search(rest) or PERCENT.search(raw)
    if RISK.search(rest) and percent:
        return Change(title, "risk_percent", float(percent.group(1)))
    money = MONEY.search(rest) or MONEY.search(raw)
    if money and (BUDGET.search(rest) or "$" in raw or money.group(2)):
        return Change(title, "budget", float(money.group(1) or money.group(2)))
    for pattern, mode in MODES:
        if pattern.search(rest):
            return Change(title, "mode", mode)
    return None
