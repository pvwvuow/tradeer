"""What the Telegram bot says, drawn neatly (phase 22b, 0.47.0).

Every message is HTML (bold headline, values in a fixed-width face, one line for what is
going on) instead of the bare lines of 0.46. A channel signal gets its own message:

- **approve**: the order card waits for you; the message carries Approve and Skip buttons
  (`alert_buttons`). Nothing is sent until you press Approve in an unlocked chat.
- **info**: nothing to decide (a Paper trial, a duplicate, a signal the checks refused, an
  order that already went out): the message only tells.

All text from outside (a channel's title, a reason) is escaped. Pure: no Qt, no network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

from app.notify.events import EventKind, Notice

APPROVE, INFO, REFUSED = "approve", "info", "refused"
ID_CHARS = 32  # the request id's part kept in a button (Telegram allows 64 bytes in all)
MAX_STATUS = 220
Button = tuple[str, str]  # the label and the callback data
Keyboard = tuple[tuple[Button, ...], ...]

NOTICE_ICONS: dict[EventKind, str] = {
    EventKind.TRADE_OPENED: "\U0001f4c8",
    EventKind.TRADE_CLOSED: "\U0001f3c1",
    EventKind.APPROVAL_NEEDED: "\u23f3",
    EventKind.LIMIT_HIT: "\U0001f6d1",
    EventKind.DISCONNECTED: "\U0001f50c",
    EventKind.ERROR: "\u26a0\ufe0f",
    EventKind.SYNC_FAILING: "\u2601\ufe0f",
    EventKind.DRIFT: "\U0001f9ea",
    EventKind.DAILY_REPORT: "\U0001f4cb",
}
KIND_MARKS = {APPROVE: "\u23f3", REFUSED: "\u26a0\ufe0f", INFO: "\u2139\ufe0f"}


@dataclass(frozen=True)
class SignalAlert:
    """One channel signal as the bot tells it."""

    channel: str
    symbol: str
    side: str  # "BUY LIMIT", "SELL", or "" when the side is not known
    rows: tuple[tuple[str, str, str], ...] = ()  # label, value, a note ("Entry", "4190", "")
    size: str = ""  # lots and money at risk, from the risk manager
    status: str = ""
    kind: str = INFO
    request_id: str = ""
    expires: str = ""  # a clock time, only for a card that waits


@dataclass(frozen=True)
class CardReply:
    """The app's answer to a button: a small toast, a line under the message and, when the
    buttons change (`buttons`) or are over (`done`), the new keyboard."""

    toast: str
    note: str = ""
    buttons: Keyboard | None = None
    done: bool = False


def clean(text: str, limit: int = MAX_STATUS) -> str:
    """One short line."""
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1] + "\u2026"


def format_notice(notice: Notice) -> str:
    icon = NOTICE_ICONS.get(notice.kind, "\u2139\ufe0f")
    return f"{icon} <b>{escape(notice.title)}</b>\n{escape(clean(notice.text, 1500))}"


def format_alert(alert: SignalAlert) -> str:
    side = alert.side.strip()
    dot = "\u26aa"
    if side.startswith("BUY"):
        dot = "\U0001f7e2"
    elif side.startswith("SELL"):
        dot = "\U0001f534"
    head = f"{dot} <b>{escape(f'{side} {alert.symbol}'.strip() or 'Signal')}</b>"
    lines = [head, f"\U0001f4e1 {escape(alert.channel)}"]
    if alert.rows:
        lines.append("")
    for label, value, note in alert.rows:
        row = f"<code>{escape(label):<5}</code> <code>{escape(value)}</code>"
        if note:
            row += f"  <i>{escape(note)}</i>"
        lines.append(row)
    if alert.size:
        lines += ["", f"\U0001f4bc {escape(alert.size)}"]
    mark = KIND_MARKS.get(alert.kind, "\u2139\ufe0f")
    status = escape(clean(alert.status))
    if alert.expires:
        status += f" \u00b7 expires {escape(alert.expires)}"
    if status.strip():
        lines += ["", f"{mark} {status}"]
    return "\n".join(lines)


def short_id(request_id: str) -> str:
    return request_id[:ID_CHARS]


def alert_buttons(request_id: str) -> Keyboard:
    """Approve and Skip for a card that waits."""
    key = short_id(request_id)
    return ((("\u2705 Approve", f"ok:{key}"), ("\u23ed Skip", f"no:{key}")),)


def real_buttons(request_id: str) -> Keyboard:
    """A real order asks once more before it goes."""
    key = short_id(request_id)
    return (
        (("\u26a0\ufe0f Yes, send the REAL order", f"yes:{key}"),),
        (("Cancel", f"back:{key}"),),
    )


def inline_keyboard(buttons: Sequence[Sequence[Button]]) -> dict[str, list[list[dict[str, str]]]]:
    rows = [[{"text": text, "callback_data": data} for text, data in row] for row in buttons]
    return {"inline_keyboard": rows}
