"""PC demo test of 6 October 2026 (0.23.3, FIBO, GBPUSD): the pending orders were placed,
the breakout pair passed and the expiring order ended by itself. But MT5 keeps an expiry in
whole minutes: 16:19:59 came back as 16:19:00, so the pending check failed, and in the next
run the 90 s order, cut to a minute 55 s away, was refused with 10022 INVALID_EXPIRATION.
Every expiry is now sent as a whole minute (moved up), the expiring order at least 2 minutes
away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.brokers.demo_test import BREAKOUT_COMMENT, EXPIRY_SECONDS, DemoTest, Outcome
from app.brokers.live_broker import send_open
from app.brokers.requests import OrderPlan, open_request, whole_minute
from app.domain.signals import Direction, OrderType
from app.mt5 import api
from tests.fakes.fake_mt5 import FakeMT5

SYMBOL = "XAUUSD.m"
MINUTE = 60
INVALID_EXPIRATION = 10022
START = 1_791_199_985.0  # five seconds into a minute (server time too), as in the second run


@dataclass
class MinuteExpiryMT5(FakeMT5):
    """FakeMT5 whose server keeps an expiry in whole minutes, dropping the seconds, and
    refuses one that this leaves under a minute away (10022), as FIBO's did."""

    refused: list[int] = field(default_factory=list)

    def server_time(self) -> int:
        return int(self.now() + self.server_offset_hours * 3600)

    def order_send(self, request: dict[str, Any]) -> Any:
        if request.get("type_time") == api.ORDER_TIME_SPECIFIED and not self.send_script:
            kept = int(request["expiration"]) // MINUTE * MINUTE
            if kept < self.server_time() + MINUTE:
                self.refused.append(kept)
                self.send_script.append(INVALID_EXPIRATION)
            else:
                request = {**request, "expiration": kept}
        return super().order_send(request)


class Gateway:
    """The MT5 gateway thread, inline: every request runs at once on the fake."""

    def __init__(self, mt5: FakeMT5) -> None:
        self.mt5 = mt5

    def run(self, name: str, work: Any, *, timeout: Any = None, arguments: Any = None) -> Any:
        return work(self.mt5)


class Market:
    """One clock for the fake and the test; while the test waits, the price rises into the
    breakout pair's buy stop, and the fake's server removes expired orders."""

    def __init__(self, mt5: FakeMT5) -> None:
        self.mt5 = mt5
        self.now = START
        mt5.now = self.clock

    def clock(self) -> float:
        return self.now

    def pause(self, seconds: float) -> None:
        self.now += seconds
        bid = next(symbol.bid for symbol in self.mt5.symbols if symbol.name == SYMBOL)
        for order in self.mt5.pending_orders:
            if order.comment == BREAKOUT_COMMENT and order.type == api.ORDER_TYPE_BUY_STOP:
                bid = order.price_open
        self.mt5.set_bid(SYMBOL, bid)


def connected(fake: FakeMT5) -> FakeMT5:
    account = fake.accounts[0]
    assert fake.initialize()
    assert fake.login(account.login, account.password, account.server)
    return fake


def plan(expiration: int) -> OrderPlan:
    return OrderPlan(
        symbol=SYMBOL,
        direction=Direction.LONG,
        order_type=OrderType.LIMIT,
        volume=0.01,
        price=2370.0,
        sl=2350.0,
        tp=2400.0,
        magic=26_070_098,
        comment="tw-demo-test",
        deviation=20,
        filling=api.ORDER_FILLING_IOC,
        digits=2,
        expiration=expiration,
    )


def test_an_expiry_moves_up_to_a_whole_minute() -> None:
    assert whole_minute(1_791_307_199) == 1_791_307_200
    assert whole_minute(1_791_307_141) == 1_791_307_200
    assert whole_minute(1_791_307_200) == 1_791_307_200


def test_the_request_carries_the_whole_minute() -> None:
    request = open_request(plan(1_791_307_265))
    assert request["type_time"] == api.ORDER_TIME_SPECIFIED
    assert request["expiration"] == 1_791_307_320


def test_the_90_second_expiry_of_the_second_run_is_kept_as_sent() -> None:
    fake = MinuteExpiryMT5()
    Market(fake)
    connected(fake)
    server = fake.server_time()
    assert server % MINUTE == 5
    assert (server + 90) // MINUTE * MINUTE - server == 55  # what FIBO kept, and refused
    result = send_open(fake, plan(server + 90), retries=1)
    assert result.ok and result.placed, result.text
    assert fake.refused == []
    assert [order.time_expiration for order in fake.pending_orders] == [server + 115]


def test_every_step_passes_on_a_server_that_keeps_whole_minutes() -> None:
    fake = MinuteExpiryMT5()
    market = Market(fake)
    connected(fake)
    test = DemoTest(
        Gateway(fake),  # type: ignore[arg-type]
        pause=market.pause,
        clock=market.clock,
        wall=market.clock,
    )
    report = test.run(["XAUUSD"])
    assert [step.line() for step in report.steps if step.outcome is not Outcome.PASS] == []
    assert report.ok and fake.refused == []
    assert fake.pending_orders == [] and fake.positions == []
    expiring = next(step for step in report.steps if step.name == "Pending order with an expiry")
    life = int(expiring.detail.split(" server time (")[1].split(" s)")[0])
    assert EXPIRY_SECONDS <= life < EXPIRY_SECONDS + MINUTE
