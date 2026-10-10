"""Telegram channel messages to the AI Lab (docs/SIGNAL_DESK.md 3.3 to 3.5, phase 21d).

The reader stores every new message of an on channel and hands it here in its own thread;
a Qt signal brings it to the UI thread. There the prefilter says whether it is a signal, the
channel's settings (`app.channels.policy`) say what to do with it: in **Live** it becomes an
order card in the AI Lab (`LabDesk.take`), booked to the channel's own magic and sized with
its budget; in **Paper trial** it is only counted. Nothing here can send an order: every
card waits for your hold-to-confirm like a pasted one, and a channel's text is never an
instruction to the app or the AI.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from PySide6.QtCore import QObject, Qt, Signal

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
from app.risk.budget import budget_features
from app.signals.prefilter import Kind, classify
from app.signals.words import resolve_symbol
from app.storage.channel_store import ChannelRepository, ChannelSource
from app.strategies.manual_signal import channel_strategy
from app.ui.lab_desk import CardSource, LabDesk

KEEP_SEEN = 50

Log = Callable[[str, str], None]


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

    def __init__(
        self,
        desk: LabDesk,
        repository: ChannelRepository,
        *,
        log: Log = _quiet,
        utc_now: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(desk)
        self.desk = desk
        self.repository = repository
        self.log = log
        self.now = utc_now
        self.seen: list[Seen] = []
        self.counts: dict[int, ChannelCounts] = {}
        self.arrived.connect(self.handle, Qt.ConnectionType.QueuedConnection)

    def attach(self, reader: ChannelReader) -> None:
        reader.add_message_listener(self._from_reader)

    def _from_reader(self, message: ChannelMessage) -> None:
        with contextlib.suppress(RuntimeError):  # the window may be closing
            self.arrived.emit(message)

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
        if found.action is Action.CARD:
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
            self.desk.take(message.text, origin)
        elif found.action is Action.PAPER:
            counts.paper += 1
        else:
            counts.skipped += 1
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
