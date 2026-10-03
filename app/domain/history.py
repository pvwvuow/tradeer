"""Rebuild trades from MT5 deals (spec C11, table `trades` of E2). Pure: no MT5, no database.

MT5 history is a list of deals. A position has one or more entry deals (`IN`) and exit deals
(`OUT`, `OUT_BY`, or `INOUT` when a netting position is reversed). Commission, swap and fees
are booked on the deals, so the net result of a trade is the sum over all of its deals.
The deal constants mirror MetaTrader 5 (`app.mt5.api` holds the same values and a Windows
test compares those with the real package).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1
DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT = 1
DEAL_ENTRY_INOUT = 2
DEAL_ENTRY_OUT_BY = 3
EXIT_ENTRIES = (DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY, DEAL_ENTRY_INOUT)

# DEAL_REASON_* -> exit reason in plain words.
EXIT_REASONS: dict[int, str] = {
    0: "manual",
    1: "manual (mobile)",
    2: "manual (web)",
    3: "expert",
    4: "stop loss",
    5: "take profit",
    6: "stop out",
}
BREAKEVEN_MONEY = 0.005
VOLUME_EPSILON = 1e-9


@dataclass(frozen=True)
class Deal:
    ticket: int
    order: int
    time: int
    time_msc: int
    type: int
    entry: int
    magic: int
    position_id: int
    reason: int
    volume: float
    price: float
    commission: float
    swap: float
    profit: float
    fee: float
    symbol: str
    comment: str

    @property
    def is_trade(self) -> bool:
        """Buy and sell deals of a position (not balance, credit or other money deals)."""
        return self.type in (DEAL_TYPE_BUY, DEAL_TYPE_SELL) and self.position_id != 0

    @property
    def sort_key(self) -> tuple[int, int]:
        return (self.time_msc or self.time * 1000, self.ticket)


@dataclass(frozen=True)
class Order:
    ticket: int
    time_setup: int
    time_done: int
    type: int
    state: int
    magic: int
    position_id: int
    volume_initial: float
    volume_current: float
    price_open: float
    sl: float
    tp: float
    price_current: float
    symbol: str
    comment: str


@dataclass(frozen=True)
class PositionSummary:
    """One trade rebuilt from the deals of one position. Times are broker server time."""

    position_id: int
    symbol: str
    direction: str
    magic: int
    volume: float
    open_time: int
    open_price: float
    closed_volume: float
    close_time: int | None
    close_price: float | None
    profit: float
    commission: float
    swap: float
    fee: float
    deals: int
    exit_reason: str | None

    @property
    def closed(self) -> bool:
        return self.close_time is not None

    @property
    def net_profit(self) -> float:
        return round(self.profit + self.commission + self.swap + self.fee, 8)

    @property
    def outcome(self) -> str:
        if not self.closed:
            return "open"
        if self.net_profit > BREAKEVEN_MONEY:
            return "win"
        if self.net_profit < -BREAKEVEN_MONEY:
            return "loss"
        return "breakeven"

    @property
    def duration_sec(self) -> int | None:
        return self.close_time - self.open_time if self.close_time is not None else None


def _average_price(deals: Sequence[Deal]) -> float:
    volume = sum(deal.volume for deal in deals)
    if volume <= 0:
        return deals[-1].price
    return round(sum(deal.price * deal.volume for deal in deals) / volume, 10)


def _money(values: Iterable[float]) -> float:
    return round(sum(values), 8)


def summarize_position(deals: Sequence[Deal]) -> PositionSummary | None:
    """The trade of one position, or None when its entry deal is not in the history."""
    ordered = sorted(deals, key=lambda deal: deal.sort_key)
    entries = [deal for deal in ordered if deal.entry == DEAL_ENTRY_IN]
    exits = [deal for deal in ordered if deal.entry in EXIT_ENTRIES]
    if not entries:
        return None
    first = entries[0]
    volume = round(sum(deal.volume for deal in entries), 8)
    closed_volume = round(sum(deal.volume for deal in exits), 8)
    closed = bool(exits) and closed_volume >= volume - VOLUME_EPSILON
    last_exit = exits[-1] if exits else None
    return PositionSummary(
        position_id=first.position_id,
        symbol=first.symbol,
        direction="buy" if first.type == DEAL_TYPE_BUY else "sell",
        magic=first.magic,
        volume=volume,
        open_time=first.time,
        open_price=_average_price(entries),
        closed_volume=closed_volume,
        close_time=last_exit.time if closed and last_exit is not None else None,
        close_price=_average_price(exits) if exits else None,
        profit=_money(deal.profit for deal in ordered),
        commission=_money(deal.commission for deal in ordered),
        swap=_money(deal.swap for deal in ordered),
        fee=_money(deal.fee for deal in ordered),
        deals=len(ordered),
        exit_reason=EXIT_REASONS.get(last_exit.reason) if closed and last_exit else None,
    )


def summarize_positions(deals: Iterable[Deal]) -> list[PositionSummary]:
    """Every position found in `deals`, oldest first. Money deals are ignored."""
    groups: dict[int, list[Deal]] = {}
    for deal in deals:
        if deal.is_trade:
            groups.setdefault(deal.position_id, []).append(deal)
    found = [summarize_position(group) for group in groups.values()]
    summaries = [summary for summary in found if summary is not None]
    return sorted(summaries, key=lambda summary: (summary.open_time, summary.position_id))


def trade_source(magic: int, bot_magics: Collection[int] = ()) -> str:
    """`bot` for this app's magic numbers, `manual` for magic 0, `external` for other EAs."""
    if magic in bot_magics:
        return "bot"
    return "manual" if magic == 0 else "external"


def server_time_to_utc(server_time: int, offset_hours: float) -> str:
    """Broker server time (written as if it were UTC) -> real UTC as ISO 8601 text."""
    moment = datetime.fromtimestamp(server_time - offset_hours * 3600, UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")
