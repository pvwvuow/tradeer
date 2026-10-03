"""Trader behavior (spec C11) of manual trades: overtrading, revenge trading, holding losers
longer than winners, trading during news, lot-size inconsistency, and trading outside the
hours that work best. Each check is a plain finding with the numbers behind it."""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.analytics.stats import mean, ordered
from app.analytics.trades import TradeRecord, utc_day

REVENGE_MINUTES = 30
NEWS_MINUTES = 15
OVERTRADING_FACTOR = 2.0
OVERTRADING_MINIMUM = 4
HOLDING_FACTOR = 1.2
LOT_VARIATION = 0.5
BEST_HOURS = 4
MIN_TRADES = 10


@dataclass(frozen=True)
class NewsEvent:
    time: float  # UTC seconds
    currency: str
    impact: str


@dataclass(frozen=True)
class Finding:
    name: str
    flagged: bool
    detail: str
    count: int = 0
    net_profit: float = 0.0


def _currencies(symbol: str) -> set[str]:
    text = "".join(ch for ch in symbol.upper() if ch.isalpha())
    return {text[:3], text[3:6]} if len(text) >= 6 else {text}


def overtrading(trades: Sequence[TradeRecord]) -> Finding:
    days: dict[str, list[TradeRecord]] = {}
    for trade in trades:
        days.setdefault(utc_day(trade.open_time), []).append(trade)
    if not days:
        return Finding("Overtrading", False, "No trades.")
    typical = statistics.median(len(items) for items in days.values())
    limit = max(OVERTRADING_MINIMUM, OVERTRADING_FACTOR * typical)
    busy = {day: items for day, items in days.items() if len(items) > limit}
    net = sum(t.net_profit for items in busy.values() for t in items)
    detail = (
        f"{len(busy)} day(s) with more than {limit:g} trades (typical day: {typical:g}); "
        f"those days made {net:+,.2f}."
        if busy
        else f"No day had more than {limit:g} trades (typical day: {typical:g})."
    )
    return Finding("Overtrading", bool(busy), detail, len(busy), net)


def revenge_trading(trades: Sequence[TradeRecord], minutes: int = REVENGE_MINUTES) -> Finding:
    """A new trade within `minutes` after a loss closed, with a larger lot."""
    items = sorted(trades, key=lambda t: t.open_time)
    found: list[TradeRecord] = []
    window = minutes * 60
    for trade in items:
        before = [t for t in items if t.loss and 0 <= trade.open_time - t.close_time <= window]
        if before and trade.volume > max(t.volume for t in before) + 1e-9:
            found.append(trade)
    net = sum(t.net_profit for t in found)
    detail = (
        f"{len(found)} trade(s) opened within {minutes} min after a loss with a larger lot; "
        f"they made {net:+,.2f}."
        if found
        else f"No larger trade within {minutes} min after a loss."
    )
    return Finding("Revenge trading", bool(found), detail, len(found), net)


def holding_losers(trades: Sequence[TradeRecord]) -> Finding:
    winners = mean([t.duration for t in trades if t.win])
    losers = mean([t.duration for t in trades if t.loss])
    if winners is None or losers is None or winners <= 0:
        return Finding("Holding losers longer than winners", False, "Needs winners and losers.")
    ratio = losers / winners
    detail = (
        f"Losers were held {losers / 60:.0f} min on average, winners {winners / 60:.0f} min "
        f"({ratio:.1f}x)."
    )
    return Finding("Holding losers longer than winners", ratio > HOLDING_FACTOR, detail)


def news_trading(
    trades: Sequence[TradeRecord],
    events: Sequence[NewsEvent],
    minutes: int = NEWS_MINUTES,
) -> Finding:
    high = [e for e in events if e.impact.lower() == "high"]
    window = minutes * 60

    def near(trade: TradeRecord, event: NewsEvent) -> bool:
        close_in_time = abs(trade.open_time - event.time) <= window
        return close_in_time and event.currency.upper() in _currencies(trade.symbol)

    found = [t for t in trades if any(near(t, e) for e in high)]
    net = sum(t.net_profit for t in found)
    if not high:
        return Finding("Trading during news", False, "No high-impact events saved for this period.")
    detail = (
        f"{len(found)} trade(s) opened within {minutes} min of high-impact news; they made "
        f"{net:+,.2f}."
    )
    return Finding("Trading during news", bool(found), detail, len(found), net)


def lot_consistency(trades: Sequence[TradeRecord]) -> Finding:
    lots = [t.volume for t in trades if t.volume > 0]
    if len(lots) < 2:
        return Finding("Lot-size inconsistency", False, "Needs two trades or more.")
    average = statistics.fmean(lots)
    variation = statistics.pstdev(lots) / average if average > 0 else 0.0
    detail = (
        f"Lots from {min(lots):g} to {max(lots):g}, average {average:.2f}; they vary by "
        f"{variation * 100:.0f}% (a fixed risk per trade usually varies less than "
        f"{LOT_VARIATION * 100:.0f}%)."
    )
    return Finding("Lot-size inconsistency", variation > LOT_VARIATION, detail)


def outside_best_hours(trades: Sequence[TradeRecord], best: int = BEST_HOURS) -> Finding:
    hours: dict[int, list[TradeRecord]] = {}
    for trade in trades:
        hours.setdefault(datetime.fromtimestamp(trade.open_time, UTC).hour, []).append(trade)
    if len(hours) <= best:
        return Finding("Trading outside your best hours", False, "Too few different hours.")
    ranked = sorted(hours, key=lambda h: sum(t.net_profit for t in hours[h]), reverse=True)
    top = sorted(ranked[:best])
    outside = [t for h in ranked[best:] for t in hours[h]]
    net = sum(t.net_profit for t in outside)
    names = ", ".join(f"{h:02d}:00" for h in top)
    detail = (
        f"Best hours (UTC): {names}. {len(outside)} of {len(trades)} trades were outside "
        f"them and made {net:+,.2f}."
    )
    return Finding("Trading outside your best hours", net < 0, detail, len(outside), net)


def behavior_report(
    trades: Sequence[TradeRecord],
    events: Sequence[NewsEvent] = (),
) -> list[Finding]:
    """The checks for manual trades (bot trades follow fixed rules)."""
    manual = [t for t in ordered(trades) if t.source != "bot"]
    if len(manual) < MIN_TRADES:
        text = f"{len(manual)} manual trades: the checks need at least {MIN_TRADES}."
        return [Finding("Manual trades", False, text, len(manual))]
    checks: list[Callable[[], Finding]] = [
        lambda: overtrading(manual),
        lambda: revenge_trading(manual),
        lambda: holding_losers(manual),
        lambda: news_trading(manual, events),
        lambda: lot_consistency(manual),
        lambda: outside_best_hours(manual),
    ]
    return [check() for check in checks]
