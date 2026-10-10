"""A trading signal from free text (docs/SIGNAL_DESK.md section 2.2). Pure, no I/O.

    XAUUSD BUY 2345-2340  SL 2335  TP1 2350  TP2 2360  TP3 open
    GOLD sell now @ 2361 sl 2368 tp 2355 / 2348
    طلا فروش ۲۳۶۱ استاپ ۲۳۶۸ تارگت ۲۳۵۵ و ۲۳۴۸

Numbers belong to the last label before them: after "sl" the stop loss, after "tp" the
targets (until the next label), after "entry" or "@" (or before any label) the entry, one
price or a zone of two. "buy stop" and "sell stop" are stop orders; any other "stop" is the
stop loss. Prices are copied as written, never guessed: a field that is not in the text is
missing and the signal is not complete.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.signals import Direction, OrderType
from app.signals.words import Token, normalise, resolve_symbol, tokens

MAX_INDEX = 10  # "TP 1: 2350" -> 1 is the target's number, not a price


@dataclass(frozen=True)
class ParsedSignal:
    text: str
    symbol: str = ""
    direction: Direction | None = None
    order: OrderType | None = None  # None: the plan picks market, limit or stop
    entry: tuple[float, ...] = ()  # one price or a zone (low, high); empty = at market
    sl: float | None = None
    tps: tuple[float | None, ...] = ()  # None = an open target
    ignored: tuple[float, ...] = ()  # numbers that belong to no field

    @property
    def missing(self) -> tuple[str, ...]:
        found: list[str] = []
        if not self.symbol:
            found.append("symbol")
        if self.direction is None:
            found.append("side")
        if self.sl is None:
            found.append("stop loss")
        if not any(tp is not None for tp in self.tps):
            found.append("target")
        return tuple(found)

    @property
    def complete(self) -> bool:
        return not self.missing

    def summary(self) -> str:
        side = "?"
        if self.direction is not None:
            side = "buy" if self.direction is Direction.LONG else "sell"
        kind = f" {self.order.value}" if self.order is not None else ""
        entry = "-".join(f"{value:g}" for value in self.entry) or "market"
        targets = " / ".join("open" if tp is None else f"{tp:g}" for tp in self.tps) or "?"
        sl = f"{self.sl:g}" if self.sl is not None else "?"
        return f"{self.symbol or '?'} {side}{kind} {entry} SL {sl} TP {targets}"


def _is_index(items: list[Token], at: int) -> bool:
    """A small whole number right after a bare "tp" and before a price: the target's number."""
    token = items[at]
    if token.number is None or "." in token.text or token.number > MAX_INDEX or at == 0:
        return False
    before = items[at - 1]
    if before.label != "tp" or before.text != "tp":
        return False
    after = items[at + 1] if at + 1 < len(items) else None
    return after is not None and after.number is not None


def parse(
    text: str,
    symbols: tuple[str, ...] = (),
    aliases: Mapping[str, str] | None = None,
) -> ParsedSignal:
    """Every field the text names; see the module docstring for the rules."""
    items = tokens(normalise(text))
    symbol = ""
    direction: Direction | None = None
    order: OrderType | None = None
    entry: list[float] = []
    sl: float | None = None
    tps: list[float | None] = []
    ignored: list[float] = []
    label = "entry"
    previous = ""
    for at, token in enumerate(items):
        word = token.label
        if token.number is not None:
            value = token.number
            if not symbol and direction is None:
                ignored.append(value)  # "VIP 2", a date: before the signal starts
            elif label == "entry" and len(entry) < 2:
                entry.append(value)
            elif label == "sl" and sl is None:
                sl = value
                label = "done"
            elif label == "tp" and not _is_index(items, at):
                tps.append(value)
            elif not (label == "tp" and _is_index(items, at)):
                ignored.append(value)
        elif word in ("buy", "sell"):
            if direction is None:
                direction = Direction.LONG if word == "buy" else Direction.SHORT
            if not entry:
                label = "entry"
        elif word == "stop":
            if previous in ("buy", "sell"):
                order = OrderType.STOP
            else:
                label = "sl"
        elif word == "limit":
            order = OrderType.LIMIT
        elif word == "now":
            order = OrderType.MARKET
        elif word in ("sl", "tp", "entry"):
            label = word
        elif word == "open" and label == "tp":
            tps.append(None)
        elif not symbol:
            symbol = resolve_symbol(token.text, symbols, aliases)
        previous = word
    low_high = tuple(sorted(entry)) if len(entry) == 2 else tuple(entry)
    return ParsedSignal(
        text=text,
        symbol=symbol,
        direction=direction,
        order=order,
        entry=low_high,
        sl=sl,
        tps=tuple(tps),
        ignored=tuple(ignored),
    )
