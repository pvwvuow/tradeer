"""The Signal desk in the AI Lab chat (docs/SIGNAL_DESK.md 2.4 and 5, phase 21a3).

`LabDesk` sits in front of the AI Lab's composer: a pasted trade signal ("XAUUSD sell limit
2361 SL 2368 TP 2355 TP 2348") becomes an order card (`app.ui.signal_card`) instead of an AI
question, and goes to the signal pipeline (`submit`), which plans it against the live price,
sizes it with the risk manager and runs every filter on one leg per target. Questions about
a trade ("why did my EURUSD buy lose?") still go to the AI.

Nothing is sent until the user holds Hold to send: then every waiting leg goes to the
pipeline's approval queue (`approve`), where the execution engine checks the price, the
spread and the limits again. Skip dismisses the legs. The AI never sends an order.

The full check (phase 21b) starts by itself once a card waits for you: in a worker thread it
loads 2 years of the symbol's M15 history (the Backtest page's loader and cache) and counts
how often the same geometry reached each target before the stop loss
(`app.signals.base_rate`). One check runs at a time. With it come the context lines
(`app.engine.desk_context`): the market watch's analysis card of the symbol, the next
high-impact news and the bot's open trades on the same currencies (phase 21b2). When the
full check is over and the AI connection is on, the AI writes a three-sentence note from the
card's lines only (`app.ai.desk_note`, phase 21b3); it has no way to send an order.

The page's header shows the trading mode (PAPER, SEMI-AUTO, AUTO) and REAL ORDERS: ONLY
WITH YOUR CONFIRM when a confirmed order would be real (Semi-auto or Auto on a real account).
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Protocol

from PySide6.QtCore import QObject, Qt, Signal

from app.ai.desk_note import NOTE_TOKENS, clean_note, note_messages
from app.ai.transport import AiCallError, AiClient, Message, plain_text
from app.backtest.service import BacktestRequest
from app.domain.modes import OperatingMode
from app.domain.signals import SignalState
from app.engine.desk_context import desk_context
from app.engine.market_watch import MarketSnapshot
from app.engine.signal_desk import DeskRequest, new_request
from app.engine.signal_pipeline import SignalsSnapshot
from app.observability.logger import audit
from app.signals.base_rate import HISTORY_DAYS, TIMEFRAME, BaseRate, Geometry, base_rate
from app.signals.parse import ParsedSignal, parse
from app.signals.prefilter import looks_like_signal
from app.ui.ai_lab_page import AiLabPage
from app.ui.backtest_page import BacktestContext
from app.ui.lab_parts import apply_tree
from app.ui.signal_card import NO_ANALYSIS, NO_HISTORY, OrderCard, order_words

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


def _quiet_note(text: str) -> None:
    return None


def history_rate(
    backtest: BacktestContext,
    symbol: str,
    geometry: Geometry,
    today: datetime | None = None,
) -> BaseRate:
    """The base rate of `geometry` on the last 2 years of `symbol`'s M15 bars."""
    day = (today or datetime.now(UTC)).date()
    request = BacktestRequest(
        symbol=symbol,
        start=day - timedelta(days=HISTORY_DAYS),
        end=day,
        monte_carlo_runs=0,
    )
    history, _notes = backtest.load(request, _quiet_note)
    bars = history.bars.get(TIMEFRAME)
    if bars is None or not len(bars):
        raise ValueError(f"no {TIMEFRAME} history of {symbol}")
    spec = history.spec
    point = spec.point if spec.point > 0 else 10.0**-spec.digits
    return base_rate(bars, geometry, point)


class LabDesk(QObject):
    snapshot = Signal(object)  # from the analysis thread, shown in the UI thread
    full_done = Signal(str, object)  # a request id and its BaseRate, or why it failed
    note_done = Signal(str, str, bool)  # a request id, the note or why not, and if it worked

    def __init__(
        self,
        page: AiLabPage,
        desk: Desk,
        real_account: Callable[[], bool] = _real_account,
        market: Callable[[], MarketSnapshot | None] | None = None,
    ) -> None:
        super().__init__(page)
        self.page = page
        self.desk = desk
        self.real_account = real_account
        self.market = market
        self.word = order_words(page.persian)
        self.cards: dict[str, OrderCard] = {}
        self.last: SignalsSnapshot | None = None
        self.checked: set[str] = set()  # cards whose full check has started
        self._full_lock = threading.Lock()
        self.noted: set[str] = set()  # cards whose AI note was asked for
        self._next = page.chat.intercept
        page.chat.intercept = self.intercept
        self.snapshot.connect(self.show_snapshot, Qt.ConnectionType.QueuedConnection)
        self.full_done.connect(self.show_full, Qt.ConnectionType.QueuedConnection)
        self.note_done.connect(self.show_note, Qt.ConnectionType.QueuedConnection)
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
        card.full.connect(partial(self.full_check, request.id, True))
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
            if card.state == "waiting" and key not in self.checked:
                self.full_check(key)

    # The full check ----------------------------------------------------------------------
    def full_check(self, request_id: str, again: bool = False) -> bool:
        """Start the card's full check in a worker thread (True when it started)."""
        card = self.cards.get(request_id)
        if card is None or card.full_state == "running":
            return False
        if request_id in self.checked and not again:
            return False
        self.checked.add(request_id)
        self.update_context(card)
        result = card.result
        context = self.page.context
        backtest = context.backtest if context is not None else None
        plan = result.plan if result is not None else None
        geometry = None
        if plan is not None and card.legs:
            geometry = Geometry.of(plan, card.legs[0].atr, time.time())
        if backtest is None or plan is None or geometry is None:
            card.full_note(self.word(NO_HISTORY))
            self.ask_note(request_id)
            return False
        card.full_running()
        threading.Thread(
            target=self._full_work,
            args=(request_id, backtest, plan.symbol, geometry),
            name="signal-desk-full-check",
            daemon=True,
        ).start()
        return True

    def update_context(self, card: OrderCard) -> bool:
        """The card's context lines from the market watch and the open trades."""
        result = card.result
        plan = result.plan if result is not None else None
        if plan is None or self.market is None:
            return False
        market: MarketSnapshot | None = None
        with contextlib.suppress(Exception):
            market = self.market()
        lines = desk_context(
            plan.symbol,
            plan.direction,
            analysis=market.analyses.get(plan.symbol) if market is not None else None,
            events=market.events if market is not None else (),
            records=self.last.signals if self.last is not None else (),
            own=card.leg_ids,
            now=time.time(),
        )
        card.show_context(lines)
        return True

    def _full_work(
        self,
        request_id: str,
        backtest: BacktestContext,
        symbol: str,
        geometry: Geometry,
    ) -> None:
        try:
            with self._full_lock:  # one at a time: the history loads are heavy
                found: BaseRate | str = history_rate(backtest, symbol, geometry)
        except Exception as error:
            found = f"{type(error).__name__}: {error}"
            self._log(f"Signal desk: full check of {symbol} failed: {found}")
        with contextlib.suppress(RuntimeError):  # the page may be gone
            self.full_done.emit(request_id, found)

    def show_full(self, request_id: str, outcome: object) -> None:
        card = self.cards.get(request_id)
        if card is not None and isinstance(outcome, BaseRate | str):
            card.show_full(outcome)
            self.ask_note(request_id)

    # The AI note -------------------------------------------------------------------------
    def ask_note(self, request_id: str) -> bool:
        """Ask the AI for the card's note once (True when it was asked)."""
        card = self.cards.get(request_id)
        if card is None or request_id in self.noted or card.result is None:
            return False
        found = self.page.llm_panel.make_client()
        if isinstance(found, str):
            return False  # the AI connection is off: the card has no note
        self.noted.add(request_id)
        messages = note_messages(card.parsed.text, card.note_facts(), persian=self.page.persian)
        card.note_running()
        threading.Thread(
            target=self._note_work,
            args=(request_id, found, messages),
            name="signal-desk-note",
            daemon=True,
        ).start()
        return True

    def _note_work(self, request_id: str, client: AiClient, messages: list[Message]) -> None:
        try:
            text = clean_note(client.complete(messages, max_tokens=NOTE_TOKENS).text)
            ok = bool(text)
        except AiCallError as error:
            text, ok = plain_text(error, client.settings.base_url), False
        except Exception as error:
            text, ok = f"{type(error).__name__}: {error}", False
        with contextlib.suppress(RuntimeError):  # the page may be gone
            self.note_done.emit(request_id, text, ok)

    def show_note(self, request_id: str, text: str, ok: bool) -> None:
        card = self.cards.get(request_id)
        if card is not None:
            card.show_note(text, ok)

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
