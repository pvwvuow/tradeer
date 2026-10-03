"""The live broker (spec C7): real orders through the MT5 gateway thread.

Every order is checked with `order_check` before it is sent. A send is retried only on the
safe return codes of `app.mt5.retcodes`, with a fresh price each time; after an uncertain
answer (timeout, lost connection) the broker first looks for the order by its comment, so a
retry can never open the same trade twice. Every request and answer is returned as an
`Attempt` for the `mt5_requests` table and the `execution` log.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import replace
from typing import Any

from app.brokers.base import Attempt, BrokerOrder, BrokerPosition, OrderResult
from app.brokers.requests import (
    OrderPlan,
    cancel_request,
    choose_filling,
    close_request,
    modify_request,
    open_request,
)
from app.domain.history import Deal
from app.domain.orders import exit_price, market_price
from app.domain.signals import Direction, OrderType
from app.mt5.api import MT5Api
from app.mt5.gateway import MT5Gateway
from app.mt5.history_sync import deal_from_mt5
from app.mt5.retcodes import Policy, describe, retcode_text

SEND_TIMEOUT_SECONDS = 60.0
RETRY_PAUSE_SECONDS = 0.3
UNCERTAIN = frozenset({10012, 10031})  # TIMEOUT, CONNECTION: the order may have arrived

Pause = Callable[[float], None]
Timer = Callable[[], float]


def as_mapping(value: Any) -> dict[str, Any]:
    """A result record as a plain dict (nested records too), for logging and storage."""
    if value is None:
        return {}
    as_dict = getattr(value, "_asdict", None)
    if callable(as_dict):
        raw: Mapping[str, Any] = as_dict()
    elif isinstance(value, Mapping):
        raw = value
    else:
        raw = {name: getattr(value, name) for name in dir(value) if not name.startswith("_")}
        raw = {name: item for name, item in raw.items() if not callable(item)}
    found: dict[str, Any] = {}
    for name, item in raw.items():
        if hasattr(item, "_asdict") or hasattr(item, "__dict__"):
            found[name] = as_mapping(item)
        elif isinstance(item, bool | int | float | str) or item is None:
            found[name] = item
        else:
            found[name] = str(item)
    return found


def _last_error(mt5: MT5Api) -> str:
    try:
        code, text = mt5.last_error()
    except Exception:
        return ""
    return f"{code} {text}"


def _number(source: Any, name: str, default: float = 0.0) -> float:
    try:
        value = float(getattr(source, name, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _attempt(
    mt5: MT5Api,
    action: str,
    request: Mapping[str, Any],
    result: Any,
    latency_ms: float,
    attempt: int,
) -> Attempt:
    code = getattr(result, "retcode", None) if result is not None else None
    if action == "order_check":
        text = "0 OK: the check passed" if code == 0 else retcode_text(code)
    else:
        text = retcode_text(code)
    return Attempt(
        action=action,
        request=dict(request),
        retcode=int(code) if code is not None else None,
        retcode_text=text,
        comment=str(getattr(result, "comment", "") or ""),
        latency_ms=round(latency_ms, 1),
        attempt=attempt,
        result=as_mapping(result),
        last_error="" if result is not None else _last_error(mt5),
    )


def _filling(mt5: MT5Api, symbol: str) -> int:
    info = mt5.symbol_info(symbol)
    return choose_filling(int(getattr(info, "filling_mode", 0) or 0)) if info else 0


def _fresh_price(mt5: MT5Api, symbol: str, direction: Direction, closing: bool) -> float | None:
    tick = mt5.symbol_info_tick(symbol)
    bid, ask = _number(tick, "bid"), _number(tick, "ask")
    if tick is None or bid <= 0 or ask <= 0:
        return None
    return exit_price(direction, bid, ask) if closing else market_price(direction, bid, ask)


def _position_of(mt5: MT5Api, result: Any) -> int:
    """The position a market deal opened: the deal's position id, else the order ticket."""
    deal = int(_number(result, "deal"))
    if deal:
        found = mt5.history_deals_get(ticket=deal)
        for raw in found or ():
            position = int(_number(raw, "position_id"))
            if position:
                return position
    return int(_number(result, "order"))


def _find_existing(mt5: MT5Api, plan: OrderPlan) -> tuple[int, float, bool] | None:
    """After an uncertain answer: the position or pending order with this comment, if any."""
    for raw in mt5.positions_get(symbol=plan.symbol) or ():
        if getattr(raw, "comment", "") == plan.comment and int(_number(raw, "magic")) == plan.magic:
            return int(_number(raw, "ticket")), _number(raw, "price_open"), False
    for raw in mt5.orders_get(symbol=plan.symbol) or ():
        if getattr(raw, "comment", "") == plan.comment and int(_number(raw, "magic")) == plan.magic:
            return int(_number(raw, "ticket")), _number(raw, "price_open"), True
    return None


def send_open(
    mt5: MT5Api,
    plan: OrderPlan,
    *,
    retries: int,
    pause: Pause = time.sleep,
    timer: Timer = time.perf_counter,
) -> OrderResult:
    """Check, then send a market or pending order; retry only on safe codes."""
    attempts: list[Attempt] = []
    market = plan.order_type is OrderType.MARKET
    if market:
        price = _fresh_price(mt5, plan.symbol, plan.direction, closing=False)
        if price is None:
            return OrderResult(False, None, "no fresh price from MT5", requested_price=plan.price)
        plan = replace(plan, price=price)
    request = open_request(plan)
    started = timer()
    checked = mt5.order_check(request)
    attempts.append(_attempt(mt5, "order_check", request, checked, (timer() - started) * 1e3, 1))
    if checked is None or getattr(checked, "retcode", None) != 0:
        text = attempts[-1].retcode_text
        why = f"order_check refused the order: {text}"
        return OrderResult(False, attempts[-1].retcode, why, attempts=tuple(attempts))
    for number in range(1, retries + 2):
        started = timer()
        result = mt5.order_send(request)
        latency = (timer() - started) * 1e3
        attempts.append(_attempt(mt5, "order_send", request, result, latency, number))
        code = attempts[-1].retcode
        info = describe(code)
        if info.ok:
            placed = info.policy is Policy.PLACED
            position = 0 if placed else _position_of(mt5, result)
            return OrderResult(
                True,
                code,
                attempts[-1].retcode_text,
                order=int(_number(result, "order")),
                deal=int(_number(result, "deal")),
                position=position,
                price=_number(result, "price", plan.price) or plan.price,
                volume=_number(result, "volume", plan.volume) or plan.volume,
                requested_price=float(request["price"]),
                attempts=tuple(attempts),
                placed=placed,
            )
        if info.policy is not Policy.RETRY or number > retries:
            break
        if code in UNCERTAIN:
            existing = _find_existing(mt5, plan)
            if existing is not None:
                ticket, price, pending = existing
                return OrderResult(
                    True,
                    code,
                    f"{attempts[-1].retcode_text}; the order had arrived (found by comment)",
                    order=ticket,
                    position=0 if pending else ticket,
                    price=price,
                    volume=plan.volume,
                    requested_price=float(request["price"]),
                    attempts=tuple(attempts),
                    placed=pending,
                )
        pause(RETRY_PAUSE_SECONDS)
        if market:
            fresh = _fresh_price(mt5, plan.symbol, plan.direction, closing=False)
            if fresh is not None:
                request = {**request, "price": round(fresh, plan.digits)}
    last = attempts[-1]
    return OrderResult(
        False,
        last.retcode,
        last.retcode_text,
        requested_price=float(request["price"]),
        attempts=tuple(attempts),
    )


def send_simple(
    mt5: MT5Api,
    request: Mapping[str, Any],
    *,
    retries: int,
    refresh: Callable[[MT5Api], float | None] | None = None,
    pause: Pause = time.sleep,
    timer: Timer = time.perf_counter,
) -> OrderResult:
    """Modify, close or cancel: send, retrying safe codes (closes with a fresh price)."""
    attempts: list[Attempt] = []
    body = dict(request)
    for number in range(1, retries + 2):
        if refresh is not None:
            price = refresh(mt5)
            if price is None:
                return OrderResult(False, None, "no fresh price from MT5", attempts=tuple(attempts))
            body["price"] = price
        started = timer()
        result = mt5.order_send(body)
        elapsed = (timer() - started) * 1e3
        attempts.append(_attempt(mt5, "order_send", body, result, elapsed, number))
        info = describe(attempts[-1].retcode)
        if info.ok:
            return OrderResult(
                True,
                attempts[-1].retcode,
                attempts[-1].retcode_text,
                order=int(_number(result, "order")),
                deal=int(_number(result, "deal")),
                price=_number(result, "price", math.nan),
                volume=_number(result, "volume"),
                attempts=tuple(attempts),
            )
        if info.policy is not Policy.RETRY or number > retries:
            break
        pause(RETRY_PAUSE_SECONDS)
    last = attempts[-1]
    return OrderResult(False, last.retcode, last.retcode_text, attempts=tuple(attempts))


def _direction(kind: Any) -> Direction:
    return Direction.LONG if int(kind or 0) in (0, 2, 4, 6) else Direction.SHORT


def read_positions(mt5: MT5Api, magics: Collection[int]) -> list[BrokerPosition] | None:
    raw = mt5.positions_get()
    if raw is None:
        return None
    return [
        BrokerPosition(
            ticket=int(_number(item, "ticket")),
            symbol=str(getattr(item, "symbol", "")),
            direction=_direction(getattr(item, "type", 0)),
            volume=_number(item, "volume"),
            price_open=_number(item, "price_open"),
            sl=_number(item, "sl"),
            tp=_number(item, "tp"),
            profit=_number(item, "profit"),
            swap=_number(item, "swap"),
            magic=int(_number(item, "magic")),
            comment=str(getattr(item, "comment", "") or ""),
            time=int(_number(item, "time")),
        )
        for item in raw
        if int(_number(item, "magic")) in magics
    ]


def read_orders(mt5: MT5Api, magics: Collection[int]) -> list[BrokerOrder] | None:
    raw = mt5.orders_get()
    if raw is None:
        return None
    return [
        BrokerOrder(
            ticket=int(_number(item, "ticket")),
            symbol=str(getattr(item, "symbol", "")),
            direction=_direction(getattr(item, "type", 0)),
            pending_type=int(_number(item, "type")),
            volume=_number(item, "volume_current") or _number(item, "volume_initial"),
            price=_number(item, "price_open"),
            sl=_number(item, "sl"),
            tp=_number(item, "tp"),
            magic=int(_number(item, "magic")),
            comment=str(getattr(item, "comment", "") or ""),
            expiration=int(_number(item, "time_expiration")),
        )
        for item in raw
        if int(_number(item, "magic")) in magics
    ]


def read_deals(mt5: MT5Api, position: int) -> list[Deal] | None:
    raw = mt5.history_deals_get(position=position)
    if raw is None:
        return None
    return [deal_from_mt5(item) for item in raw]


class LiveBroker:
    """`Broker` on the real account, through the gateway thread."""

    def __init__(
        self,
        gateway: MT5Gateway,
        *,
        retries: Callable[[], int] = lambda: 3,
        deviation: Callable[[], int] = lambda: 10,
        pause: Pause = time.sleep,
    ) -> None:
        self._gateway = gateway
        self._retries = retries
        self._deviation = deviation
        self._pause = pause

    @property
    def mode(self) -> str:
        return "live"

    def open(self, plan: OrderPlan) -> OrderResult:
        retries, pause = self._retries(), self._pause
        arguments = {"symbol": plan.symbol, "volume": plan.volume, "type": plan.mt5_type}
        return self._gateway.run(
            "order_send",
            lambda mt5: send_open(mt5, plan_filling(mt5, plan), retries=retries, pause=pause),
            timeout=SEND_TIMEOUT_SECONDS,
            arguments=arguments,
        )

    def modify(self, position: BrokerPosition, sl: float, tp: float) -> OrderResult:
        request = modify_request(position.ticket, position.symbol, sl, tp, position.magic)
        retries, pause = self._retries(), self._pause
        return self._gateway.run(
            "order_modify",
            lambda mt5: send_simple(mt5, request, retries=retries, pause=pause),
            timeout=SEND_TIMEOUT_SECONDS,
            arguments={"position": position.ticket, "sl": sl, "tp": tp},
        )

    def close(self, position: BrokerPosition, volume: float | None = None) -> OrderResult:
        amount = position.volume if volume is None else volume
        retries, pause, deviation = self._retries(), self._pause, self._deviation()

        def work(mt5: MT5Api) -> OrderResult:
            request = close_request(
                position.ticket,
                position.symbol,
                position.direction,
                amount,
                0.0,
                deviation=deviation,
                magic=position.magic,
                comment=position.comment,
                filling=_filling(mt5, position.symbol),
            )
            return send_simple(
                mt5,
                request,
                retries=retries,
                refresh=lambda api_: _fresh_price(
                    api_,
                    position.symbol,
                    position.direction,
                    closing=True,
                ),
                pause=pause,
            )

        return self._gateway.run(
            "order_close",
            work,
            timeout=SEND_TIMEOUT_SECONDS,
            arguments={"position": position.ticket, "volume": amount},
        )

    def cancel(self, order: BrokerOrder) -> OrderResult:
        request = cancel_request(order.ticket)
        retries, pause = self._retries(), self._pause
        return self._gateway.run(
            "order_cancel",
            lambda mt5: send_simple(mt5, request, retries=retries, pause=pause),
            timeout=SEND_TIMEOUT_SECONDS,
            arguments={"order": order.ticket},
        )

    def positions(self, magics: Collection[int]) -> list[BrokerPosition] | None:
        found = frozenset(magics)
        return self._gateway.run("positions_get", lambda mt5: read_positions(mt5, found))

    def orders(self, magics: Collection[int]) -> list[BrokerOrder] | None:
        found = frozenset(magics)
        return self._gateway.run("orders_get", lambda mt5: read_orders(mt5, found))

    def deals(self, position: int) -> Sequence[Deal] | None:
        return self._gateway.run(
            "history_deals_get",
            lambda mt5: read_deals(mt5, position),
            arguments={"position": position},
        )


def plan_filling(mt5: MT5Api, plan: OrderPlan) -> OrderPlan:
    """The plan with the symbol's filling mode (read in the gateway thread)."""
    return replace(plan, filling=_filling(mt5, plan.symbol))
