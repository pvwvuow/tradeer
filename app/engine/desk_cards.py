"""How an order card stands and why it was refused (docs/AI_AUTOPILOT.md 23a, 0.50.2). Pure.

Three fixes of the card's state (`app.ui.signal_card.card_state` stays for the older code):

- **B15**: a card with one leg open (sent, filled or managed) is not "failed" because another
  leg failed: it is "filled" or "sent", and its leg lines say which leg failed.
- **B14**: a card whose waiting legs are past their expiry is "expired" and its hold is off,
  even before the pipeline's next cycle marks the legs.
- **B11**: a card refused after it was shown says why: the legs' own reasons (the risk
  manager, the filters) come first, the desk's first message only when the legs have none,
  and a line says what can be done about it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.domain.signals import SignalRecord, SignalState
from app.engine.signal_desk import DeskResult

REASON_STATES = (
    SignalState.FAILED,
    SignalState.EXPIRED,
    SignalState.RISK_REJECTED,
    SignalState.FILTERED_OUT,
)
OPEN_STATES = (SignalState.FILLED, SignalState.MANAGED)
ENDED = (SignalState.EXPIRED, SignalState.USER_REJECTED)
LATE = "refused by the final check after it was shown"
EXPIRED_TEXT = "Expired: nothing was sent. The signal was planned against an older price."
FIXES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("budget too small", "smallest lot", "budget"),
        "Fix: give the channel a bigger budget or floor in Settings > Telegram channels.",
    ),
    (
        ("one bet", "same bet", "currency and direction"),
        "The bot has a trade on the same currency and side: this one waits until it ends.",
    ),
    (("daily loss",), "The daily loss stop is on: new trades wait for the next day."),
    (("drawdown",), "The drawdown stop is on: check the Risk page."),
    (
        ("open trades", "already has"),
        "The open-trades limit is full: a trade has to close first (Risk settings).",
    ),
    (
        ("total open risk", "currency exposure"),
        "Too much risk is open already: a trade has to close first (Risk settings).",
    ),
    (("margin",), "Not enough free margin for this lot."),
    (("spread",), "The spread is too wide right now."),
    (("has moved", "price has not moved", "moved"), "The price has moved away from the signal."),
    (("market closed", "market open"), "The market is closed for this symbol."),
)
DESK_FA: dict[str, str] = {
    LATE: "پس از نمایش، در بررسی نهایی رد شد",
    EXPIRED_TEXT: "منقضی شد: چیزی ارسال نشد. سیگنال با قیمتِ قدیمی‌تری برنامه‌ریزی شده بود.",
    FIXES[0][1]: "راه حل: در تنظیمات > کانال‌های تلگرام بودجه یا کفِ کانال را بیشتر کنید.",
    FIXES[1][1]: "ربات روی همین ارز و همین جهت معامله دارد: این یکی تا بسته شدن آن صبر می‌کند.",
    FIXES[2][1]: "توقف ضرر روزانه فعال است: معامله‌های تازه برای روز بعد می‌مانند.",
    FIXES[3][1]: "توقف افت سرمایه فعال است: صفحه‌ی ریسک را ببینید.",
    FIXES[4][1]: "سقف معامله‌های باز پر است: اول باید یک معامله بسته شود (تنظیمات ریسک).",
    FIXES[5][1]: "ریسکِ باز زیاد است: اول باید یک معامله بسته شود (تنظیمات ریسک).",
    FIXES[6][1]: "مارجینِ آزاد برای این حجم کافی نیست.",
    FIXES[7][1]: "اسپرد الان خیلی زیاد است.",
    FIXES[8][1]: "قیمت از نقطه‌ی سیگنال دور شده است.",
    FIXES[9][1]: "بازارِ این نماد بسته است.",
}
Words = Callable[[str], str]


def desk_words(fa: bool) -> Words:
    def word(english: str) -> str:
        return DESK_FA.get(english, english) if fa else english

    return word


def desk_state(
    result: DeskResult | None,
    legs: Sequence[SignalRecord],
    *,
    sent: bool = False,
    skipped: bool = False,
    now: float | None = None,
) -> str:
    """Where the card is: checking, refused, waiting, sending, sent, filled, closed,
    failed, expired or skipped (see the module docstring for what changed)."""
    states = [leg.signal.state for leg in legs]
    moving = any(state.open_position or state is SignalState.APPROVED for state in states)
    if skipped and not moving:
        return "skipped"
    if any(state in OPEN_STATES for state in states):
        return "filled"
    if SignalState.SENT in states:
        return "sent"
    waiting = [leg for leg in legs if leg.signal.state is SignalState.PENDING_APPROVAL]
    if sent and (waiting or SignalState.APPROVED in states):
        return "sending"
    if waiting:
        late = now is not None and all(leg.signal.expires_at <= now for leg in waiting)
        return "expired" if late else "waiting"
    if SignalState.FAILED in states:
        return "failed"
    if SignalState.CLOSED in states:
        return "closed"
    if states and all(state in ENDED for state in states):
        return "skipped" if SignalState.USER_REJECTED in states else "expired"
    return "checking" if result is None else "refused"


def leg_reasons(legs: Sequence[SignalRecord]) -> list[str]:
    """The reasons of the legs that stopped, each once, in the legs' order."""
    found: list[str] = []
    for leg in legs:
        signal = leg.signal
        if signal.state not in REASON_STATES:
            continue
        reason = leg.reject_reason or (signal.history[-1].reason if signal.history else "")
        if reason and reason not in found:
            found.append(reason)
    return found


def refusal(result: DeskResult | None, legs: Sequence[SignalRecord], word: Words) -> str:
    """Why a refused card was refused: the legs' reasons, else the desk's message (only
    when the desk itself refused it: its message of a card that waited is old news)."""
    reasons = leg_reasons(legs)
    if reasons:
        return "; ".join(reasons)
    if result is not None and not result.ok and result.message:
        return result.message
    return word(LATE)


def fix_hint(reason: str) -> str:
    """What can be done about a refusal, "" when nothing is known."""
    low = reason.lower()
    for words, hint in FIXES:
        if any(item in low for item in words):
            return hint
    return ""
