"""MT5 trade requests (spec C7): every order carries its SL and TP (server-side stops are
mandatory), the symbol's filling mode, a deviation limit, the strategy's magic number and a
comment with the short signal id, so a position can always be traced back to its signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.signals import Direction, OrderType
from app.mt5 import api

# SYMBOL_FILLING_* flags of `symbol_info().filling_mode` (MQL5 values; not every version of
# the Python package exports them, so they live here and not in `app.mt5.api`).
FILLING_FLAG_FOK = 1
FILLING_FLAG_IOC = 2
COMMENT_LENGTH = 31  # MT5 cuts longer comments
EXPIRY_STEP_SECONDS = 60  # MT5 keeps the expiry of a pending order in whole minutes


@dataclass(frozen=True)
class OrderPlan:
    """One order the app wants to place."""

    symbol: str
    direction: Direction
    order_type: OrderType
    volume: float
    price: float  # the ask or bid for a market order, the entry of a pending order
    sl: float
    tp: float
    magic: int
    comment: str
    deviation: int  # points
    filling: int
    digits: int = 5
    expiration: int | None = None  # broker server time; None = good till cancelled

    @property
    def mt5_type(self) -> int:
        return order_type_code(self.direction, self.order_type)


def order_type_code(direction: Direction, order_type: OrderType) -> int:
    long = direction is Direction.LONG
    if order_type is OrderType.LIMIT:
        return api.ORDER_TYPE_BUY_LIMIT if long else api.ORDER_TYPE_SELL_LIMIT
    if order_type is OrderType.STOP:
        return api.ORDER_TYPE_BUY_STOP if long else api.ORDER_TYPE_SELL_STOP
    return api.ORDER_TYPE_BUY if long else api.ORDER_TYPE_SELL


def whole_minute(expiration: int) -> int:
    """The expiry moved up to the next whole minute (unchanged when it is one already).

    MT5 drops the seconds of a pending order's expiry: FIBO kept 16:19:59 as 16:19:00, and
    refused an order that this left under a minute away (10022 INVALID_EXPIRATION, PC demo
    test of 6 October 2026). Moved up, an order never ends before it is due and MT5 keeps
    the time as it was sent.
    """
    step = EXPIRY_STEP_SECONDS
    return -(-int(expiration) // step) * step


def choose_filling(flags: int) -> int:
    """FOK when the symbol allows it, else IOC, else RETURN (exchange-style symbols)."""
    if flags & FILLING_FLAG_FOK:
        return api.ORDER_FILLING_FOK
    if flags & FILLING_FLAG_IOC:
        return api.ORDER_FILLING_IOC
    return api.ORDER_FILLING_RETURN


def filling_name(filling: int) -> str:
    names = {
        api.ORDER_FILLING_FOK: "FOK",
        api.ORDER_FILLING_IOC: "IOC",
        api.ORDER_FILLING_RETURN: "RETURN",
    }
    return names.get(filling, str(filling))


def open_request(plan: OrderPlan) -> dict[str, Any]:
    """The trade request for a market or pending order, SL and TP included."""
    pending = plan.order_type is not OrderType.MARKET
    request: dict[str, Any] = {
        "action": api.TRADE_ACTION_PENDING if pending else api.TRADE_ACTION_DEAL,
        "symbol": plan.symbol,
        "volume": float(plan.volume),
        "type": plan.mt5_type,
        "price": round(plan.price, plan.digits),
        "sl": round(plan.sl, plan.digits),
        "tp": round(plan.tp, plan.digits),
        "deviation": int(plan.deviation),
        "magic": int(plan.magic),
        "comment": plan.comment[:COMMENT_LENGTH],
        "type_filling": int(plan.filling),
        "type_time": api.ORDER_TIME_GTC,
    }
    if pending and plan.expiration is not None:
        request["type_time"] = api.ORDER_TIME_SPECIFIED
        request["expiration"] = whole_minute(plan.expiration)
    return request


def modify_request(ticket: int, symbol: str, sl: float, tp: float, magic: int) -> dict[str, Any]:
    """Change the SL and TP of an open position (TRADE_ACTION_SLTP)."""
    return {
        "action": api.TRADE_ACTION_SLTP,
        "position": int(ticket),
        "symbol": symbol,
        "sl": float(sl),
        "tp": float(tp),
        "magic": int(magic),
    }


def close_request(
    ticket: int,
    symbol: str,
    direction: Direction,
    volume: float,
    price: float,
    *,
    deviation: int,
    magic: int,
    comment: str,
    filling: int,
) -> dict[str, Any]:
    """Close (all or part of) a position with an opposite deal on that position."""
    closing = api.ORDER_TYPE_SELL if direction is Direction.LONG else api.ORDER_TYPE_BUY
    return {
        "action": api.TRADE_ACTION_DEAL,
        "position": int(ticket),
        "symbol": symbol,
        "volume": float(volume),
        "type": closing,
        "price": float(price),
        "deviation": int(deviation),
        "magic": int(magic),
        "comment": comment[:COMMENT_LENGTH],
        "type_filling": int(filling),
        "type_time": api.ORDER_TIME_GTC,
    }


def cancel_request(order_ticket: int) -> dict[str, Any]:
    return {"action": api.TRADE_ACTION_REMOVE, "order": int(order_ticket)}
