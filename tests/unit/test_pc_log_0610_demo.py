"""PC demo test of 6 October 2026 (0.23.2, FIBO, GBPUSD): the market orders, the SL move,
the partial and full close, the history, the refused order and the kill switch all passed.
Every pending order came back as 10009 DONE instead of 10008 PLACED, so the live broker took
it for a filled position and the three pending steps failed, although the orders were in MT5
(the kill switch cancelled 7). A pending order MT5 takes is now placed, whatever its code.
"""

from __future__ import annotations

import time
from typing import Any

from app.brokers.live_broker import send_open
from app.brokers.requests import OrderPlan
from app.domain.signals import Direction, OrderType
from app.mt5 import api
from tests.fakes.fake_mt5 import FakeMT5


class DoneForPendingMT5(FakeMT5):
    """FakeMT5 that answers a placed pending order with 10009 DONE, as FIBO does."""

    def order_send(self, request: dict[str, Any]) -> Any:
        result = super().order_send(request)
        if result is not None and result.retcode == api.TRADE_RETCODE_PLACED:
            result.retcode = api.TRADE_RETCODE_DONE
        return result


def connected(fake: FakeMT5) -> FakeMT5:
    account = fake.accounts[0]
    assert fake.initialize()
    assert fake.login(account.login, account.password, account.server)
    return fake


def plan(order_type: OrderType, price: float, sl: float, tp: float) -> OrderPlan:
    pending = order_type is not OrderType.MARKET
    return OrderPlan(
        symbol="XAUUSD.m",
        direction=Direction.LONG,
        order_type=order_type,
        volume=0.01,
        price=price,
        sl=sl,
        tp=tp,
        magic=26_070_098,
        comment="tw-demo-test",
        deviation=20,
        filling=api.ORDER_FILLING_IOC,
        digits=2,
        expiration=int(time.time()) + 3600 if pending else None,
    )


def no_pause(seconds: float) -> None:
    return None


def test_a_pending_order_answered_done_is_placed_not_a_position() -> None:
    fake = connected(DoneForPendingMT5())
    result = send_open(fake, plan(OrderType.STOP, 2400.0, 2380.0, 2430.0), retries=1)
    assert result.ok and result.placed, result.text
    assert result.retcode == api.TRADE_RETCODE_DONE
    assert result.position == 0
    assert [order.ticket for order in fake.pending_orders] == [result.order]
    assert fake.positions == []


def test_a_limit_order_answered_done_is_placed_too() -> None:
    fake = connected(DoneForPendingMT5())
    result = send_open(fake, plan(OrderType.LIMIT, 2370.0, 2350.0, 2400.0), retries=1)
    assert result.ok and result.placed and result.position == 0, result.text
    assert len(fake.pending_orders) == 1


def test_a_market_order_answered_done_is_still_a_position() -> None:
    fake = connected(DoneForPendingMT5())
    ask = 2385.42 + 0.25
    result = send_open(fake, plan(OrderType.MARKET, ask, 2360.0, 2410.0), retries=1, pause=no_pause)
    assert result.ok and not result.placed, result.text
    assert result.position and [p.ticket for p in fake.positions] == [result.position]
