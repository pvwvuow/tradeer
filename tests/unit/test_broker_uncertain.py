"""A lost reply never sends twice (docs/AI_AUTOPILOT.md B20, B21, 0.50.2): after an
uncertain answer the broker looks again before any retry, for an order, a partial close, a
stop change and a cancel."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from app.brokers.base import BrokerPosition
from app.brokers.live_broker import (
    cancel_arrived,
    close_position,
    levels_arrived,
    send_open,
    send_simple,
    volume_of,
)
from app.brokers.requests import OrderPlan, cancel_request, modify_request
from app.domain.signals import Direction, OrderType
from app.mt5 import api
from tests.fakes.fake_mt5 import FakeMT5

PAUSES: list[float] = []


def pause(seconds: float) -> None:
    PAUSES.append(seconds)


@dataclass
class SlowFake(FakeMT5):
    """A trade server that shows a new position only after a few looks."""

    hidden_looks: int = 0

    def positions_get(self, *args: Any, **kwargs: Any) -> tuple[SimpleNamespace, ...] | None:
        if self.hidden_looks > 0 and "symbol" in kwargs:
            self.hidden_looks -= 1
            return ()
        return super().positions_get(*args, **kwargs)


def ready(fake: FakeMT5) -> FakeMT5:
    fake.initialize()
    return fake


def plan(fake: FakeMT5) -> OrderPlan:
    tick = fake.symbol_info_tick("EURUSD.m")
    assert tick is not None
    return OrderPlan(
        symbol="EURUSD.m",
        direction=Direction.LONG,
        order_type=OrderType.MARKET,
        volume=0.10,
        price=tick.ask,
        sl=round(tick.ask - 0.0050, 5),
        tp=round(tick.ask + 0.0100, 5),
        magic=26_071_001,
        comment="ch test 1",
        deviation=10,
        filling=api.ORDER_FILLING_FOK,
    )


def position(fake: FakeMT5, volume: float = 0.10) -> BrokerPosition:
    ticket = 70_500_001
    fake.positions.append(
        SimpleNamespace(
            ticket=ticket,
            symbol="EURUSD.m",
            type=api.POSITION_TYPE_BUY,
            volume=volume,
            price_open=1.08,
            price_current=1.08,
            sl=1.07,
            tp=1.10,
            profit=0.0,
            swap=0.0,
            magic=26_071_001,
            identifier=ticket,
            comment="ch test 1",
            time=0,
        ),
    )
    return BrokerPosition(
        ticket,
        "EURUSD.m",
        Direction.LONG,
        volume,
        1.08,
        1.07,
        1.10,
        0.0,
        0.0,
        26_071_001,
        "ch test 1",
        0,
    )


def sends(fake: FakeMT5) -> int:
    return sum(1 for name in fake.calls if name == "order_send")


def test_an_order_the_server_shows_late_is_not_sent_again() -> None:
    fake = ready(SlowFake(hidden_looks=2))
    fake.lost_replies = 1
    PAUSES.clear()
    found = send_open(fake, plan(fake), retries=3, pause=pause)
    assert found.ok and "had arrived" in found.text
    assert sends(fake) == 1
    assert len(fake.positions) == 1
    assert len(PAUSES) == 2


def test_a_lost_reply_to_a_partial_close_does_not_close_the_rest() -> None:
    fake = ready(FakeMT5())
    held = position(fake)
    fake.lost_replies = 1
    found = close_position(fake, held, 0.05, retries=3, deviation=10, pause=pause)
    assert found.ok and "had arrived" in found.text
    assert sends(fake) == 1
    assert volume_of(fake, held.ticket) == 0.05


def test_a_close_that_did_not_arrive_is_retried() -> None:
    fake = ready(FakeMT5())
    held = position(fake)
    fake.send_script = [10012]  # the reply is lost and nothing happened
    found = close_position(fake, held, 0.05, retries=3, deviation=10, pause=pause)
    assert found.ok
    assert sends(fake) == 2
    assert volume_of(fake, held.ticket) == 0.05


def test_a_stop_change_and_a_cancel_are_checked_before_a_retry() -> None:
    fake = ready(FakeMT5())
    held = position(fake)
    assert not levels_arrived(held.ticket, 1.075, 1.10)(fake)
    fake.positions[0].sl = 1.075
    assert levels_arrived(held.ticket, 1.075, 1.10)(fake)
    request = modify_request(held.ticket, held.symbol, 1.075, 1.10, held.magic)
    fake.send_script = [10012]
    found = send_simple(
        fake,
        request,
        retries=3,
        arrived=levels_arrived(held.ticket, 1.075, 1.10),
        pause=pause,
    )
    assert found.ok and sends(fake) == 1
    assert cancel_arrived(424242)(fake)  # no such order and no such position
    assert not cancel_arrived(held.ticket)(fake)  # it is a position: it filled
    fake.send_script = [10012]
    gone = send_simple(fake, cancel_request(424242), retries=3, arrived=cancel_arrived(424242))
    assert gone.ok
