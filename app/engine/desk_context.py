"""The Signal desk's context lines (docs/SIGNAL_DESK.md 2.5, phase 21b2).

What the app sees around a pasted signal right now, read from what it already has: the
market watch's analysis card (the trend per timeframe and the bias, the H1 structure, the
spread against its normal for this hour), the next high-impact news for either currency of
the symbol, and the bot's open trades on the same currencies (with the shared bet, as the
one-bet-per-currency filter sees it). Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.analysis.symbol import SymbolAnalysis
from app.calendar.models import WARN_AFTER_SECONDS, CalendarEvent, Impact, matches_symbol
from app.domain.signals import Direction, SignalRecord
from app.engine.currency_guard import legs, shared_bet

MAX_TRADES = 4
CARD_LINES = ("Trend:", "Structure ")


def currency_names(symbol: str) -> str:
    """'EUR or USD' for a currency pair, the symbol itself otherwise."""
    found = legs(symbol)
    return f"{found[0]} or {found[1]}" if found is not None else symbol


def market_lines(analysis: SymbolAnalysis | None, symbol: str) -> list[str]:
    """The analysis card's headline, its trend and structure lines, and the spread."""
    if analysis is None:
        return [f"No live analysis of {symbol} yet."]
    card = analysis.card
    lines = [f"Now: {card.headline}"]
    lines.extend(line for line in card.lines if line.startswith(CARD_LINES))
    if analysis.spread is not None:
        text = analysis.spread.text()
        lines.append(f"{text[:1].upper()}{text[1:]}.")
    return lines


def _high(event: CalendarEvent, symbol: str, now: float) -> bool:
    """A high-impact event for the symbol that is still ahead (or under 15 minutes ago)."""
    recent = event.time >= now - WARN_AFTER_SECONDS
    return event.impact is Impact.HIGH and matches_symbol(event, symbol) and recent


def news_line(events: Iterable[CalendarEvent], symbol: str, now: float) -> str:
    """The next high-impact event for either currency of the symbol."""
    found = [event for event in events if _high(event, symbol, now)]
    if not found:
        return f"No high-impact news for {currency_names(symbol)} on the calendar."
    soonest = min(found, key=lambda event: event.time)
    return f"Next high-impact news: {soonest.short(now)}."


def _related(symbol: str, other: str) -> bool:
    if symbol == other:
        return True
    mine, theirs = legs(symbol), legs(other)
    return mine is not None and theirs is not None and bool(set(mine) & set(theirs))


def trades_line(
    symbol: str,
    direction: Direction | None,
    records: Iterable[SignalRecord],
    own: Iterable[str] = (),
) -> str:
    """The bot's open trades on the same currencies, and which of them are the same bet."""
    skip = set(own)
    found: list[str] = []
    for record in records:
        signal = record.signal
        if record.id in skip or not signal.state.open_position:
            continue
        if not _related(symbol, signal.symbol):
            continue
        side = "buy" if signal.direction is Direction.LONG else "sell"
        text = f"{signal.symbol} {side} ({signal.strategy})"
        if direction is not None:
            bet = shared_bet(symbol, direction, signal.symbol, signal.direction)
            if bet:
                text += f", the same bet: {bet}"
        found.append(text)
    names = currency_names(symbol)
    if not found:
        return f"No open trades on {names}."
    more = f" and {len(found) - MAX_TRADES} more" if len(found) > MAX_TRADES else ""
    return f"Open trades on {names}: {'; '.join(found[:MAX_TRADES])}{more}."


def desk_context(
    symbol: str,
    direction: Direction | None,
    *,
    analysis: SymbolAnalysis | None,
    events: Sequence[CalendarEvent],
    records: Sequence[SignalRecord],
    own: Sequence[str] = (),
    now: float,
) -> tuple[str, ...]:
    """Every context line of a signal on `symbol`, in the card's order."""
    found = [*market_lines(analysis, symbol)]
    known = list(events) + (list(analysis.events) if analysis is not None else [])
    found.append(news_line(known, symbol, now))
    found.append(trades_line(symbol, direction, records, own))
    return tuple(found)
