"""The channel feed's chat lines in Persian (0.44.x).

The feed writes its lines in English (the log and the AI read them); the AI Lab shows them
in Persian when the app is in Persian. Prices, symbols and the parts the table does not know
stay as they are, left to right inside the line.
"""

from __future__ import annotations

import re

MISSING_FA = {
    "symbol": "نماد",
    "side": "جهت",
    "stop loss": "حد ضرر",
    "target": "تارگت",
    "the details": "جزئیات",
}
WAIT = (
    r"^waiting for the rest of the signal \(no (?P<missing>.+?) yet\)"
    r"(?P<ai>; the AI reads it now)?$"
)
GONE = r"^the signal stayed incomplete \(no (?P<missing>.+?)\)(?P<rest>, nothing done|: the AI .+)$"
LINES_FA: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), template)
    for pattern, template in (
        (WAIT, "منتظر بقیه‌ی سیگنال (هنوز {missing} ندارد){ai}"),
        (r"^(?P<count>\d+) messages joined into one signal$", "{count} پیام یک سیگنال شد"),
        (GONE, "سیگنال ناقص ماند ({missing} نداشت){rest}"),
        (r"^the edited message completed the signal$", "با ویرایش پیام، سیگنال کامل شد"),
        (r"^the AI read the message as: (?P<text>.+)$", "AI پیام را این‌طور خواند: {text}"),
        (
            r"^the AI read the picture as: (?P<text>.+) \(check the numbers on the card\)$",
            "AI عکس را این‌طور خواند: {text} (عددها را روی کارت چک کنید)",
        ),
        (
            r"^the AI gave a number the channel did not write$",
            "AI عددی داد که در پیام کانال نبود؛ کاری انجام نشد",
        ),
        (r"^the AI found no side, stop loss or target$", "AI جهت، حد ضرر یا تارگت پیدا نکرد"),
        (
            r"^the stop loss or a target is on the wrong side for a (?P<side>buy|sell)$",
            "حد ضرر یا تارگت برای {side} در سمت اشتباه است",
        ),
        (
            r"(?s)^the AI could not read a picture(?:.*(?P<pause>; pictures go to the AI again "
            r"in 6 hours)$)?",
            "AI نتوانست عکس را بخواند (مدل شما عکس می‌خواند؟){pause}",
        ),
    )
)
REST_FA = {
    ", nothing done": "، کاری انجام نشد",
    ": the AI reads it once more": "؛ AI یک بار دیگر می‌خواندش",
    "; the AI reads it now": "؛ AI الان می‌خواندش",
    "; pictures go to the AI again in 6 hours": "؛ تا 6 ساعت عکسی برای AI فرستاده نمی‌شود",
    "": "",
}
SIDES_FA = {"buy": "خرید", "sell": "فروش"}


def _missing(text: str) -> str:
    return "، ".join(MISSING_FA.get(part.strip(), part.strip()) for part in text.split(","))


def feed_line_fa(text: str) -> str:
    """A feed line in Persian, or the line itself when it is not one of the known lines."""
    for pattern, template in LINES_FA:
        found = pattern.match(text)
        if found is None:
            continue
        parts = {key: value or "" for key, value in found.groupdict().items()}
        if "missing" in parts:
            parts["missing"] = _missing(parts["missing"])
        for key in ("ai", "rest", "pause"):
            if key in parts:
                parts[key] = REST_FA.get(parts[key], parts[key])
        if "side" in parts:
            parts["side"] = SIDES_FA.get(parts["side"], parts["side"])
        return template.format(**parts)
    return text
