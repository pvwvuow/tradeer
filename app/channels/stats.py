"""Each channel's record (docs/SIGNAL_DESK.md 3.5, 3.7 and 3.8, phases 21d2 and 21e). Pure.

- **Paper trial**: after 20 resolved shadow signals or 14 days the page shows the result
  and offers Live.
- **Duplicates**: the same symbol and side, the entry within 0.2 ATR, within 15 minutes,
  from two or more channels: one card with every source; taking it books it to the first
  channel, the others count it as a shadow trade.
- **Stats and ranking**: signals, taken, skipped, refused (by reason), win rate, average R,
  profit in money and in % of the budget, max drawdown, profit factor, latency (message to
  send), slippage (signal price to fill), deleted signals and edits after the result, and the
  shadow results of every signal. The ranking sorts by the shadow average R once a channel
  has 20 resolved signals; under that it says "too few signals to rank".
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.signals import Direction

TRIAL_SIGNALS = 20
TRIAL_DAYS = 14
RANK_SIGNALS = 20
DUPLICATE_SECONDS = 15 * 60
DUPLICATE_ATR = 0.2


@dataclass(frozen=True)
class Trial:
    resolved: int
    days: float
    done: bool
    average_r: float | None

    def text(self) -> str:
        found = "no result yet" if self.average_r is None else f"average {self.average_r:+.2f} R"
        if self.done:
            return f"Paper trial done: {self.resolved} signals, {found}. You can set it to Live."
        return (
            f"Paper trial: {self.resolved} of {TRIAL_SIGNALS} signals resolved, day "
            f"{int(self.days)} of {TRIAL_DAYS} ({found})"
        )


def trial(shadow_r: Sequence[float], started: float, now: float) -> Trial:
    days = max(0.0, (now - started) / 86_400.0)
    average = sum(shadow_r) / len(shadow_r) if shadow_r else None
    done = len(shadow_r) >= TRIAL_SIGNALS or days >= TRIAL_DAYS
    return Trial(len(shadow_r), days, done, average)


@dataclass(frozen=True)
class Card:
    """A card that waits (or a signal just seen) for the duplicate check."""

    key: str  # the card's request id
    channel: str
    symbol: str
    direction: Direction
    entry: float  # the planned entry (or the price when it came)
    at: float


def duplicate_of(new: Card, cards: Sequence[Card], atr: float) -> Card | None:
    """The earlier card of another channel this signal repeats, if any."""
    room = DUPLICATE_ATR * atr if math.isfinite(atr) and atr > 0 else 0.0
    for card in sorted(cards, key=lambda item: item.at):
        if (
            card.channel != new.channel
            and card.symbol == new.symbol
            and card.direction is new.direction
            and 0 <= new.at - card.at <= DUPLICATE_SECONDS
            and abs(card.entry - new.entry) <= room
        ):
            return card
    return None


@dataclass(frozen=True)
class Trade:
    """One closed trade of the channel (its magic), from the trades table."""

    profit: float  # net, account currency
    r: float | None
    slippage: float | None  # points
    latency: float | None  # seconds from the message to the send


@dataclass(frozen=True)
class ChannelStats:
    signals: int
    taken: int
    skipped: int
    refused: Counter[str]
    trades: int
    win_rate: float | None
    average_r: float | None
    profit: float
    profit_percent: float | None  # of the budget
    max_drawdown: float
    profit_factor: float | None
    latency: float | None
    slippage: float | None
    deleted: int
    edited_after: int
    shadow_signals: int
    shadow_win_rate: float | None
    shadow_average_r: float | None

    def lines(self, currency: str = "") -> list[str]:
        money = f" {currency}" if currency else ""
        lines = [
            f"Signals {self.signals}: taken {self.taken}, skipped {self.skipped}, "
            f"refused {sum(self.refused.values())}",
        ]
        if self.refused:
            why = ", ".join(f"{reason} {count}" for reason, count in self.refused.most_common())
            lines.append(f"Refused: {why}")
        if self.trades:
            rate = _percent(self.win_rate)
            share = self.profit_percent
            part = f" ({share:+.1f}% of the budget)" if share else ""
            lines.append(
                f"Your trades {self.trades}: win rate {rate}, average {_r(self.average_r)}, "
                f"profit {self.profit:+,.2f}{money}{part}, max drawdown "
                f"{self.max_drawdown:,.2f}{money}, profit factor {_number(self.profit_factor)}",
            )
        else:
            lines.append("Your trades: none closed yet")
        if self.latency is not None or self.slippage is not None:
            latency, slippage = _number(self.latency, "s"), _number(self.slippage, " pt")
            lines.append(f"Latency {latency}, slippage {slippage}")
        lines.append(
            f"Shadow (every signal): {self.shadow_signals} resolved, win rate "
            f"{_percent(self.shadow_win_rate)}, average {_r(self.shadow_average_r)}",
        )
        if self.deleted or self.edited_after:
            lines.append(
                f"Deleted by the channel {self.deleted}, edited after the result "
                f"{self.edited_after}",
            )
        return lines


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def _r(value: float | None) -> str:
    return "-" if value is None else f"{value:+.2f} R"


def _number(value: float | None, unit: str = "") -> str:
    return "-" if value is None else f"{value:,.1f}{unit}"


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def drawdown(profits: Sequence[float]) -> float:
    """The largest fall of the running result, from its peak (money, >= 0)."""
    peak = level = worst = 0.0
    for profit in profits:
        level += profit
        peak = max(peak, level)
        worst = max(worst, peak - level)
    return worst


def channel_stats(
    actions: Sequence[str],
    refused: Sequence[str],
    taken: int,
    trades: Sequence[Trade],
    shadow_r: Sequence[float],
    budget: float,
    *,
    deleted: int = 0,
    edited_after: int = 0,
) -> ChannelStats:
    """`actions` are the feed's decisions of every signal (card, paper, skip), `refused` the
    reasons of the skipped ones, `taken` the signals you confirmed, `trades` the closed ones
    oldest first, `shadow_r` one average R per resolved shadow signal."""
    cards = sum(1 for action in actions if action == "card")
    wins = [trade for trade in trades if trade.profit > 0]
    gains = sum(trade.profit for trade in wins)
    losses = -sum(trade.profit for trade in trades if trade.profit < 0)
    rs = [trade.r for trade in trades if trade.r is not None and math.isfinite(trade.r)]
    profit = sum(trade.profit for trade in trades)
    latencies = [t.latency for t in trades if t.latency is not None and t.latency >= 0]
    slippages = [t.slippage for t in trades if t.slippage is not None]
    shadow_wins = sum(1 for value in shadow_r if value > 0)
    return ChannelStats(
        signals=len(actions),
        taken=taken,
        skipped=max(0, cards - taken),
        refused=Counter(refused),
        trades=len(trades),
        win_rate=len(wins) / len(trades) if trades else None,
        average_r=_mean(rs),
        profit=round(profit, 2),
        profit_percent=round(profit / budget * 100.0, 2) if budget > 0 else None,
        max_drawdown=round(drawdown([trade.profit for trade in trades]), 2),
        profit_factor=gains / losses if losses > 0 else None,
        latency=_mean(latencies),
        slippage=_mean(slippages),
        deleted=deleted,
        edited_after=edited_after,
        shadow_signals=len(shadow_r),
        shadow_win_rate=shadow_wins / len(shadow_r) if shadow_r else None,
        shadow_average_r=_mean(list(shadow_r)),
    )


def ranking(stats: Sequence[tuple[str, ChannelStats]]) -> list[str]:
    """The channels best first by the shadow average R (20 resolved signals at least)."""
    ranked = [
        (title, found)
        for title, found in stats
        if found.shadow_signals >= RANK_SIGNALS and found.shadow_average_r is not None
    ]
    ranked.sort(key=lambda item: item[1].shadow_average_r or 0.0, reverse=True)
    lines = [
        f"{number}. {title}: {_r(found.shadow_average_r)} over {found.shadow_signals} signals"
        for number, (title, found) in enumerate(ranked, start=1)
    ]
    names = {title for title, _found in ranked}
    lines += [f"{title}: too few signals to rank" for title, _found in stats if title not in names]
    return lines
