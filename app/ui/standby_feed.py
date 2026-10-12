"""The AI stands by for a channel that is about to give a signal (phase 22j, 0.50.0).

`ChannelFeed` reads whole signals and asks the AI about messages with numbers. A channel's
admin often talks first ("get ready", "dollar", "I am looking at gold") and gives the trade
in the next messages, so the feed saw noise and the AI was never asked. `StandbyFeed` adds
(docs/TELEGRAM_STANDBY.md, `app.channels.standby`):

- **A glance**: a short message the parser calls noise goes to the AI in a tiny, quick call
  (low reasoning effort, 80 tokens): does it say a signal is coming? Yes: standby.
- **Standby**: a teaser or a half signal ("get ready", "EURUSD buy") also opens it. For 10
  minutes (30 at most, 12 readings) every new message of that channel is read by the AI at
  once, with the earlier messages, whether it has numbers or not, and put together with
  them. The line goes to the Telegram signals tab when it starts and when it ends.
- The AI's answer is checked like any other reading and ends as a card that needs your hold.
  Nothing here can send an order. Glances are limited (12 an hour per channel, 80 a day);
  readings 200 a day.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from PySide6.QtCore import Qt, Signal

from app.ai.transport import AiCallError, AiClient, CallOptions, Message, plain_text
from app.ai.usage_store import UsageRecord
from app.channels.drafts import WAITING, Draft, Piece, piece
from app.channels.folder import ChannelMessage
from app.channels.policy import Action, Route
from app.channels.reading import (
    READ_TOKENS,
    SCHEMA,
    ReadCap,
    ReadResult,
    parse_reply,
)
from app.channels.standby import (
    GLANCE_SCHEMA,
    GLANCE_TOKENS,
    QUICK_EFFORT,
    QUICK_READ_TOKENS,
    STANDBY_MINUTES,
    Glance,
    GlanceCap,
    StandbyBook,
    glance_messages,
    parse_glance,
    standby_messages,
    worth_glance,
)
from app.storage.channel_store import ChannelRepository, ChannelSource
from app.ui.channel_feed import (
    KEEP_ASKED,
    READING,
    REFUSED_CODES,
    ChannelCounts,
    ChannelFeed,
    Engine,
    Log,
    Reading,
    spent,
)
from app.ui.lab_desk import LabDesk

READS_PER_DAY = 200  # AI readings a day (the plain feed allows 100)
OPENING = (Piece.TEASER, Piece.PART)  # pieces that tell the next messages are the rest
DONE = (Action.CARD, Action.PAPER)  # a signal came: the standby is over


def _quiet(level: str, message: str) -> None:
    return None


def quick_client(client: AiClient, learned: dict[str, CallOptions]) -> AiClient:
    """The same connection with a low reasoning effort, when the model has an effort set (a
    quick call must not think for long). What the service refused is kept in `learned`,
    apart from the chat's own."""
    effort = getattr(getattr(client, "settings", None), "reasoning_effort", "")
    if not isinstance(client, AiClient) or not effort:
        return client
    settings = replace(client.settings, reasoning_effort=QUICK_EFFORT)
    return AiClient(
        settings,
        client.key,
        transport=client.transport,
        clock=client.clock,
        learned=learned,
    )


@dataclass(frozen=True)
class Glanced:
    """The AI's glance at one message, from the worker thread."""

    reading: Reading  # what it cost; its outcome is the failure text, if any
    glance: Glance | None


class StandbyFeed(ChannelFeed):
    glanced = Signal(object)  # a Glanced, from the AI's worker thread

    def __init__(
        self,
        desk: LabDesk,
        repository: ChannelRepository,
        *,
        engine: Engine | None = None,
        log: Log = _quiet,
        utc_now: Callable[[], float] = time.time,
        ai: Callable[[], AiClient | str] | None = None,
        usage: Callable[[UsageRecord], None] | None = None,
    ) -> None:
        super().__init__(
            desk,
            repository,
            engine=engine,
            log=log,
            utc_now=utc_now,
            ai=ai,
            usage=usage,
        )
        self.standby = StandbyBook()
        self.glances = GlanceCap()
        self.quick_learned: dict[str, CallOptions] = {}
        self.reads = ReadCap(READS_PER_DAY)
        self.glanced.connect(self.show_glance, Qt.ConnectionType.QueuedConnection)

    # Standby ------------------------------------------------------------------------------
    def watch(self, source: ChannelSource, hint: str, reason: str) -> bool:
        """Put the channel on standby (or keep it there); True when it just started."""
        now = self.now()
        fresh = self.standby.get(source.channel_id, now) is None
        self.standby.open(source.channel_id, now, hint, reason)
        if fresh:
            named = f" ({hint})" if hint else ""
            line = (
                f"standby{named}: {reason or 'a signal may be coming'}. "
                f"The AI reads every new message for {STANDBY_MINUTES:g} min"
            )
            self.log("INFO", f"Telegram channel {source.title}: {line}")
            self.noted.emit(source.title, line)
        return fresh

    def standing(self, channel_id: int) -> bool:
        return self.standby.get(channel_id, self.now()) is not None

    # A message ----------------------------------------------------------------------------
    def handle(self, message: object, *, joined: bool = False) -> Route | None:
        if not isinstance(message, ChannelMessage) or joined:
            return super().handle(message, joined=joined)
        source = self.repository.source(message.channel_id)
        if source is None or not source.enabled:
            return super().handle(message)
        before = self.standing(source.channel_id)
        policy = self.repository.policy(source.channel_id)
        kind, parsed = piece(message.text, self.desk.symbols(), policy.aliases)
        found = super().handle(message)
        if found is None:
            return None
        if found.action in DONE:
            self.standby.close(source.channel_id)
            return found
        if before and found.reason.startswith(WAITING) and READING not in found.reason:
            draft = self.drafts.get(source.channel_id, self.now())
            if draft is not None and self.ask_ai(source, message, draft.text):
                found = Route(found.action, found.reason + READING)
        if kind in OPENING:
            self.watch(source, parsed.symbol or "", "the channel is giving a signal in pieces")
        return found

    def _unclear(
        self,
        source: ChannelSource,
        message: ChannelMessage,
        text: str,
        symbols: tuple[str, ...],
        aliases: dict[str, str],
    ) -> Route | None:
        found = super()._unclear(source, message, text, symbols, aliases)
        if found is not None:
            return found
        if self.standing(source.channel_id):
            asked = self.ask_ai(source, message, text)
            reason = "standby: the AI reads it"
        else:
            asked = self.glance(source, message, text)
            reason = "the AI glances at it: is a signal coming?"
        if not asked:
            return None
        found = Route(Action.SKIP, reason)
        self.counts.setdefault(source.channel_id, ChannelCounts()).skipped += 1
        self.log("INFO", f"Telegram channel {source.title}: {reason}")
        self._remember(source, message, found)
        return found

    # The quick readings ---------------------------------------------------------------------
    def ask_ai(
        self,
        source: ChannelSource,
        message: ChannelMessage,
        text: str,
        picture: bytes = b"",
    ) -> bool:
        """On standby the reading is quick and told so; otherwise the plain feed's."""
        now = self.now()
        on = self.standby.get(source.channel_id, now)
        if on is None:
            return super().ask_ai(source, message, text, picture)
        key = (source.channel_id, message.message_id)
        if key in self.asked or (picture and now < self.pictures_off_until):
            return False
        client = self.ai()
        if isinstance(client, str):
            self.cannot(source, client)
            return False
        if not self.reads.take(now):
            self.cannot(source, f"the daily limit of {self.reads.limit} AI readings is used")
            return False
        if len(self.asked) >= KEEP_ASKED:
            self.asked.clear()
        self.asked.add(key)
        self.standby.count_read(source.channel_id, now)
        earlier = self._earlier(source.channel_id, message.message_id)
        messages = standby_messages(text, earlier, self.desk.symbols(), on.hint, picture)
        threading.Thread(
            target=self._read_work,
            args=(
                quick_client(client, self.quick_learned),
                source.channel_id,
                message,
                (*earlier, text),
                messages,
                bool(picture),
                QUICK_READ_TOKENS,
            ),
            name="channel-ai-standby",
            daemon=True,
        ).start()
        return True

    def _read_work(
        self,
        client: AiClient,
        channel_id: int,
        message: ChannelMessage,
        texts: tuple[str, ...],
        messages: Sequence[Message],
        picture: bool = False,
        max_tokens: int = READ_TOKENS,
    ) -> None:
        outcome: ReadResult | str
        refused = False
        started = time.monotonic()
        answer: object = None
        try:
            reply = client.complete(messages, schema=SCHEMA, max_tokens=max_tokens)
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
        found = super().show_read(reading)
        if not isinstance(reading, Reading) or not isinstance(reading.outcome, ReadResult):
            return found
        source = self.repository.source(reading.channel_id)
        if source is None or not source.enabled:
            return found
        outcome = reading.outcome
        if found is not None and found.action in DONE:
            self.standby.close(source.channel_id)
        elif outcome.kind in ("part", "teaser"):
            self.watch(source, outcome.symbol, outcome.why or "a signal is coming")
        return found

    # The glance -----------------------------------------------------------------------------
    def glance(self, source: ChannelSource, message: ChannelMessage, text: str) -> bool:
        """Ask the AI once, quickly, if a short message says a signal is coming (True when it
        was asked). Without an AI connection or within the limits nothing happens."""
        key = (source.channel_id, message.message_id)
        now = self.now()
        if key in self.asked or not worth_glance(text):
            return False
        client = self.ai()
        if isinstance(client, str) or not self.glances.take(source.channel_id, now):
            return False
        if len(self.asked) >= KEEP_ASKED:
            self.asked.clear()
        self.asked.add(key)
        earlier = self._earlier(source.channel_id, message.message_id)
        messages = glance_messages(text, earlier, self.desk.symbols())
        threading.Thread(
            target=self._glance_work,
            args=(
                quick_client(client, self.quick_learned),
                source.channel_id,
                message,
                (*earlier, text),
                messages,
            ),
            name="channel-ai-glance",
            daemon=True,
        ).start()
        return True

    def _glance_work(
        self,
        client: AiClient,
        channel_id: int,
        message: ChannelMessage,
        texts: tuple[str, ...],
        messages: Sequence[Message],
    ) -> None:
        found: Glance | None = None
        failure = ""
        started = time.monotonic()
        answer: object = None
        try:
            reply = client.complete(messages, schema=GLANCE_SCHEMA, max_tokens=GLANCE_TOKENS)
            answer = reply
            found = parse_glance(reply.json_text or reply.text)
            failure = "" if found is not None else "the AI's answer could not be read"
        except AiCallError as error:
            failure = plain_text(error, client.settings.base_url)
        except Exception as error:
            failure = f"{type(error).__name__}: {error}"
        reading = spent(
            Reading(channel_id, message, texts, failure),
            answer,
            time.monotonic() - started,
        )
        with contextlib.suppress(RuntimeError):  # the window may be closing
            self.glanced.emit(Glanced(reading, found))

    def show_glance(self, glanced: object) -> Route | None:
        """Slot (UI thread): the AI's glance. A signal that may be coming starts the standby."""
        if not isinstance(glanced, Glanced):
            return None
        reading = glanced.reading
        source = self.repository.source(reading.channel_id)
        if source is None or not source.enabled:
            return None
        self.account(source, reading)
        number = reading.message.message_id
        found = glanced.glance
        head = f"Telegram channel {source.title}"
        if found is None:
            self.log("WARNING", f"{head}: the AI could not judge a message: {reading.outcome}")
            return None
        if not found.standby:
            why = f" ({found.why})" if found.why else ""
            self.log("INFO", f"{head}: message {number}: no signal coming{why}")
            return None
        self.watch(source, found.symbol, found.why or "a signal may be coming")
        route = Route(Action.SKIP, "standby: the AI thinks a signal is coming")
        self._remember(source, reading.message, route)
        return route

    # The end ------------------------------------------------------------------------------
    def expire_drafts(self) -> list[Draft]:
        """Timer: a standby whose time or readings are used up says so, then the drafts."""
        for stood in self.standby.expired(self.now()):
            source = self.repository.source(stood.channel_id)
            title = source.title if source is not None else str(stood.channel_id)
            used = stood.reads >= self.standby.reads
            line = "standby over: its readings are used" if used else "standby over: no signal came"
            self.log("INFO", f"Telegram channel {title}: {line}")
            self.noted.emit(title, line)
        return super().expire_drafts()
