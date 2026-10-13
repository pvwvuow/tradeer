"""Telegram channel messages to the AI Lab (docs/SIGNAL_DESK.md 3.3 to 3.5, phase 21d).

The reader stores every new message of an on channel and hands it here in its own thread;
a Qt signal brings it to the UI thread. There the prefilter says whether it is a signal, the
channel's settings (`app.channels.policy`) say what to do with it: in **Live** it becomes an
order card in the AI Lab (`LabDesk.take`), booked to the channel's own magic and sized with
its budget; in **Paper trial** it is only counted. Nothing here can send an order: every
card waits for your hold-to-confirm like a pasted one, and a channel's text is never an
instruction to the app or the AI.

0.44.0: a half signal ("gold buy now", then "SL 4180 TP 4205"; "wait, I will complete it";
an edit of the first message) waits in the channel's draft and goes on only when it is
whole (`app.channels.drafts`); `noted` tells the chat. A message the parser cannot read
(three prices and a symbol, but no whole signal) goes once to the AI with the channel's last
messages (`app.channels.reading`, at most 40 a day); its answer counts only when the checks
pass, and then goes the normal way. A result claim ("+120 pips today") is never a signal.
0.44.1: a message with a picture and no whole signal in its text goes to the AI with the
picture; the card then says the numbers came from a picture. A model that refuses pictures
gets none for 6 hours (`PICTURE_PAUSE`), so the daily readings are not spent on it.
0.47.0 (phase 22b): every channel signal is also told to the Telegram bot (`alerts`): one
that became a card when the desk first answered, one that did not (a Paper trial, a
duplicate, a refusal) at once; see `app.ui.bot_cards`.
0.47.0: every reading is logged with what the AI answered, the time and the tokens, and its
tokens and cost go to the AI Lab's usage (kind `channel`) and to the Telegram signals tab.
0.48.1 (phase 22h): a draft of three messages that is still incomplete goes to the AI too;
a message that cannot go to the AI (no connection, the daily limit used) says why (`cannot`).
0.50.3 (PC log of 12 October 2026): a reading whose symbol is not on the watchlist (AUDUSD,
added a minute later) waits 15 minutes (`SYMBOL_WAIT_SECONDS`); once the symbol is watched
it goes on the normal way, without a second AI call.
0.50.4: a follow-up card obeys the channel's settings (none when the channel is off or its
follow-ups or stop to entry are set to never; the shadow still changes), and a channel's
daily loss stop counts from the broker's trading day (`app.channels.feed_rules`).
0.50.5 (phase 23f): a Live channel with Auto-take on (you turn it on per channel, with one
hold) confirms its waiting card by itself after 10 seconds unless you press Skip; never on a
real account or for numbers read from a picture, at most 5 a day (`app.channels.auto_take`).

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

from app.ai.transport import AiCallError, AiClient, Message, plain_text
from app.ai.usage_store import UsageRecord, short_tokens
from app.analysis.bars import Bars
from app.backtest.service import BacktestRequest
from app.channels.auto_take import AUTO_PER_DAY, AUTO_TAKE_SECONDS, read_auto
from app.channels.chat_settings import Change, read_change
from app.channels.drafts import Draft, Drafts, Step, edit, expired_reason, join, worth_telling
from app.channels.feed_rules import channel_day_start, follow_block
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
from app.channels.reading import (
    CLAIM_REASON,
    CONTEXT_MESSAGES,
    READ_TOKENS,
    SCHEMA,
    ReadCap,
    ReadResult,
    canonical,
    is_claim,
    numbers,
    parse_reply,
    read_messages,
    unclear,
)
from app.channels.shadow_follow import BarSource, follow_signals
from app.channels.stats import Card, duplicate_of
from app.channels.updates import LINK_SECONDS, Earlier, Update, UpdateKind, link, read_update
from app.core.clock import BrokerClock
from app.domain.signals import Direction, SignalState
from app.notify.bot_text import SignalAlert
from app.observability.logger import audit
from app.risk.budget import budget_features
from app.signals.prefilter import Kind, classify
from app.signals.shadow import TIMEFRAMES
from app.signals.words import resolve_symbol
from app.storage.channel_store import ChannelRepository, ChannelSignal, ChannelSource
from app.strategies.manual_signal import channel_strategy
from app.ui.backtest_page import BacktestContext
from app.ui.bot_cards import alert_from_card, alert_from_parsed
from app.ui.channel_cards import FollowUpCard, HoldCard, SettingsCard
from app.ui.lab_desk import CardSource, LabDesk
from app.ui.lab_parts import apply_tree
from app.ui.signal_card import OrderCard

KEEP_SEEN = 50
DRAFT_CHECK_MS = 60 * 1000  # incomplete drafts are expired this often (0.44.0)
SHADOW_MS = 10 * 60 * 1000  # the shadow results are brought up to date this often
FIRST_SHADOW_MS = 60 * 1000
READING = "; the AI reads it now"
KEEP_ASKED = 500
LONG_DRAFT = 3  # a draft of this many messages that is still incomplete goes to the AI
CANNOT_SECONDS = 600.0  # the same "the AI could not read" line is told at most this often
PICTURE_PAUSE = 6 * 3600  # no pictures to a model that refused one
PICTURE_PAUSED = "; pictures go to the AI again in 6 hours"
RAW_CHARS = 400  # characters of the AI's answer in the log
REFUSED_CODES = frozenset({400, 413, 415, 422})  # a refused request, not a network problem
WAITING = frozenset({SignalState.PENDING_APPROVAL})
ON_THEIR_WAY = frozenset({SignalState.SENT, SignalState.FILLED, SignalState.MANAGED})
SYMBOL_WAIT_SECONDS = 15 * 60.0  # a reading waits this long for its symbol (0.50.3)
KEEP_SYMBOL_WAITS = 20

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


@dataclass(frozen=True)
class Reading:
    """The AI's reading of one unclear message, from the worker thread."""

    channel_id: int
    message: ChannelMessage
    texts: tuple[str, ...]  # what the AI saw: the earlier messages, then this one
    outcome: ReadResult | str  # the reading, or why it failed
    picture: bool = False  # the AI read the message's picture too
    refused: bool = False  # the service refused the request with the picture
    model: str = ""
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    cost: float = math.nan  # USD; NaN when no price is set
    seconds: float = 0.0
    raw: str = ""  # the AI's answer as it came, shortened (for the log)


@dataclass
class AiSpend:
    """The AI's readings of channel messages today (UTC), for the Telegram signals tab."""

    day: float = 0.0
    reads: int = 0
    tokens: int = 0
    cost: float = 0.0

    def text(self) -> str:
        cost = f" \u00b7 ${self.cost:.4f}" if self.cost > 0 else ""
        return f"AI today: {self.reads} reading(s) \u00b7 {short_tokens(self.tokens)} tokens{cost}"


def _count(source: object, name: str) -> int:
    value = getattr(source, name, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def spent(reading: Reading, answer: object, seconds: float) -> Reading:
    """The reading with what the call cost: the model, the tokens, the price, the time and
    the answer as it came (an answer without these, as in a test, costs nothing)."""
    usage = getattr(answer, "usage", None)
    cost = getattr(answer, "cost", math.nan)
    model = getattr(answer, "model", "")
    text = getattr(answer, "text", "")
    return replace(
        reading,
        model=model if isinstance(model, str) else "",
        input_tokens=_count(usage, "input_tokens"),
        cached_tokens=_count(usage, "cached_tokens"),
        output_tokens=_count(usage, "output_tokens"),
        cost=float(cost) if isinstance(cost, int | float) and cost > 0 else math.nan,
        seconds=seconds,
        raw=" ".join(text.split())[:RAW_CHARS] if isinstance(text, str) else "",
    )


@dataclass
class ChannelCounts:
    cards: int = 0
    paper: int = 0
    skipped: int = 0


class ChannelFeed(QObject):
    arrived = Signal(object)  # a ChannelMessage, from the reader thread
    edited = Signal(object)  # an edited ChannelMessage, from the reader thread (0.44.0)
    noted = Signal(str, str)  # a channel's title and a line for the chat (drafts, 0.44.0)
    followed = Signal(int)  # shadow legs brought up to date (the worker thread)
    read_done = Signal(object)  # a Reading, from the AI's worker thread

    def __init__(
        self,
        desk: LabDesk,
        repository: ChannelRepository,
        *,
        engine: Engine | None = None,
        bars: BarSource | None = None,
        log: Log = _quiet,
        utc_now: Callable[[], float] = time.time,
        ai: Callable[[], AiClient | str] | None = None,
        usage: Callable[[UsageRecord], None] | None = None,
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
        self.drafts = Drafts()  # half signals waiting for their rest (0.44.0)
        self.ai = ai if ai is not None else self._page_ai
        self.reads = ReadCap()
        self.asked: set[tuple[int, int]] = set()  # (channel, message) read by the AI
        self.cannot_at: dict[str, float] = {}  # why the AI could not read -> when it was told
        self.pictures_off_until = 0.0  # a model that refused a picture gets none until then
        self.symbol_waits: list[tuple[float, Reading]] = []  # readings waiting for a symbol
        self.clock_source: Callable[[], BrokerClock] | None = None  # the broker day (0.50.4)
        self.auto: dict[str, int] = {}  # Auto-take: a card's request id -> its channel
        self.auto_count: dict[tuple[int, float], int] = {}  # (channel, broker day) -> taken
        self.no_auto: set[tuple[int, int]] = set()  # (channel, message) read from a picture
        self.usage = usage if usage is not None else desk.page.record_usage
        self.spend = AiSpend()
        self.alerts: list[Callable[[SignalAlert], None]] = []  # the Telegram bot (0.47.0)
        desk.on_settled.append(self._card_settled)
        self.read_done.connect(self.show_read, Qt.ConnectionType.QueuedConnection)
        self.arrived.connect(self.handle, Qt.ConnectionType.QueuedConnection)
        self.edited.connect(self.handle_edit, Qt.ConnectionType.QueuedConnection)
        self.draft_timer = QTimer(self)
        self.draft_timer.setInterval(DRAFT_CHECK_MS)
        self.draft_timer.timeout.connect(self.expire_drafts)
        self.timer = QTimer(self)
        self.timer.setInterval(SHADOW_MS)
        self.timer.timeout.connect(self.follow_shadow)

    def attach(self, reader: ChannelReader) -> None:
        reader.add_message_listener(self._from_reader)
        reader.add_edit_listener(self._edit_from_reader)
        self.timer.start()
        self.draft_timer.start()
        QTimer.singleShot(FIRST_SHADOW_MS, self.follow_shadow)

    def _from_reader(self, message: ChannelMessage) -> None:
        with contextlib.suppress(RuntimeError):  # the window may be closing
            self.arrived.emit(message)

    def _edit_from_reader(self, message: ChannelMessage) -> None:
        with contextlib.suppress(RuntimeError):
            self.edited.emit(message)

    # A message --------------------------------------------------------------------------
    def handle(self, message: object, *, joined: bool = False) -> Route | None:
        """Slot (UI thread): one stored message of an on channel. A half signal waits in the
        channel's draft for its rest (0.44.0); `joined` is a completed draft's whole text."""
        if not isinstance(message, ChannelMessage):
            return None
        source = self.repository.source(message.channel_id)
        if source is None or not source.enabled:
            return None
        policy = self.repository.policy(source.channel_id)
        symbols = self.desk.symbols()
        text = message.text
        if not joined:
            step = join(
                self.drafts,
                source.channel_id,
                message.message_id,
                text,
                self.now(),
                symbols,
                policy.aliases,
                message.reply_to,
            )
            if step.action in ("open", "wait"):
                extra = ""
                pieces = len(step.draft.texts) if step.draft is not None else 0
                odd = bool(message.photo) or pieces >= LONG_DRAFT
                odd = odd or unclear(step.text, symbols, policy.aliases)
                if odd and self.ask_ai(source, message, step.text, message.photo):
                    extra = READING
                return self._waiting(source, message, step, symbols, policy.aliases, extra)
            text = step.text
            if step.draft is not None:
                count = len(step.draft.texts)
                self.noted.emit(source.title, f"{count} messages joined into one signal")
        kind = classify(text, symbols, policy.aliases, reply=message.reply_to is not None)
        if kind is Kind.NOISE and not joined:
            early = self._unclear(source, message, text, symbols, policy.aliases)
            if early is not None:
                return early
        if kind is Kind.UPDATE:
            self.follow_up(source, message, symbols, policy.aliases)
        symbol = self._symbol(text, symbols, policy.aliases)
        now = self.now()
        since = self.broker_day_start(now)
        state = budget_state(policy, self.repository.money(source.magic, since))
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
        parsed = self.desk.parse_text(text, policy.aliases)
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
                card = self.desk.take(text, origin)
                request_id = card.request_id
                key = (source.channel_id, message.message_id)
                if policy.auto_take and key not in self.no_auto:
                    self.auto[request_id] = source.channel_id
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
        if kind is Kind.SIGNAL and parsed.direction is not None and found.action is not Action.CARD:
            why = found.reason[:1].upper() + found.reason[1:]
            self.tell(alert_from_parsed(parsed, source.title, symbol, why))
        if kind is not Kind.NOISE:
            self.log("INFO", f"Telegram channel {source.title}: {found.reason}")
        self._remember(source, message, found)
        return found

    def broker_day_start(self, now: float) -> float:
        """UTC start of the broker's trading day, for a channel's daily loss stop (0.50.4)."""
        clock: BrokerClock | None = None
        source = self.clock_source
        if source is not None:
            with contextlib.suppress(Exception):
                clock = source()
        return channel_day_start(clock, now)

    # The Telegram bot (phase 22b) ---------------------------------------------------------
    def tell(self, alert: SignalAlert) -> None:
        """A signal to every sink (the bot); a sink that fails stops nothing."""
        for sink in list(self.alerts):
            try:
                sink(alert)
            except Exception as error:
                self.log("WARNING", f"Telegram signal message failed: {type(error).__name__}")

    def _card_settled(self, request_id: str, card: OrderCard) -> None:
        """The desk answered a channel's card for the first time: tell the bot, and start
        the Auto-take countdown when the channel has it on."""
        names = self.sources.get(request_id)
        self.tell(alert_from_card(card, " + ".join(names) if names else "channel"))
        self.auto_take_soon(request_id, card)

    # Auto-take (phase 23f, 0.50.5) ----------------------------------------------------------
    def _title(self, channel_id: int) -> str:
        source = self.repository.source(channel_id)
        return source.title if source is not None else str(channel_id)

    def auto_block(self, channel_id: int) -> str:
        """Why Auto-take may not send now ("" when it may)."""
        source = self.repository.source(channel_id)
        policy = self.repository.policy(channel_id)
        if source is None or not source.enabled or policy.mode is not ChannelMode.LIVE:
            return "the channel is not Live"
        if not policy.auto_take:
            return "Auto-take was turned off"
        if self.desk.real_orders():
            return "a real account always needs your hold"
        day = self.broker_day_start(self.now())
        if self.auto_count.get((channel_id, day), 0) >= AUTO_PER_DAY:
            return f"it took {AUTO_PER_DAY} trades by itself today"
        return ""

    def auto_take_soon(self, request_id: str, card: OrderCard) -> bool:
        """A waiting card of a channel with Auto-take on is confirmed after a short
        countdown unless you press Skip (True when the countdown started)."""
        channel_id = self.auto.get(request_id)
        if channel_id is None:
            return False
        title = self._title(channel_id)
        if card.state != "waiting" or card.block:
            self.auto.pop(request_id, None)
            why = card.block or f"the card is {card.state}"
            self.log("INFO", f"Telegram channel {title}: Auto-take: nothing to send ({why})")
            return False
        why = self.auto_block(channel_id)
        if why:
            self.auto.pop(request_id, None)
            line = f"Auto-take: {why}; the card waits for your hold"
            self.log("INFO", f"Telegram channel {title}: {line}")
            self.noted.emit(title, line)
            return False
        line = f"Auto-take: the card goes in {AUTO_TAKE_SECONDS} s; press Skip to stop it"
        self.log("INFO", f"Telegram channel {title}: {line}")
        self.noted.emit(title, line)
        QTimer.singleShot(AUTO_TAKE_SECONDS * 1000, partial(self.auto_send, request_id))
        return True

    def auto_send(self, request_id: str) -> bool:
        """The countdown ended: confirm the card as your hold would (True when it went)."""
        channel_id = self.auto.pop(request_id, None)
        card = self.desk.cards.get(request_id)
        if channel_id is None or card is None:
            return False
        title = self._title(channel_id)
        why = self.auto_block(channel_id)
        if not why and card.state != "waiting":
            why = f"the card is {card.state}"
        if why:
            self.log("INFO", f"Telegram channel {title}: Auto-take stopped: {why}")
            return False
        if not self.desk.send(request_id):
            self.log("WARNING", f"Telegram channel {title}: Auto-take: the card did not go")
            return False
        day = self.broker_day_start(self.now())
        self.auto_count[(channel_id, day)] = self.auto_count.get((channel_id, day), 0) + 1
        audit("channel auto-take", before=title, after=request_id)
        line = "Auto-take: sent; the engine checks the price, the spread and the limits again"
        self.log("INFO", f"Telegram channel {title}: {line}")
        self.noted.emit(title, line)
        return True

    # Half signals (docs/AI_LAB_V3.md section 1) -------------------------------------------
    def _waiting(
        self,
        source: ChannelSource,
        message: ChannelMessage,
        step: Step,
        symbols: tuple[str, ...],
        aliases: dict[str, str],
        extra: str = "",
    ) -> Route:
        found = Route(Action.SKIP, step.reason() + extra)
        self.log("INFO", f"Telegram channel {source.title}: {found.reason}")
        self._remember(source, message, found)
        if step.draft is not None and worth_telling(step.draft, symbols, aliases):
            self.noted.emit(source.title, found.reason)
        return found

    def handle_edit(self, message: object) -> Route | None:
        """Slot (UI thread): an edited message; it counts when it is part of an open draft."""
        if not isinstance(message, ChannelMessage):
            return None
        source = self.repository.source(message.channel_id)
        if source is None or not source.enabled:
            return None
        policy = self.repository.policy(source.channel_id)
        symbols = self.desk.symbols()
        step = edit(
            self.drafts,
            source.channel_id,
            message.message_id,
            message.text,
            self.now(),
            symbols,
            policy.aliases,
        )
        if step is None:
            return None
        if step.action != "complete":
            return self._waiting(source, message, step, symbols, policy.aliases)
        self.noted.emit(source.title, "the edited message completed the signal")
        whole = ChannelMessage(
            message.channel_id,
            message.message_id,
            message.date,
            step.text,
            reply_to=message.reply_to,
        )
        return self.handle(whole, joined=True)

    def expire_drafts(self) -> list[Draft]:
        """Timer (UI thread): drafts whose time is up, each told once with what was missing.
        Readings that waited for their symbol go on first (0.50.3)."""
        self.retry_symbols()
        gone = self.drafts.expired(self.now())
        symbols = self.desk.symbols()
        for draft in gone:
            source = self.repository.source(draft.channel_id)
            title = source.title if source is not None else str(draft.channel_id)
            aliases = self.repository.policy(draft.channel_id).aliases
            reason = expired_reason(draft, symbols, aliases)
            telling = worth_telling(draft, symbols, aliases)
            last_id = draft.message_ids[-1]
            last = ChannelMessage(draft.channel_id, last_id, draft.started, draft.text)
            on = source is not None and source.enabled
            lone = len(draft.texts) == 1 and not numbers(draft.text)
            lone = lone and self.desk.parse_text(draft.text, aliases).direction is None
            ready = telling and on and not lone
            if ready and source is not None and self.ask_ai(source, last, draft.text):
                reason = reason.replace(", nothing done", ": the AI reads it once more")
            self.log("INFO", f"Telegram channel {title}: {reason}")
            if telling:
                self.noted.emit(title, reason)
        return gone

    # A symbol that is not watched yet (0.50.3) --------------------------------------------
    def wait_for_symbol(
        self,
        source: ChannelSource,
        reading: Reading,
        symbols: tuple[str, ...],
        aliases: dict[str, str],
    ) -> bool:
        """Keep a signal reading whose symbol is not on the watchlist for a while (True when
        it waits now)."""
        outcome = reading.outcome
        if not isinstance(outcome, ReadResult) or outcome.kind != "signal" or not outcome.symbol:
            return False
        if resolve_symbol(outcome.symbol, symbols, aliases):
            return False  # the symbol is watched: it failed for another reason
        key = (reading.channel_id, reading.message.message_id)
        if any((item.channel_id, item.message.message_id) == key for _, item in self.symbol_waits):
            return False
        self.symbol_waits = [*self.symbol_waits, (self.now(), reading)][-KEEP_SYMBOL_WAITS:]
        minutes = SYMBOL_WAIT_SECONDS / 60
        self.log(
            "INFO",
            f"Telegram channel {source.title}: the signal waits {minutes:.0f} min; add "
            f"{outcome.symbol} on the Market page and its card comes by itself",
        )
        return True

    def retry_symbols(self) -> int:
        """Readings whose symbol is on the watchlist now go on the normal way (the number
        that went on); the ones that waited too long are dropped."""
        if not self.symbol_waits:
            return 0
        now = self.now()
        symbols = self.desk.symbols()
        keep: list[tuple[float, Reading]] = []
        ready: list[Reading] = []
        for at, reading in self.symbol_waits:
            if now - at > SYMBOL_WAIT_SECONDS:
                continue
            outcome = reading.outcome
            symbol = outcome.symbol if isinstance(outcome, ReadResult) else ""
            aliases = self.repository.policy(reading.channel_id).aliases
            if symbol and resolve_symbol(symbol, symbols, aliases):
                ready.append(reading)
            else:
                keep.append((at, reading))
        self.symbol_waits = keep
        for reading in ready:
            self.log(
                "INFO",
                f"Telegram channel message {reading.message.message_id}: its symbol is on the "
                "watchlist now, the reading goes on",
            )
            self.show_read(replace(reading, model=""))  # nothing is spent a second time
        return len(ready)

    # The AI reads unclear messages (docs/AI_LAB_V3.md section 1) ----------------------------
    def _unclear(
        self,
        source: ChannelSource,
        message: ChannelMessage,
        text: str,
        symbols: tuple[str, ...],
        aliases: dict[str, str],
    ) -> Route | None:
        """A result claim, or an unclear message the AI reads (None: plain noise)."""
        if is_claim(text):
            found = Route(Action.SKIP, CLAIM_REASON)
        elif message.photo and self.ask_ai(source, message, text, message.photo):
            found = Route(Action.SKIP, "a picture: the AI reads it")
        elif unclear(text, symbols, aliases) and self.ask_ai(source, message, text):
            found = Route(Action.SKIP, "an unclear message: the AI reads it")
        else:
            return None
        self.counts.setdefault(source.channel_id, ChannelCounts()).skipped += 1
        self.log("INFO", f"Telegram channel {source.title}: {found.reason}")
        self._remember(source, message, found)
        return found

    def _page_ai(self) -> AiClient | str:
        found: object = self.desk.page.llm_panel.make_client()
        return found if isinstance(found, AiClient | str) else "no AI connection"

    def ask_ai(
        self,
        source: ChannelSource,
        message: ChannelMessage,
        text: str,
        picture: bytes = b"",
    ) -> bool:
        """Ask the AI once to read a message (and its picture), in a worker thread (True
        when it was asked)."""
        key = (source.channel_id, message.message_id)
        if key in self.asked or (picture and self.now() < self.pictures_off_until):
            return False
        client = self.ai()
        if isinstance(client, str):
            self.cannot(source, client)
            return False
        if not self.reads.take(self.now()):
            self.cannot(source, f"the daily limit of {self.reads.limit} AI readings is used")
            return False
        if len(self.asked) >= KEEP_ASKED:
            self.asked.clear()
        self.asked.add(key)
        earlier = self._earlier(source.channel_id, message.message_id)
        messages = read_messages(text, earlier, self.desk.symbols(), picture)
        threading.Thread(
            target=self._read_work,
            args=(client, source.channel_id, message, (*earlier, text), messages, bool(picture)),
            name="channel-ai-read",
            daemon=True,
        ).start()
        return True

    def cannot(self, source: ChannelSource, reason: str) -> bool:
        """Tell (log and chat line) that a message the parser did not understand could not go to
        the AI, and why; the same reason is told once in a while (True when it was told)."""
        now = self.now()
        if now - self.cannot_at.get(reason, -math.inf) < CANNOT_SECONDS:
            return False
        self.cannot_at[reason] = now
        line = f"the AI could not read a message: {reason}"
        self.log("WARNING", f"Telegram channel {source.title}: {line}")
        self.noted.emit(source.title, line)
        return True

    def _earlier(self, channel_id: int, message_id: int) -> list[str]:
        """The channel's last messages before this one, oldest first (the AI's context)."""
        try:
            rows = self.repository.messages(channel_id, CONTEXT_MESSAGES + 1)
        except Exception:
            return []
        found = [
            row.edited_text or row.text
            for row in rows
            if row.message_id != message_id and not row.deleted
        ]
        return list(reversed(found[:CONTEXT_MESSAGES]))

    def _read_work(
        self,
        client: AiClient,
        channel_id: int,
        message: ChannelMessage,
        texts: tuple[str, ...],
        messages: Sequence[Message],
        picture: bool = False,
    ) -> None:
        outcome: ReadResult | str
        refused = False
        started = time.monotonic()
        answer: object = None
        try:
            reply = client.complete(messages, schema=SCHEMA, max_tokens=READ_TOKENS)
            answer = reply
            found = parse_reply(reply.json_text or reply.text)
            outcome = found if found is not None else "the AI's answer could not be read"
        except AiCallError as error:
            outcome = plain_text(error, client.settings.base_url)
            refused = picture and error.code in REFUSED_CODES
        except Exception as error:
            outcome = f"{type(error).__name__}: {error}"
        reading = spent(
            Reading(channel_id, message, texts, outcome, picture, refused),
            answer,
            time.monotonic() - started,
        )
        with contextlib.suppress(RuntimeError):  # the window may be closing
            self.read_done.emit(reading)

    def show_read(self, reading: object) -> Route | None:
        """Slot (UI thread): the AI's reading, checked before it counts (`canonical`)."""
        if not isinstance(reading, Reading):
            return None
        source = self.repository.source(reading.channel_id)
        if source is None or not source.enabled:
            return None
        message, outcome = reading.message, reading.outcome
        self.account(source, reading)
        if isinstance(outcome, str):
            what = "a picture (does the model read images?)" if reading.picture else "a message"
            why = f"the AI could not read {what}: {outcome}"
            if reading.refused:
                self.pictures_off_until = self.now() + PICTURE_PAUSE
                why += PICTURE_PAUSED
            self.log("WARNING", f"Telegram channel {source.title}: {why}")
            if reading.picture:
                self.noted.emit(source.title, why)
            return None
        aliases = self.repository.policy(source.channel_id).aliases
        symbols = self.desk.symbols()
        text, problem = canonical(
            outcome,
            reading.texts,
            symbols,
            aliases,
            picture=reading.picture,
        )
        if not text:
            found = Route(Action.SKIP, problem)
            self.log("INFO", f"Telegram channel {source.title}: {problem}")
            self._remember(source, message, found)
            if outcome.kind in ("signal", "part"):
                self.noted.emit(source.title, problem)
            self.wait_for_symbol(source, reading, symbols, aliases)
            return found
        draft = self.drafts.get(source.channel_id, self.now())
        if draft is not None and message.message_id in draft.message_ids:
            self.drafts.drop(source.channel_id)
        if reading.picture:
            line = f"the AI read the picture as: {text} (check the numbers on the card)"
            if len(self.no_auto) >= KEEP_ASKED:
                self.no_auto.clear()
            self.no_auto.add((message.channel_id, message.message_id))  # never Auto-take
        else:
            line = f"the AI read the message as: {text}"
        self.noted.emit(source.title, line)
        whole = ChannelMessage(
            message.channel_id,
            message.message_id,
            message.date,
            text,
            reply_to=message.reply_to,
        )
        return self.handle(whole, joined=True)

    def account(self, source: ChannelSource, reading: Reading) -> None:
        """One reading in the log (what the AI answered, how long, how many tokens), in the
        AI Lab's usage (kind `channel`) and in today's AI line of the Telegram signals tab."""
        what = " with its picture" if reading.picture else ""
        tokens = f"{reading.input_tokens}+{reading.output_tokens} tokens"
        cost = f", ${reading.cost:.4f}" if math.isfinite(reading.cost) else ""
        model = f"{reading.model}, " if reading.model else ""
        answer = reading.raw or (reading.outcome if isinstance(reading.outcome, str) else "")
        self.log(
            "INFO",
            f"Telegram channel {source.title}: the AI read message {reading.message.message_id}"
            f"{what} in {reading.seconds:.1f} s ({model}{tokens}{cost}): {answer}",
        )
        if not reading.model:
            return  # the call failed before an answer: nothing was spent
        now = self.now()
        try:
            self.usage(
                UsageRecord(
                    now,
                    "channel",
                    reading.model,
                    reading.input_tokens,
                    reading.cached_tokens,
                    reading.output_tokens,
                    1,
                    reading.cost,
                ),
            )
        except Exception as error:
            self.log("WARNING", f"Telegram channel AI usage not written: {error}")
        today = day_start(now)
        if self.spend.day != today:
            self.spend = AiSpend(today)
        self.spend.reads += 1
        self.spend.tokens += reading.input_tokens + reading.output_tokens
        if math.isfinite(reading.cost):
            self.spend.cost += reading.cost
        inbox = self.desk.inbox
        if inbox is not None:
            inbox.set_ai_spend(self.spend.reads, self.spend.tokens, self.spend.cost)

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
        if not (update.acts and signal.request_id and signal.request_id in self.desk.cards):
            return update
        why = follow_block(self.repository.policy(source.channel_id), update.kind)
        if why:
            self.log("INFO", f"Telegram channel {source.title}: no follow-up card, {why}")
            return update
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
        self._show(card, title)
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
        titles = [channel.title for channel in sources]
        auto = read_auto(text, titles)
        if auto is not None:
            source = next(channel for channel in sources if channel.title == auto[0])
            self.desk.page.chat.add_bubble(text.strip())
            self.auto_card(source, auto[1])
            return True
        change = read_change(text, titles)
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

    def auto_card(self, source: ChannelSource, on: bool) -> SettingsCard:
        """Auto-take on or off for one channel: saved only after Hold to save (0.50.5)."""
        policy = self.repository.policy(source.channel_id)
        old = "on" if policy.auto_take else "off"
        card = SettingsCard(
            source.title,
            "auto-take",
            old,
            "on" if on else "off",
            fa=self.desk.page.persian,
        )
        card.held.connect(partial(self._save_auto, card, source, on))
        self._show(card)
        return card

    def _save_auto(self, card: SettingsCard, source: ChannelSource, on: bool) -> bool:
        if card.done:
            return False
        policy = self.repository.policy(source.channel_id)
        if on and policy.mode is not ChannelMode.LIVE:
            card.finish("Not saved: Auto-take needs a Live channel (set it to Live first).")
            return False
        updated = policy.model_copy(update={"auto_take": on})
        self.repository.set_policy(source.channel_id, updated, self.now())
        after = "on" if on else "off"
        audit("telegram channel setting", before=f"{source.title} auto-take", after=after)
        card.finish(card.word("Saved."))
        return True

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

    def _show(self, card: HoldCard, channel: str = "") -> None:
        """A follow-up goes to the Telegram signals tab when there is one (0.47.0); a
        settings card answers your own chat message, so it stays in the chat."""
        tokens = self.desk.page.tokens
        apply_tree(card, tokens)
        card.apply_tokens(tokens)
        inbox = self.desk.inbox
        if inbox is not None and channel:
            inbox.add_card(card, channel)
        else:
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
