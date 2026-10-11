"""A channel signal in the Telegram bot (phase 22b, 0.47.0).

`alert_from_card` turns an order card that got the desk's first answer into the bot's
message: one that waits for you gets Approve and Skip (`app.notify.bot_text`), everything
else (an order that went out by itself in Auto, a signal the checks refused, a Paper trial)
only tells. `CardActions` is the app's side of the buttons. The bot's thread cannot touch
widgets, so `CardActions.handle` hands the press to the UI thread and waits for the answer.

Approve does what the hold does (`LabDesk.send`): every waiting leg goes to the approval
queue and the engine checks the price, the spread and the limits again before it sends. On a
real account the press asks once more. Skip dismisses the legs. Nothing else can be done
from a button, and a card that is no longer waiting says so.
"""

from __future__ import annotations

import math
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Qt, Signal

from app.notify.bot_text import (
    APPROVE,
    INFO,
    REFUSED,
    CardReply,
    SignalAlert,
    alert_buttons,
    clean,
    real_buttons,
    short_id,
)
from app.signals.parse import ParsedSignal
from app.ui.lab_desk import LabDesk
from app.ui.signal_card import OrderCard, price_text, side_text, size_line

WAIT_SECONDS = 8.0  # the bot waits this long for the UI thread
GONE = "This signal is gone: the app was restarted or the card was cleared."
OVER = ("sent", "filled", "closed")
STOPPED = ("refused", "failed", "expired", "skipped")


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return clean(line)
    return ""


def rows_from_parsed(parsed: ParsedSignal) -> tuple[tuple[str, str, str], ...]:
    entry = "-".join(f"{value:g}" for value in parsed.entry) or "MARKET"
    rows = [("Entry", entry, ""), ("SL", price_text(parsed.sl, None), "")]
    for index, tp in enumerate(parsed.tps, start=1):
        rows.append((f"TP{index}", "open" if tp is None else f"{tp:g}", ""))
    return tuple(rows)


def alert_from_parsed(
    parsed: ParsedSignal,
    channel: str,
    symbol: str,
    status: str,
    kind: str = INFO,
) -> SignalAlert:
    """A signal that did not become a card (a Paper trial, a duplicate, a refusal)."""
    return SignalAlert(
        channel=channel,
        symbol=parsed.symbol or symbol,
        side=side_text(parsed.direction, parsed.order),
        rows=rows_from_parsed(parsed),
        status=status,
        kind=kind,
    )


def alert_from_card(card: OrderCard, channel: str) -> SignalAlert:
    """The card's first answer as the bot tells it."""
    plan = card.result.plan if card.result is not None else None
    legs = card.legs
    if plan is not None and plan.direction is not None:
        digits = legs[0].signal.digits if legs else None
        symbol = plan.symbol
        side = side_text(plan.direction, plan.order)
        rows = [
            ("Entry", price_text(plan.entry, digits), ""),
            ("SL", price_text(plan.sl, digits), ""),
        ]
        for index, tp in enumerate(plan.tps, start=1):
            rr = plan.rr(tp)
            note = f"R {rr:.1f}" if math.isfinite(rr) else ""
            rows.append((f"TP{index}", price_text(tp, digits), note))
        shown = tuple(rows)
    else:
        symbol = card.parsed.symbol
        side = side_text(card.parsed.direction, card.parsed.order)
        shown = rows_from_parsed(card.parsed)
    size = size_line(legs, card.currency).replace("   ", " \u00b7 ")
    state = card.state
    expires = ""
    kind = INFO
    status = first_line(card.status.text())
    if state == "waiting" and not card.block:
        kind = APPROVE
        status = "Waiting for your approval"
        if legs:
            expires = datetime.fromtimestamp(legs[0].signal.expires_at).strftime("%H:%M")
    elif state == "waiting":
        status = f"Waiting, but sending is off: {card.block}"
    elif state in STOPPED:
        kind = REFUSED
    elif state in OVER or state == "sending":
        status = status or "Sent: the engine places the order"
    return SignalAlert(
        channel=channel,
        symbol=symbol,
        side=side,
        rows=shown,
        size=size,
        status=status,
        kind=kind,
        request_id=card.request_id,
        expires=expires,
    )


class _Job:
    def __init__(self, action: str, key: str) -> None:
        self.action = action
        self.key = key
        self.done = threading.Event()
        self.reply = CardReply("The app did not answer. Tap again.")


class CardActions(QObject):
    """What Approve, Skip and the real-order confirmation do in the app."""

    asked = Signal(object)

    def __init__(self, desk: LabDesk) -> None:
        super().__init__(desk)
        self.desk = desk
        self.asked.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def handle(self, action: str, key: str) -> CardReply:
        """Called in the bot's thread: the press is answered by the UI thread."""
        job = _Job(action, key)
        self.asked.emit(job)
        if not job.done.wait(WAIT_SECONDS):
            return CardReply("The app is busy. Tap again in a moment.")
        return job.reply

    def _run(self, job: object) -> None:
        if not isinstance(job, _Job):
            return
        try:
            job.reply = self.act(job.action, job.key)
        except Exception as error:
            job.reply = CardReply(f"Failed: {type(error).__name__}")
        finally:
            job.done.set()

    def find(self, key: str) -> tuple[str, OrderCard] | None:
        if not key:
            return None
        for request_id, card in self.desk.cards.items():
            if short_id(request_id) == key or request_id.startswith(key):
                return request_id, card
        return None

    def act(self, action: str, key: str) -> CardReply:
        """UI thread: one press. Approve, Skip, Yes (a real order) or Cancel."""
        found = self.find(key)
        if found is None:
            return CardReply(GONE, done=True)
        request_id, card = found
        if action == "back":
            return CardReply("Cancelled.", buttons=alert_buttons(request_id))
        if card.state != "waiting":
            return CardReply(f"Not waiting any more ({card.state}).", done=True)
        if action == "no":
            self.desk.skip(request_id)
            return CardReply("Skipped.", note="\u23ed <b>Skipped.</b> Nothing was sent.", done=True)
        if action == "ok" and self.desk.real_orders():
            return CardReply("This would be a REAL order.", buttons=real_buttons(request_id))
        if action in ("ok", "yes"):
            if action == "yes":
                card.set_real(True)  # the second press is the confirmation of a real order
            if self.desk.send(request_id):
                text = "\u2705 <b>Approved.</b> The engine checks the price, the spread and "
                text += "the limits again, then places the order."
                return CardReply("Approved.", note=text, done=True)
            why = first_line(card.status.text()) or "Not sent."
            return CardReply(why, done=card.state != "waiting")
        return CardReply("Unknown button.")
