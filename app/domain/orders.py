"""Prices and the checks before an order is sent (spec C5, C7). Pure: no MT5, no I/O.

Buy at the ask, sell at the bid. The checks run when the user approves a signal: is it still
valid, did the price move too far from the entry, is the spread acceptable, are the stops far
enough from the price? Every check becomes a line of the decision trace.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.domain.signals import Direction, OrderType, Signal

COMMENT_PREFIX = "tw-"


@dataclass(frozen=True)
class Check:
    """One check before sending, shown in the decision trace."""

    name: str
    passed: bool
    value: float | str | None
    threshold: float | str | None
    detail: str


def short_id(signal_id: str) -> str:
    return f"{COMMENT_PREFIX}{signal_id.replace('-', '')[:10]}"


def round_price(value: float, digits: int, tick_size: float = 0.0) -> float:
    """The price on the symbol's grid: a multiple of the tick size, `digits` decimals."""
    if not math.isfinite(value):
        return value
    if tick_size > 0:
        value = round(value / tick_size) * tick_size
    return round(value, digits)


def market_price(direction: Direction, bid: float, ask: float) -> float:
    return ask if direction is Direction.LONG else bid


def exit_price(direction: Direction, bid: float, ask: float) -> float:
    """A long closes at the bid, a short at the ask."""
    return bid if direction is Direction.LONG else ask


def stop_distance_check(
    direction: Direction,
    price: float,
    sl: float,
    tp: float,
    *,
    stops_level: int,
    point: float,
) -> Check:
    """SL and TP on the right side and at least the broker's stops level away."""
    sign = direction.sign
    if (price - sl) * sign <= 0 or (tp - price) * sign <= 0:
        return Check("stops", False, None, None, "the SL or TP is on the wrong side of the price")
    if point <= 0:
        return Check("stops", True, None, stops_level, "the symbol's point size is unknown")
    nearest = min(abs(price - sl), abs(tp - price)) / point
    passed = nearest >= stops_level
    detail = "points to the nearer of SL and TP vs the broker's stops level"
    return Check("stops", passed, round(nearest, 1), stops_level, detail)


def frozen(price: float, level: float, freeze_level: int, point: float) -> bool:
    """MT5 refuses to change an SL or TP within the freeze level of the current price."""
    return freeze_level > 0 and point > 0 and abs(price - level) / point < freeze_level


def recheck_at_approval(
    signal: Signal,
    bid: float,
    ask: float,
    *,
    max_entry_move_r: float,
    max_spread_sl_fraction: float,
    stops_level: int,
    point: float,
    now: float,
) -> list[Check]:
    """Before sending (spec C5): still valid, price within the entry tolerance, spread."""
    checks = [
        Check(
            "not expired",
            now < signal.expires_at,
            round(signal.expires_at - now),
            0,
            "seconds until the signal expires",
        ),
    ]
    if not (bid > 0 and ask > 0 and ask >= bid):
        checks.append(Check("fresh price", False, None, None, "no valid bid and ask from MT5"))
        return checks
    spread = ask - bid
    limit = max_spread_sl_fraction * signal.risk
    checks.append(
        Check(
            "spread",
            spread <= limit,
            round(spread / signal.risk, 3) if signal.risk > 0 else None,
            max_spread_sl_fraction,
            "spread as a fraction of the SL distance",
        ),
    )
    sign = signal.direction.sign
    if signal.order_type is OrderType.MARKET:
        current = market_price(signal.direction, bid, ask)
        move = (current - signal.entry) * sign / signal.risk if signal.risk > 0 else math.inf
        checks.append(
            Check(
                "entry tolerance",
                abs(move) <= max_entry_move_r,
                round(move, 3),
                max_entry_move_r,
                "how far the price moved from the signal's entry, in R",
            ),
        )
        checks.append(
            stop_distance_check(
                signal.direction,
                current,
                signal.sl,
                signal.tp,
                stops_level=stops_level,
                point=point,
            ),
        )
        return checks
    # Pending orders: the entry is fixed; the market must still be on the right side of it.
    current = market_price(signal.direction, bid, ask)
    stop = signal.order_type is OrderType.STOP
    away = (signal.entry - current) * sign if stop else (current - signal.entry) * sign
    points = away / point if point > 0 else math.nan
    checks.append(
        Check(
            "pending entry",
            away > 0 and (not math.isfinite(points) or points >= stops_level),
            round(points, 1) if math.isfinite(points) else None,
            stops_level,
            "points from the price to the pending entry vs the stops level",
        ),
    )
    return checks


def failed(checks: list[Check]) -> list[Check]:
    return [check for check in checks if not check.passed]
