"""Telegram channel messages to the AI Lab (docs/SIGNAL_DESK.md 3.3 to 3.5, phase 21d).

The reader stores every new message of an on channel and hands it here in its own thread;
a Qt signal brings it to the UI thread. There the prefilter says whether it is a signal, the
channel's settings (`app.channels.policy`) say what to do with it: in **Live** it becomes an
order card in the AI Lab (`LabDesk.take`), booked to the channel's own magic and sized with
its budget; in **Paper trial** it is only counted. Nothing here can send an order: every
card waits for your hold-to-confirm like a pasted one, and a channel's text is never an
instruction to the app or the AI.

Phase 21d2 and 21e: every parsed signal is saved (`tg_signals`) and followed as a shadow
trade in a worker thread every 10 minutes; the same signal from a second channel within 15
minutes joins the first card instead of making a new one; an update ("close now", "move SL
to entry", "new SL") changes the shadow and, for a signal you took, shows a follow-up card
that needs one hold; "give Gold Room 100 dollars" in the chat shows a settings card that
needs one hold too.
"""

from __future__ import annotations

import contextlib
import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Protocol

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from app.analysis.bars import Bars
from app.backtest.service import BacktestRequest
from app.channels.chat_settings import Change, read_change
from app.channels.folder import ChannelMessage, preview
from app.channels.policy import (
    Action,
    ChannelMode,
    Route,
    budget_state,
    channel_budget,
    route,
)
from app.channels.reader import ChannelReader
from app.channels.shadow_follow import BarSource, follow_signals
from app.channels.stats import Card, duplicate_of
from app.channels.updates import LINK_SECONDS, Earlier, Update, UpdateKind, link, read_update
from app.domain.signals import Direction, SignalState
from app.observability.logger import audit
from app.risk.budget import budget_features
from app.signals.prefilter import Kind, classify
from app.signals.shadow import TIMEFRAMES
from app.signals.words import resolve_symbol
from app.storage.channel_store import ChannelRepository, ChannelSignal, ChannelSource
from app.strategies.manual_signal import channel_strategy
from app.ui.backtest_page import BacktestContext
from app.ui.channel_cards import FollowUpCard, HoldCard, SettingsCard
from app.ui.lab_desk import CardSource, LabDesk
from app.ui.lab_parts import apply_tree

KEEP_SEEN = 50
SHADOW_MS = 10 * 60 * 1000  # the shadow results are brought up to date this often
FIRST_SHADOW_MS = 60 * 1000
WAITING = frozenset({SignalState.PENDING_APPROVAL})
ON_THEIR_WAY = frozenset({SignalState.SENT, SignalState.FILLED, SignalState.MANAGED})

Log = Callable[[str, str], None]


class Position(Protocol):
    @property
    def mode(self) -> str: ...

    @property
    def ticket(self) -> int: ...

    @property
    def pending(self) -> bool: ...

    @property
    def signal_id(self) -> str: ...


class Positions(Protocol):
    @property
    def positions(self) -> Sequence[Position]: ...


class Engine(Protocol):
    """The execution engine as a follow-up uses it (`ExecutionEngine`)."""

    @property
    def snapshot(self) -> Positions: ...

    def request_close(self, mode: str, ticket: int) -> None: ...

    def request_stop(self, mode: str, ticket: int, sl: float | None = None) -> None: ...


def _quiet_note(text: str) -> None:
    return None


def history_bars(
    backtest: BacktestContext,
    symbol: str,
    start: float,
    end: float,
) -> tuple[Bars, float] | None:
    """The finest history of `symbol` between two moments (the Backtest page's loader and
    cache), and its point."""
    first = datetime.fromtimestamp(start, UTC).date() - timedelta(days=1)
    last = max(first, datetime.fromtimestamp(end, UTC).date())
    request = BacktestRequest(symbol=symbol, start=first, end=last, monte_carlo_runs=0)
    history, _notes = backtest.load(request, _quiet_note)
    for timeframe in TIMEFRAMES:
        bars = history.bars.get(timeframe)
        if bars is not None and len(bars):
            spec = history.spec
            return bars, spec.point if spec.point > 0 else 10.0**-spec.digits
    return None


def _quiet(level: str, message: str) -> None:
    return None


def day_start(now: float) -> float:
    moment = datetime.fromtimestamp(now, UTC)
    return datetime(moment.year, moment.month, moment.day, tzinfo=UTC).timestamp()


@dataclass(frozen=True)
class Seen:
    """One message the feed looked at, for the Channels page."""

    channel: str
    at: float
    text: str  # a one-line preview
    action: Action
    reason: str


@dataclass
class ChannelCounts:
    cards: int = 0
    paper: int = 0
    skipped: int = 0


class ChannelFeed(QObject):
    arrived = Signal(object)  # a ChannelMessage, from the reader thread
    followed = Signal(int)  # shadow legs brought up to date (the worker thread)

    def __init__(
        self,
        desk: LabDesk,
        repository: ChannelRepository,
        *,
        engine: Engine | None = None,
        bars: BarSource | None = None,
        log: Log = _quiet,
        utc_now: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(desk)
        self.desk = desk
        self.repository = repository
        self.engine = engine
        self.bars = bars
        self.log = log
        self.now = utc_now
        self.seen: list[Seen] = []
        self.counts: dict[int, ChannelCounts] = {}
        self.live: list[Card] = []  # the newest cards, for the duplicate check
        self.sources: dict[str, list[str]] = {}  # a card's request id -> its channels
        self.extras: list[HoldCard] = []  # settings and follow-up cards in the chat
        self._shadow_lock = threading.Lock()
        self._next = desk.page.chat.intercept
        desk.page.chat.intercept = self.intercept
        self.arrived.connect(self.handle, Qt.ConnectionType.QueuedConnection)
        self.timer = QTimer(self)
        self.timer.setInterval(SHADOW_MS)
        self.timer.timeout.connect(self.follow_shadow)

    def attach(self, reader: ChannelReader) -> None:
        reader.add_message_listener(self._from_reader)
        self.timer.start()
        QTimer.singleShot(FIRST_SHADOW_MS, self.follow_shadow)

    def _from_reader(self, message: ChannelMessage) -> None:
        with contextlib.suppress(RuntimeError):  # the window may be closing
            self.arrived.emit(message)

    # A message --------------------------------------------------------------------------
    def handle(self, message: object) -> Route | None:
        """Slot (UI thread): one stored message of an on channel."""
        if not isinstance(message, ChannelMessage):
            return None
        source = self.repository.source(message.channel_id)
        if source is None or not source.enabled:
            return None
        policy = self.repository.policy(source.channel_id)
        symbols = self.desk.symbols()
        kind = classify(message.text, symbols, policy.aliases, reply=message.reply_to is not None)
        if kind is Kind.UPDATE:
            self.follow_up(source, message, symbols, policy.aliases)
        symbol = self._symbol(message.text, symbols, policy.aliases)
        now = self.now()
        state = budget_state(policy, self.repository.money(source.magic, day_start(now)))
        found = route(policy, kind, symbol, state, symbols)
        if found.back_to_paper:
            self.repository.set_policy(
                source.channel_id,
                policy.model_copy(update={"mode": ChannelMode.PAPER}),
                now,
            )
            self.log("WARNING", f"Telegram channel {source.title}: {found.reason}")
        counts = self.counts.setdefault(source.channel_id, ChannelCounts())
        request_id = ""
        parsed = self.desk.parse_text(message.text, policy.aliases)
        if found.action is Action.CARD:
            twin = self._twin(source, symbol, parsed.direction, parsed.entry, now)
            if twin is not None:
                found = Route(Action.PAPER, f"the same signal as {twin.channel}'s card")
                self._join(twin, source.title)
                request_id = twin.key
            else:
                counts.cards += 1
                features = budget_features(channel_budget(policy, state))
                origin = CardSource(
                    source=channel_strategy(source.magic),
                    label=source.title,
                    strategy=channel_strategy(source.magic),
                    features=features,
                    max_open=policy.max_open,
                    aliases=dict(policy.aliases),
                    quick=True,
                    market_minutes=policy.market_minutes,
                    pending_minutes=policy.pending_minutes,
                )
                card = self.desk.take(message.text, origin)
                request_id = card.request_id
                found_entry = parsed.entry
                self._remember_card(request_id, source, symbol, parsed.direction, found_entry, now)
        if found.action is Action.PAPER:
            counts.paper += 1
        elif found.action is Action.SKIP:
            counts.skipped += 1
        if kind is Kind.SIGNAL and parsed.direction is not None:
            self.repository.save_signal(
                ChannelSignal(
                    channel_id=source.channel_id,
                    message_id=message.message_id,
                    date=message.date,
                    symbol=symbol,
                    direction=parsed.direction.value,
                    action=found.action.value,
                    reason=found.reason,
                    request_id=request_id,
                    latency=max(0.0, now - message.date),
                ),
                now,
            )
        if kind is not Kind.NOISE:
            self.log("INFO", f"Telegram channel {source.title}: {found.reason}")
        self._remember(source, message, found)
        return found

    def _symbol(self, text: str, symbols: tuple[str, ...], aliases: dict[str, str]) -> str:
        parsed = self.desk.parse_text(text, aliases)
        if parsed.symbol:
            return resolve_symbol(parsed.symbol, symbols, aliases) or parsed.symbol
        return ""

    def _remember(self, source: ChannelSource, message: ChannelMessage, found: Route) -> None:
        seen = Seen(source.title, message.date, preview(message.text), found.action, found.reason)
        self.seen = [seen, *self.seen][:KEEP_SEEN]

    def count(self, channel_id: int) -> ChannelCounts:
        return replace(self.counts.get(channel_id, ChannelCounts()))

    # Duplicates (docs/SIGNAL_DESK.md 3.7) -------------------------------------------------
    def _entry(self, entry: Sequence[float]) -> float:
        return sum(entry) / len(entry) if entry else math.nan

    def _twin(
        self,
        source: ChannelSource,
        symbol: str,
        direction: Direction | None,
        entry: Sequence[float],
        now: float,
    ) -> Card | None:
        if direction is None:
            return None
        self.live = [card for card in self.live if now - card.at <= 3600]
        new = Card("", source.title, symbol, direction, self._entry(entry), now)
        for card in self.live:
            mine, theirs = new, card
            if math.isnan(card.entry) and math.isnan(new.entry):  # two market signals
                mine, theirs = replace(new, entry=0.0), replace(card, entry=0.0)
            if duplicate_of(mine, [theirs], self._atr(card.key)) is not None:
                return card
        return None

    def _atr(self, request_id: str) -> float:
        order = self.desk.cards.get(request_id)
        if order is None or not order.legs:
            return math.nan
        return order.legs[0].atr

    def _remember_card(
        self,
        request_id: str,
        source: ChannelSource,
        symbol: str,
        direction: Direction | None,
        entry: Sequence[float],
        now: float,
    ) -> None:
        if direction is None:
            return
        self.live.append(Card(request_id, source.title, symbol, direction, self._entry(entry), now))
        self.sources[request_id] = [source.title]

    def _join(self, twin: Card, title: str) -> None:
        names = self.sources.setdefault(twin.key, [twin.channel])
        if title not in names:
            names.append(title)
        card = self.desk.cards.get(twin.key)
        if card is not None:
            card.note.setText(" + ".join(names))
            card.note.setVisible(True)

    # Follow-ups (docs/SIGNAL_DESK.md 3.6) -------------------------------------------------
    def follow_up(
        self,
        source: ChannelSource,
        message: ChannelMessage,
        symbols: tuple[str, ...],
        aliases: dict[str, str],
    ) -> Update | None:
        update = read_update(message.text, symbols, aliases)
        if update is None:
            return None
        signals = self.repository.signals(source.channel_id, message.date - LINK_SECONDS)
        earlier = [
            Earlier(
                signal.message_id,
                signal.date,
                signal.symbol,
                Direction(signal.direction) if signal.direction else None,
            )
            for signal in signals
        ]
        found = link(update, message.reply_to, message.date, earlier)
        if found is None:
            return update
        if update.kind in (UpdateKind.CLOSE, UpdateKind.CANCEL):
            self.repository.mark_signal(
                source.channel_id,
                found.message_id,
                "close_at",
                message.date,
            )
        elif update.kind is UpdateKind.BREAK_EVEN:
            self.repository.mark_signal(
                source.channel_id,
                found.message_id,
                "break_even_at",
                message.date,
            )
        signal = next(item for item in signals if item.message_id == found.message_id)
        if update.acts and signal.request_id and signal.request_id in self.desk.cards:
            self.follow_up_card(source.title, preview(message.text), update, signal.request_id)
        return update

    def follow_up_card(
        self,
        title: str,
        text: str,
        update: Update,
        request_id: str,
    ) -> FollowUpCard | None:
        order = self.desk.cards.get(request_id)
        if order is None:
            return None
        last = self.desk.last
        records = {record.id: record for record in (last.signals if last is not None else ())}
        legs = [records[leg] for leg in order.leg_ids if leg in records]
        waiting = [leg.id for leg in legs if leg.signal.state in WAITING]
        moving = {leg.id for leg in legs if leg.signal.state in ON_THEIR_WAY}
        positions: list[Position] = []
        if self.engine is not None and moving:
            positions = [p for p in self.engine.snapshot.positions if p.signal_id in moving]
        steps: list[Callable[[], None]] = []
        actions: list[str] = []
        kind = update.kind
        if kind in (UpdateKind.CLOSE, UpdateKind.CANCEL):
            for leg in waiting:
                steps.append(partial(self.desk.desk.dismiss, leg))
            if waiting:
                actions.append(f"skip {len(waiting)} order(s) that still wait for you")
            for position in positions:
                assert self.engine is not None
                steps.append(partial(self.engine.request_close, position.mode, position.ticket))
            if positions:
                actions.append(f"close or cancel {len(positions)} bot trade(s)")
        elif kind in (UpdateKind.BREAK_EVEN, UpdateKind.NEW_SL):
            price = update.price if kind is UpdateKind.NEW_SL else None
            opened = [position for position in positions if not position.pending]
            for position in opened:
                assert self.engine is not None
                move = partial(self.engine.request_stop, position.mode, position.ticket, price)
                steps.append(move)
            if opened:
                where = "the entry" if price is None else f"{price:g}"
                count = len(opened)
                actions.append(f"move the stop loss of {count} trade(s) to {where} (never wider)")
        card = FollowUpCard(title, text, actions, fa=self.desk.page.persian)
        if not actions:
            card.status.setText("Nothing to do here: change it on the Positions page if you want.")
        card.held.connect(partial(self._apply, card, steps, text))
        self._show(card)
        return card

    def _apply(self, card: FollowUpCard, steps: list[Callable[[], None]], text: str) -> None:
        if card.done:
            return
        for step in steps:
            step()
        audit("channel follow-up confirmed", before=text, after=f"{len(steps)} step(s)")
        card.finish(card.word("Done: sent to the engine."))

    # Settings in the chat (docs/SIGNAL_DESK.md 3.4) ---------------------------------------
    def intercept(self, text: str) -> bool:
        sources = [channel for channel in self.repository.channels() if channel.in_folder]
        change = read_change(text, [channel.title for channel in sources])
        if change is None:
            return self._next(text)
        source = next(channel for channel in sources if channel.title == change.title)
        self.desk.page.chat.add_bubble(text.strip())
        self.settings_card(source, change)
        return True

    def settings_card(self, source: ChannelSource, change: Change) -> SettingsCard:
        policy = self.repository.policy(source.channel_id)
        old, new = change.old_new(policy)
        card = SettingsCard(source.title, change.field, old, new, fa=self.desk.page.persian)
        card.held.connect(partial(self._save, card, source, change))
        self._show(card)
        return card

    def _save(self, card: SettingsCard, source: ChannelSource, change: Change) -> bool:
        if card.done:
            return False
        policy = self.repository.policy(source.channel_id)
        try:
            updated = change.apply(policy)
        except ValueError:
            card.finish("Not saved: the value is out of range.")
            return False
        if updated.mode is ChannelMode.LIVE and updated.budget <= 0:
            card.finish("Not saved: Live needs a budget first (give the channel money).")
            return False
        self.repository.set_policy(source.channel_id, updated, self.now())
        old, new = change.old_new(policy)
        audit("telegram channel setting", before=f"{source.title} {change.field} {old}", after=new)
        card.finish(card.word("Saved."))
        return True

    def _show(self, card: HoldCard) -> None:
        tokens = self.desk.page.tokens
        apply_tree(card, tokens)
        card.apply_tokens(tokens)
        self.desk.page.chat.add_extra(card)
        self.extras.append(card)

    # Shadow results (docs/SIGNAL_DESK.md 3.5) ---------------------------------------------
    def bar_source(self) -> BarSource | None:
        if self.bars is not None:
            return self.bars
        context = self.desk.page.context
        backtest = context.backtest if context is not None else None
        if backtest is None:
            return None
        return partial(history_bars, backtest)

    def follow_shadow(self) -> bool:
        """Bring the shadow results up to date in a worker thread (True when it started)."""
        source = self.bar_source()
        if source is None or self._shadow_lock.locked():
            return False
        threading.Thread(
            target=self._shadow_work,
            args=(source,),
            name="channel-shadow",
            daemon=True,
        ).start()
        return True

    def _shadow_work(self, source: BarSource) -> None:
        with self._shadow_lock:
            try:
                saved = follow_signals(self.repository, source, self.now(), self.log)
            except Exception as error:
                saved = 0
                self.log("WARNING", f"Shadow results failed: {type(error).__name__}")
            finally:
                with contextlib.suppress(Exception):
                    self.repository.store.db.release()  # this thread's database connection
        with contextlib.suppress(RuntimeError):
            self.followed.emit(saved)
