"""The Signal desk in the AI Lab chat (docs/SIGNAL_DESK.md 2.4 and 5, phase 21a3).

`LabDesk` sits in front of the AI Lab's composer: a pasted trade signal ("XAUUSD sell limit
2361 SL 2368 TP 2355 TP 2348") becomes an order card (`app.ui.signal_card`) instead of an AI
question, and goes to the signal pipeline (`submit`), which plans it against the live price,
sizes it with the risk manager and runs every filter on one leg per target. Questions about
a trade ("why did my EURUSD buy lose?") still go to the AI.

Nothing is sent until the user holds Hold to send: then every waiting leg goes to the
pipeline's approval queue (`approve`), where the execution engine checks the price, the
spread and the limits again. Skip dismisses the legs. The AI never sends an order.

The page's header shows the trading mode (PAPER, SEMI-AUTO, AUTO) and REAL ORDERS: ONLY
WITH YOUR CONFIRM when a confirmed order would be real (Semi-auto or Auto on a real account).
"""

from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Callable
from functools import partial
from typing import Protocol

from PySide6.QtCore import QObject, Qt, Signal

from app.domain.modes import OperatingMode
from app.domain.signals import SignalState
from app.engine.signal_desk import DeskRequest, new_request
from app.engine.signal_pipeline import SignalsSnapshot
from app.observability.logger import audit
from app.signals.parse import ParsedSignal, parse
from app.signals.prefilter import looks_like_signal
from app.ui.ai_lab_page import AiLabPage
from app.ui.lab_parts import apply_tree
from app.ui.signal_card import NO_ANALYSIS, OrderCard, order_words

QUESTION = re.compile(r"[?\u061f]|\bwhy\b|چرا", re.IGNORECASE)
NEVER_TAG = "REAL ORDERS: NEVER"
CONFIRM_TAG = "REAL ORDERS: ONLY WITH YOUR CONFIRM"


class Desk(Protocol):
    """The signal pipeline as the Signal desk uses it (`SignalPipeline`)."""

    @property
    def snapshot(self) -> SignalsSnapshot: ...

    def add_listener(self, listener: Callable[[SignalsSnapshot], None]) -> None: ...

    def submit(self, request: DeskRequest) -> None: ...

    def approve(self, signal_id: str) -> None: ...

    def dismiss(self, signal_id: str) -> None: ...


def _real_account() -> bool:
    return True  # unknown: a confirmed order in Semi-auto or Auto counts as real


def is_signal(text: str, symbols: tuple[str, ...] = ()) -> bool:
    """A pasted trade signal (a symbol, a side and prices), not a question about one and
    not an AI answer (JSON)."""
    if QUESTION.search(text) or "{" in text:
        return False
    return looks_like_signal(text, symbols) or (bool(symbols) and looks_like_signal(text))


class LabDesk(QObject):
    snapshot = Signal(object)  # from the analysis thread, shown in the UI thread

    def __init__(
        self,
        page: AiLabPage,
        desk: Desk,
        real_account: Callable[[], bool] = _real_account,
    ) -> None:
        super().__init__(page)
        self.page = page
        self.desk = desk
        self.real_account = real_account
        self.word = order_words(page.persian)
        self.cards: dict[str, OrderCard] = {}
        self.last: SignalsSnapshot | None = None
        self._next = page.chat.intercept
        page.chat.intercept = self.intercept
        self.snapshot.connect(self.show_snapshot, Qt.ConnectionType.QueuedConnection)
        desk.add_listener(self.snapshot.emit)
        self.show_snapshot(desk.snapshot)

    # The mode ----------------------------------------------------------------------------
    def trading_mode(self) -> OperatingMode | None:
        context = self.page.context
        return context.execution.mode if context is not None else None

    def real_orders(self) -> bool:
        """A confirmed order would be real: Semi-auto or Auto on a real account."""
        mode = self.trading_mode()
        return mode is not None and mode.places_real_orders and self.real_account()

    def update_tags(self) -> None:
        """The page's header: the trading mode, and whether a real order can go out."""
        mode = self.trading_mode()
        label = mode.label.upper() if mode is not None else "PAPER"
        tag = self.page.paper_tag
        if tag.text != label:
            tag.set(label)
        text, tone = (CONFIRM_TAG, "loss") if self.real_orders() else (NEVER_TAG, "neutral")
        tag = self.page.never_tag
        if (tag.text, tag.tone) != (text, tone):
            tag.set(text, tone)

    # The composer ------------------------------------------------------------------------
    def symbols(self) -> tuple[str, ...]:
        return self.desk.snapshot.symbols

    def intercept(self, text: str) -> bool:
        clean = text.strip()
        if is_signal(clean, self.symbols()):
            self.page.chat.add_bubble(clean)
            self.take(clean)
            return True
        return self._next(text)

    def parse_text(self, text: str) -> ParsedSignal:
        symbols = self.symbols()
        parsed = parse(text, symbols)
        if not parsed.symbol and symbols:
            parsed = parse(text)  # not on the watchlist: the desk says so on the card
        return parsed

    def take(self, text: str) -> OrderCard:
        """An order card for a pasted signal, handed to the desk (nothing is sent)."""
        parsed = self.parse_text(text)
        card = OrderCard(parsed, self.word, real=self.real_orders())
        request = new_request(parsed, time.time())
        card.request_id = request.id
        self.cards[request.id] = card
        card.edit.connect(partial(self.page.chat.set_text, parsed.text))
        card.held.connect(partial(self.send, request.id))
        card.skip.connect(partial(self.skip, request.id))
        card.destroyed.connect(partial(self._forget, request.id))
        tokens = self.page.tokens
        apply_tree(card, tokens)
        card.apply_tokens(tokens)
        self.page.chat.add_extra(card)
        self.desk.submit(request)
        self._log(f"Signal desk: pasted {parsed.summary()}")
        return card

    def _forget(self, request_id: str, *_: object) -> None:
        self.cards.pop(request_id, None)

    def _log(self, message: str) -> None:
        context = self.page.context
        if context is not None:
            context.log("INFO", message)

    def _currency(self) -> str:
        context = self.page.context
        if context is None:
            return ""
        with contextlib.suppress(Exception):
            return context.currency()
        return ""

    # Snapshots ---------------------------------------------------------------------------
    def show_snapshot(self, snapshot: object) -> None:
        """Slot: a signals snapshot for the order cards and the header's tags."""
        if not isinstance(snapshot, SignalsSnapshot):
            return
        self.last = snapshot
        self.update_tags()
        if not self.cards:
            return
        results = {result.request_id: result for result in snapshot.desk}
        records = {record.id: record for record in snapshot.signals}
        real = self.real_orders()
        currency = self._currency()
        for key, card in list(self.cards.items()):
            result = results.get(key)
            ids = result.signal_ids if result is not None else card.leg_ids
            legs = [records[signal_id] for signal_id in ids if signal_id in records]
            note = ""
            if result is not None and not result.ok and not snapshot.symbols:
                note = self.word(NO_ANALYSIS)
            if card.state in ("checking", "waiting"):
                card.set_real(real)
            card.show_result(
                result,
                legs,
                snapshot.approval_block,
                currency=currency,
                note=note,
            )

    # The hold and Skip -------------------------------------------------------------------
    def send(self, request_id: str) -> bool:
        """The hold is complete: every waiting leg goes to the approval queue, where the
        engine checks the price, the spread and the limits again before it sends."""
        card = self.cards.get(request_id)
        if card is None or card.state != "waiting":
            return False
        if self.real_orders() and not card.real:
            card.set_real(True)
            card.warn_real()
            return False
        block = self.last.approval_block if self.last is not None else "no signals yet"
        if block:
            card.show_result(None, card.legs, block)
            return False
        waiting = [leg for leg in card.legs if leg.signal.state is SignalState.PENDING_APPROVAL]
        if not waiting:
            return False
        for leg in waiting:
            self.desk.approve(leg.id)
            audit("signal approved", before=leg.signal.state.value, after=leg.id)
        card.mark_sent()
        self._log(f"Signal desk: {len(waiting)} legs confirmed with a hold")
        return True

    def skip(self, request_id: str) -> bool:
        card = self.cards.get(request_id)
        if card is None:
            return False
        for leg in card.legs:
            if leg.signal.state is SignalState.PENDING_APPROVAL:
                self.desk.dismiss(leg.id)
                audit("signal dismissed", before=leg.signal.state.value, after=leg.id)
        card.mark_skipped()
        return True
