"""From a parsed signal to an order the app can check (docs/SIGNAL_DESK.md 2.3).

Picks market, limit or stop the way a trader would read the message, then checks every
price against the live quote before a card is shown:

- no entry, or "now": a market order at the live price (the signal's price, if it gave one,
  must be within 0.3 ATR of it, else the price has moved);
- a zone: the live price inside it is a market order; outside, a pending order at the
  nearer edge (a limit when the price must come back to it, a stop when it must go on);
- one price without a kind: within 0.3 ATR a market order, else a limit or stop by side;
- an explicit limit or stop is kept and checked for the right side of the price.

Then: the stop loss on the losing side, every target on the winning side, a pending entry
within 2 ATR, the stop beyond the stops level plus the spread, and the reward to risk of
the first target (a warning under 1). Pure: the quote and the ATR come in as numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.domain.signals import Direction, OrderType
from app.signals.parse import ParsedSignal

MARKET_ATR = 0.3
PENDING_ATR = 2.0
MIN_RR = 1.0


@dataclass(frozen=True)
class Market:
    """The live numbers of the symbol (from MT5 or the paper broker)."""

    bid: float
    ask: float
    atr: float  # ATR(14) of the closed M15 bars
    digits: int = 5
    point: float = 0.00001
    stops_level: int = 0  # in points
    open: bool = True

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str = ""
    warning: bool = False  # shown on the card, does not block

    @property
    def blocks(self) -> bool:
        return not self.passed and not self.warning


@dataclass(frozen=True)
class OrderPlan:
    symbol: str
    direction: Direction | None
    order: OrderType | None
    entry: float
    sl: float
    tps: tuple[float, ...]  # the targets with a price, in the signal's order
    checks: tuple[Check, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and not any(check.blocks for check in self.checks)

    @property
    def problems(self) -> tuple[str, ...]:
        return tuple(f"{c.name}: {c.detail}" for c in self.checks if c.blocks)

    def rr(self, tp: float) -> float:
        risk = abs(self.entry - self.sl)
        return abs(tp - self.entry) / risk if risk > 0 else math.nan


def _kind(direction: Direction, entry: float, price: float) -> OrderType:
    """A limit when the price must come back to the entry, a stop when it must go on."""
    below = entry < price
    if direction is Direction.LONG:
        return OrderType.LIMIT if below else OrderType.STOP
    return OrderType.STOP if below else OrderType.LIMIT


def _resolve(signal: ParsedSignal, price: float, tolerance: float) -> tuple[OrderType, float]:
    assert signal.direction is not None
    wanted = signal.order
    if not signal.entry or wanted is OrderType.MARKET:
        return OrderType.MARKET, price
    if len(signal.entry) == 2:
        low, high = signal.entry
        if low <= price <= high:
            return OrderType.MARKET, price
        edge = high if price > high else low
        return wanted or _kind(signal.direction, edge, price), edge
    entry = signal.entry[0]
    if wanted is not None:
        return wanted, entry
    if abs(entry - price) <= tolerance:
        return OrderType.MARKET, price
    return _kind(signal.direction, entry, price), entry


def _side_check(direction: Direction, order: OrderType, entry: float, market: Market) -> Check:
    if order is OrderType.MARKET:
        return Check("order side", True, "market order at the live price")
    long = direction is Direction.LONG
    price = market.ask if long else market.bid
    above = (order is OrderType.STOP) == long  # a buy stop and a sell limit sit above
    passed = entry > price if above else entry < price
    side = "buy" if long else "sell"
    where = "above" if above else "below"
    quote = "ask" if long else "bid"
    return Check(
        "order side",
        passed,
        f"a {side} {order.value} must be {where} the {quote} ({price:.{market.digits}f})",
    )


def plan(signal: ParsedSignal, market: Market) -> OrderPlan:
    """The order and every check of section 2.3, nothing sent."""
    if not signal.complete:
        missing = ", ".join(signal.missing)
        check = Check("complete signal", False, f"missing: {missing}")
        tps = tuple(tp for tp in signal.tps if tp is not None)
        return OrderPlan(signal.symbol, signal.direction, None, math.nan, math.nan, tps, (check,))
    assert signal.direction is not None and signal.sl is not None
    direction = signal.direction
    digits = market.digits
    atr_ok = math.isfinite(market.atr) and market.atr > 0
    tolerance = MARKET_ATR * market.atr if atr_ok else 0.0
    price = market.ask if direction is Direction.LONG else market.bid
    order, entry = _resolve(signal, price, tolerance)
    entry = round(entry, digits)
    sl = round(signal.sl, digits)
    tps = tuple(round(tp, digits) for tp in signal.tps if tp is not None)
    notes: list[str] = []
    if any(tp is None for tp in signal.tps):
        notes.append("open targets are left out for now: each leg needs a take profit")
    checks = [
        Check("market open", market.open, "the symbol can be traded now"),
        Check("ATR", atr_ok, f"{market.atr:.{digits}f} (M15)" if atr_ok else "no ATR yet"),
        _side_check(direction, order, entry, market),
    ]
    sign = direction.sign
    checks.append(
        Check(
            "stop loss side",
            (entry - sl) * sign > 0,
            f"SL {sl:.{digits}f} must be {'below' if sign > 0 else 'above'} the entry",
        ),
    )
    wrong = [tp for tp in tps if (tp - entry) * sign <= 0]
    checks.append(
        Check(
            "targets side",
            bool(tps) and not wrong,
            "every TP beyond the entry" if not wrong else f"wrong side: {wrong}",
        ),
    )
    single = len(signal.entry) == 1
    distance = abs(price - signal.entry[0]) if single else 0.0  # a zone or no entry: 0
    if atr_ok and order is OrderType.MARKET:
        checks.append(
            Check(
                "price has not moved",
                distance <= tolerance + market.point / 2,
                f"{distance:.{digits}f} from the signal (at most {tolerance:.{digits}f})",
            ),
        )
    elif atr_ok:
        away = abs(entry - price)
        checks.append(
            Check(
                "entry distance",
                away <= PENDING_ATR * market.atr,
                f"{away / market.atr:.1f} ATR from the price (at most {PENDING_ATR:g})",
            ),
        )
    floor = market.stops_level * market.point + market.spread
    stop = abs(entry - sl)
    in_atr = f" = {stop / market.atr:.1f} ATR" if atr_ok else ""
    checks.append(
        Check(
            "stop distance",
            stop >= floor,
            f"{stop:.{digits}f}{in_atr}; stops level plus spread {floor:.{digits}f}",
        ),
    )
    if order is not OrderType.MARKET and market.stops_level:
        level = market.stops_level * market.point
        checks.append(
            Check(
                "pending distance",
                abs(entry - price) >= level,
                f"at least {level:.{digits}f} from the price (stops level)",
            ),
        )
    if tps and stop > 0:
        first = abs(tps[0] - entry) / stop
        checks.append(
            Check(
                "reward to risk",
                first >= MIN_RR,
                f"TP1 {first:.2f} R",
                warning=True,
            ),
        )
    return OrderPlan(
        signal.symbol,
        direction,
        order,
        entry,
        sl,
        tps,
        tuple(checks),
        tuple(notes),
    )
