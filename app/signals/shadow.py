"""Shadow results (docs/SIGNAL_DESK.md 3.5, phase 21d2): every parsed signal of a channel is
followed on the price history as if it had been taken, also the ones you skipped and every
signal of a Paper trial, so "what if I took them all" is known per channel. Pure numpy.

The rules are the shadow rules of docs/AI_DESK.md section 2: bars are bid prices with the
spread in points; a buy fills and a sell exits at the ask; when the stop loss and the target
are both inside one bar, the stop loss came first. The fill starts at the message time plus
the measured latency (not when you confirmed), so the comparison with your own trades is
fair. A market signal fills at the open of the first bar after that moment; a limit or stop
signal fills when the price reaches its entry before it expires. On the bar a pending order
fills only its stop loss counts (the target from the next bar), which never flatters it.
A channel's follow-ups count too: "close" closes it at the next bar's open, "cancel" drops
an entry that was not reached, "move SL to entry" makes the stop the fill price.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from app.analysis.bars import Bars
from app.domain.signals import Direction, OrderType
from app.signals.base_rate import first_hit
from app.signals.parse import ParsedSignal

HORIZON_SECONDS = 7 * 86_400  # a shadow trade is followed for at most a week
TIMEFRAMES = ("M1", "M5", "M15")  # the finest one the history has


class ShadowState(StrEnum):
    WAITING = "waiting"  # not filled yet (or no bar after the message yet)
    OPEN = "open"
    WIN = "win"
    LOSS = "loss"
    EXPIRED = "expired"  # a pending entry that was never reached (or was cancelled)
    TIMEOUT = "timeout"  # open for a week: closed at the last price
    CLOSED = "closed"  # closed when the channel said so

    @property
    def resolved(self) -> bool:
        return self in (ShadowState.WIN, ShadowState.LOSS, ShadowState.TIMEOUT, ShadowState.CLOSED)


@dataclass(frozen=True)
class ShadowLeg:
    direction: Direction
    order: OrderType
    entry: float  # nan for a market order (it fills at the next bar's open)
    sl: float
    tp: float | None  # None: an open target (followed to the stop or the week's end)
    start: float  # the message time plus the latency, UTC seconds
    expires: float  # a pending entry is cancelled after this
    close_at: float | None = None  # the channel said close (or cancel) at this time
    break_even_at: float | None = None  # the channel said move the stop to the entry


@dataclass(frozen=True)
class ShadowResult:
    state: ShadowState
    fill_time: float | None = None
    fill_price: float | None = None
    exit_time: float | None = None
    exit_price: float | None = None
    r: float | None = None  # only when resolved


def legs_of(
    parsed: ParsedSignal,
    start: float,
    reference: float,
    pending_minutes: int,
) -> tuple[ShadowLeg, ...]:
    """One shadow leg per target of a complete signal. `reference` is the price when the
    message came (the first bar's open): it decides market, limit or stop when the text
    did not say, like the desk's plan does with the live price."""
    direction, sl = parsed.direction, parsed.sl
    if not parsed.complete or direction is None or sl is None:
        return ()
    long = direction is Direction.LONG
    order = parsed.order
    entry = math.nan
    if parsed.entry:
        low, high = min(parsed.entry), max(parsed.entry)
        if order is None:
            if low <= reference <= high:
                order = OrderType.MARKET
            elif long:
                order = OrderType.LIMIT if reference > high else OrderType.STOP
            else:
                order = OrderType.LIMIT if reference < low else OrderType.STOP
        if order is not OrderType.MARKET:
            nearer = high if (order is OrderType.LIMIT) == long else low
            entry = nearer if len(parsed.entry) > 1 else parsed.entry[0]
    order = order or OrderType.MARKET
    expires = start + 60.0 * pending_minutes
    targets: Sequence[float | None] = parsed.tps or (None,)
    return tuple(ShadowLeg(direction, order, entry, sl, tp, start, expires) for tp in targets)


def _r(leg: ShadowLeg, fill: float, exit_price: float) -> float:
    risk = abs(fill - leg.sl)
    if not risk > 0:
        return 0.0
    move = exit_price - fill if leg.direction is Direction.LONG else fill - exit_price
    return round(move / risk, 3)


def follow(leg: ShadowLeg, bars: Bars, point: float, now: float) -> ShadowResult:
    """Where the shadow trade stands on these bars (bid prices, spread in points)."""
    if not len(bars):
        return ShadowResult(ShadowState.WAITING)
    long = leg.direction is Direction.LONG
    spreads = bars.spread.astype(np.float64) * max(point, 0.0)
    times = bars.time.astype(np.float64)
    ends = times + bars.seconds
    closing = leg.close_at if leg.close_at is not None else math.inf
    if leg.order is OrderType.MARKET:
        after = np.nonzero(times >= leg.start)[0]
        if not len(after):
            return ShadowResult(ShadowState.WAITING)
        index = int(after[0])
        fill = float(bars.open[index] + (spreads[index] if long else 0.0))
        first_target = index
    else:
        live = np.nonzero((ends > leg.start) & (times < min(leg.expires, closing)))[0]
        if not len(live):
            gone = now >= min(leg.expires, closing) or float(times[-1]) >= leg.expires
            return ShadowResult(ShadowState.EXPIRED if gone else ShadowState.WAITING)
        low = bars.low[live] + (spreads[live] if long else 0.0)
        high = bars.high[live] + (spreads[live] if long else 0.0)
        limit = leg.order is OrderType.LIMIT
        if long:
            reached = low <= leg.entry if limit else high >= leg.entry
        else:
            reached = high >= leg.entry if limit else low <= leg.entry
        hit = first_hit(reached)
        if hit < 0:
            last_end = float(ends[live[-1]])
            gone = now >= min(leg.expires, closing) or last_end >= min(leg.expires, closing)
            return ShadowResult(ShadowState.EXPIRED if gone else ShadowState.WAITING)
        index = int(live[hit])
        fill = leg.entry
        first_target = index + 1
    fill_time = float(times[index])
    if fill_time >= closing:
        return ShadowResult(ShadowState.EXPIRED)  # the channel cancelled it first
    horizon = min(fill_time + HORIZON_SECONDS, closing)
    stop = int(np.searchsorted(times, horizon, side="left" if horizon == closing else "right"))
    span = slice(index, stop)
    gap = spreads[span]  # a sell exits at the ask
    stops = np.full(stop - index, leg.sl)
    if leg.break_even_at is not None:
        stops = np.where(times[span] >= leg.break_even_at, fill, stops)
    if long:
        lost = first_hit(bars.low[span] <= stops)
    else:
        lost = first_hit(bars.high[span] + gap >= stops)
    won = -1
    if leg.tp is not None:
        offset = first_target - index
        tail = slice(first_target, stop)
        if long:
            found = first_hit(bars.high[tail] >= leg.tp)
        else:
            found = first_hit(bars.low[tail] + spreads[tail] <= leg.tp)
        won = found + offset if found >= 0 else -1
    if lost >= 0 and (won < 0 or lost <= won):
        at = float(times[index + lost])
        level = float(stops[lost])
        state = ShadowState.LOSS if level == leg.sl else ShadowState.CLOSED  # stopped at entry
        return _closed(state, leg, fill_time, fill, at, level)
    if won >= 0 and leg.tp is not None:
        at = float(times[index + won])
        return _closed(ShadowState.WIN, leg, fill_time, fill, at, leg.tp)
    if horizon == closing and stop < len(times):  # the channel closed it
        price = float(bars.open[stop] + (0.0 if long else spreads[stop]))
        return _closed(ShadowState.CLOSED, leg, fill_time, fill, float(times[stop]), price)
    last = stop - 1
    if last >= index and float(ends[last]) >= fill_time + HORIZON_SECONDS:
        price = float(bars.close[last] + (0.0 if long else spreads[last]))
        return _closed(ShadowState.TIMEOUT, leg, fill_time, fill, float(ends[last]), price)
    return ShadowResult(ShadowState.OPEN, fill_time, fill)


def _closed(
    state: ShadowState,
    leg: ShadowLeg,
    fill_time: float,
    fill: float,
    at: float,
    price: float,
) -> ShadowResult:
    return ShadowResult(state, fill_time, fill, at, price, _r(leg, fill, price))
