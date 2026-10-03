"""`--mt5-trade-test --symbol EURUSD` (spec I4): proves the full real order path on a DEMO
account, step by step: open the minimum lot with SL and TP, check it in MT5 (magic number,
stops), move the SL, close it, read the closed deal back from the history.

It refuses to run on a REAL (or unknown) account and on an investor-password login. Every
request and its return code is printed, so a failure shows exactly where the path broke.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from app.brokers.base import OrderResult
from app.brokers.live_broker import (
    plan_filling,
    read_deals,
    read_positions,
    send_open,
    send_simple,
)
from app.brokers.market import read_quote, read_spec
from app.brokers.requests import OrderPlan, close_request, filling_name, modify_request
from app.domain.history import summarize_position
from app.domain.orders import exit_price, round_price
from app.domain.signals import Direction, OrderType
from app.mt5.api import MT5Api
from app.mt5.checklist import ConnectRequest, run_checklist
from app.mt5.models import AccountKind
from app.mt5.symbols import resolve_symbol

TEST_MAGIC = 26_070_099
TEST_COMMENT = "tw-trade-test"
DEVIATION_POINTS = 20
RETRIES = 2
DEAL_WAIT_SECONDS = 10.0
OK_MARK = "[\u2713]"
FAIL_MARK = "[\u2717]"

Emit = Callable[[str], None]


def _attempts(emit: Emit, result: OrderResult) -> None:
    for attempt in result.attempts:
        latency = f"{attempt.latency_ms:.0f} ms"
        emit(f"      {attempt.action} #{attempt.attempt}: {attempt.retcode_text} ({latency})")


def _step(emit: Emit, ok: bool, text: str) -> bool:
    emit(f"{OK_MARK if ok else FAIL_MARK} {text}")
    return ok


def stop_distance(point: float, stops_level: int, spread: float) -> float:
    """SL/TP distance for the test: well outside the stops level and the spread."""
    return max((stops_level + 20) * point, 20 * spread, 100 * point)


def run_trade_test(
    mt5: MT5Api,
    request: ConnectRequest,
    symbol: str,
    emit: Emit,
    *,
    pause: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    path_exists: Callable[[str], bool] = os.path.exists,
    elevated: bool | None = None,
) -> bool:
    emit("MT5 trade test (DEMO accounts only: opens and closes one minimum-lot trade)")
    report = run_checklist(mt5, request, path_exists=path_exists, elevated=elevated)
    account = report.account
    if not report.connected or account is None:
        problem = report.first_problem()
        _step(emit, False, f"Not connected: {problem.value if problem else 'see the checklist'}")
        emit("Result: FAIL")
        return False
    emit(f"Account: {account.summary()}")
    if account.kind is not AccountKind.DEMO:
        _step(emit, False, f"Refused: this is a {account.kind.value} account, not DEMO")
        emit("Result: FAIL (nothing was sent)")
        return False
    if account.read_only:
        _step(emit, False, "Refused: investor (read-only) login, the account cannot trade")
        emit("Result: FAIL (nothing was sent)")
        return False
    names = [str(getattr(item, "name", "")) for item in (mt5.symbols_get() or ())]
    name = resolve_symbol(symbol, names)
    if name is None:
        _step(emit, False, f"{symbol} is not available at this broker")
        emit("Result: FAIL (nothing was sent)")
        return False
    mt5.symbol_select(name, True)
    spec = read_spec(mt5, name)
    quote = read_quote(mt5, name)
    if spec is None or quote is None:
        _step(emit, False, f"No symbol data or live price for {name} (is the market open?)")
        emit("Result: FAIL (nothing was sent)")
        return False
    point = spec.point if spec.point > 0 else 10.0**-spec.digits
    distance = stop_distance(point, spec.stops_level, quote.ask - quote.bid)
    sl = round_price(quote.ask - distance, spec.digits, spec.tick_size)
    tp = round_price(quote.ask + distance, spec.digits, spec.tick_size)
    plan = plan_filling(
        mt5,
        OrderPlan(
            symbol=name,
            direction=Direction.LONG,
            order_type=OrderType.MARKET,
            volume=spec.volume_min,
            price=quote.ask,
            sl=sl,
            tp=tp,
            magic=TEST_MAGIC,
            comment=TEST_COMMENT,
            deviation=DEVIATION_POINTS,
            filling=0,
            digits=spec.digits,
        ),
    )
    emit(f"Symbol {name}: min lot {spec.volume_min:g}, filling {filling_name(plan.filling)}")
    opened = send_open(mt5, plan, retries=RETRIES, pause=pause)
    _step(emit, opened.ok, f"1. Open buy {spec.volume_min:g} lot with SL {sl} and TP {tp}")
    _attempts(emit, opened)
    if not opened.ok:
        emit(f"      {opened.text}")
        emit("Result: FAIL")
        return False
    ticket = opened.position
    slippage = (opened.price - opened.requested_price) / point
    emit(f"      position {ticket}, filled at {opened.price} (slippage {slippage:+.1f} points)")
    ok = _check_position(mt5, emit, ticket, sl, tp)
    new_sl = round_price(sl + distance / 2, spec.digits, spec.tick_size)
    modified = send_simple(
        mt5,
        modify_request(ticket, name, new_sl, tp, TEST_MAGIC),
        retries=RETRIES,
        pause=pause,
    )
    ok &= _step(emit, modified.ok, f"3. Move the SL to {new_sl}")
    _attempts(emit, modified)
    if modified.ok:
        ok &= _check_position(mt5, emit, ticket, new_sl, tp, label="   SL changed in MT5")
    closed = _close(mt5, ticket, name, plan.filling, pause)
    ok &= _step(emit, closed.ok, "4. Close the position")
    _attempts(emit, closed)
    if not closed.ok:
        emit(f"      {closed.text}. Close position {ticket} by hand in MT5.")
        emit("Result: FAIL")
        return False
    ok &= _read_deal(mt5, emit, ticket, pause, clock)
    emit("Result: PASS" if ok else "Result: FAIL")
    return ok


def _check_position(
    mt5: MT5Api,
    emit: Emit,
    ticket: int,
    sl: float,
    tp: float,
    *,
    label: str = "2. The position is in MT5 with the right magic number and stops",
) -> bool:
    found = [p for p in (read_positions(mt5, {TEST_MAGIC}) or []) if p.ticket == ticket]
    if not found:
        return _step(emit, False, f"{label}: position {ticket} not found")
    position = found[0]
    good = abs(position.sl - sl) < 1e-9 and abs(position.tp - tp) < 1e-9
    text = f"{label}: magic {position.magic}, SL {position.sl}, TP {position.tp}"
    return _step(emit, good and position.magic == TEST_MAGIC, text)


def _close(
    mt5: MT5Api,
    ticket: int,
    symbol: str,
    filling: int,
    pause: Callable[[float], None],
) -> OrderResult:
    found = [p for p in (read_positions(mt5, {TEST_MAGIC}) or []) if p.ticket == ticket]
    if not found:
        return OrderResult(False, None, "the position is gone (closed by SL/TP?)")
    position = found[0]

    def price(api_: MT5Api) -> float | None:
        quote = read_quote(api_, symbol)
        return exit_price(Direction.LONG, quote.bid, quote.ask) if quote is not None else None

    request = close_request(
        ticket,
        symbol,
        Direction.LONG,
        position.volume,
        0.0,
        deviation=DEVIATION_POINTS,
        magic=TEST_MAGIC,
        comment=TEST_COMMENT,
        filling=filling,
    )
    return send_simple(mt5, request, retries=RETRIES, refresh=price, pause=pause)


def _read_deal(
    mt5: MT5Api,
    emit: Emit,
    ticket: int,
    pause: Callable[[float], None],
    clock: Callable[[], float],
) -> bool:
    deadline = clock() + DEAL_WAIT_SECONDS
    while True:
        deals = read_deals(mt5, ticket) or []
        summary = summarize_position(deals)
        if summary is not None and summary.closed:
            text = (
                f"5. Closed deal read from the history: profit {summary.profit:+.2f}, "
                f"commission {summary.commission:+.2f}, swap {summary.swap:+.2f}, "
                f"net {summary.net_profit:+.2f}, exit {summary.exit_reason}"
            )
            return _step(emit, True, text)
        if clock() >= deadline:
            return _step(emit, False, f"5. No closed deal for position {ticket} in the history")
        pause(0.5)
