"""The plain-language story of one trade (spec C12), made from its record, its signal's
reason and its events. Pure: the same trade always gives the same story."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.analytics.trades import TradeRecord

EVENT_WORDS = {
    "open": "opened",
    "fill": "filled",
    "modify_sl": "moved the stop loss",
    "modify_tp": "moved the take profit",
    "partial_close": "closed part of the position",
    "close": "closed",
    "error": "had an error",
    "retry": "retried",
}


@dataclass(frozen=True)
class TradeEvent:
    time: float  # UTC seconds
    type: str
    old_value: str = ""
    new_value: str = ""
    reason: str = ""


def _clock(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {rest:02d} min"
    return f"{hours // 24} days {hours % 24} h"


def narrative(trade: TradeRecord, events: Sequence[TradeEvent] = ()) -> str:
    side = "Bought" if trade.direction == "buy" else "Sold"
    who = (
        f"The {trade.strategy.replace('_', ' ')} strategy"
        if trade.source == "bot"
        else "You (a manual trade)"
    )
    mode = "practice money (Paper)" if trade.mode == "paper" else "the live account"
    parts = [
        f"{side} {trade.volume:g} lots of {trade.symbol} at {trade.open_price:g} on "
        f"{_clock(trade.open_time)} with {mode}. {who} took it."
    ]
    if trade.reason:
        parts.append(f"Why: {trade.reason.rstrip('.')}.")
    if trade.sl is not None or trade.tp is not None:
        stop = f"{trade.sl:g}" if trade.sl is not None else "none"
        target = f"{trade.tp:g}" if trade.tp is not None else "none"
        risk = f", risking {trade.risk_money:,.2f}" if trade.risk_money else ""
        parts.append(f"Stop loss {stop}, take profit {target}{risk}.")
    if trade.probability is not None:
        parts.append(f"The estimated chance to win was {trade.probability * 100:.0f}%.")
    for event in sorted(events, key=lambda e: e.time):
        if event.type in ("open", "close"):
            continue
        word = EVENT_WORDS.get(event.type, event.type.replace("_", " "))
        change = f" from {event.old_value} to {event.new_value}" if event.old_value else ""
        why = f" ({event.reason})" if event.reason else ""
        parts.append(f"At {_clock(event.time)} the app {word}{change}{why}.")
    result = "won" if trade.win else "lost" if trade.loss else "broke even"
    how = f" by {trade.exit_reason}" if trade.exit_reason else ""
    r_text = f" ({trade.r_multiple:+.2f} R)" if trade.r_multiple is not None else ""
    parts.append(
        f"It closed{how} at {trade.close_price:g} on {_clock(trade.close_time)} after "
        f"{_duration(trade.duration)} and {result} {trade.net_profit:+,.2f}{r_text}, "
        f"costs included (commission {trade.commission:+,.2f}, swap {trade.swap:+,.2f})."
    )
    if trade.mfe_r is not None and trade.mae_r is not None:
        parts.append(
            f"On the way it was up to {trade.mfe_r:+.2f} R in profit and down to "
            f"{trade.mae_r:+.2f} R."
        )
    return " ".join(parts)
