"""Fixes from the PC log of 5 October 2026, morning (0.22.2).

The London breakout's two stop orders: the side that is cancelled when the other one fills
is never adopted back, and an order that fills between the engine's two reads is not taken
for an expiry (that left the other side open). The one-position filter reads right as a
reason. A backtest logs its replay at DEBUG and the idle CPU budget waits while one runs.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from dataclasses import asdict

from app.backtest.engine import BacktestSetup, run_backtest
from app.brokers.base import BrokerOrder, BrokerPosition, OrderResult
from app.brokers.market import Quote
from app.brokers.requests import OrderPlan
from app.domain.history import Deal
from app.domain.modes import OperatingMode
from app.domain.signals import Direction, OrderType, SignalRecord, SignalState
from app.engine.execution import ExecutionEngine, Tracked
from app.engine.filters import FilterSettings, run_filters
from app.engine.perf_monitor import PerfMonitor
from app.mt5.models import SymbolSpec
from app.observability.busy import BusyTracker, recent
from app.observability.health import HealthStatus
from app.observability.metrics import CPU_WINDOW_SECONDS, PerfInputs, cpu_metric
from app.storage.trade_store import STATE_KEY, TradeRepository
from app.strategies.registry import MAGIC_NUMBERS, create_strategy
from tests.unit.backtest_helpers import DAY, noisy_history
from tests.unit.execution_helpers import NOW, Config, eurusd_record, rig
from tests.unit.risk_helpers import ACCOUNT, connected
from tests.unit.storage_helpers import temporary_store
from tests.unit.strategy_helpers import WEDNESDAY
from tests.unit.test_signal_filters import data, results

LONDON = MAGIC_NUMBERS["london_breakout"]
GROUP = "EURUSD:london_breakout:2"
ONE_POSITION = "one position per strategy and symbol"


def breakout() -> tuple[SignalRecord, SignalRecord]:
    group = {"oco_group": "EURUSD:london_breakout:1"}
    long = eurusd_record(
        id="long",
        order_type=OrderType.STOP,
        entry=1.08500,
        sl=1.08300,
        tp=1.08900,
        strategy="london_breakout",
        features=group,
    )
    short = eurusd_record(
        id="short",
        order_type=OrderType.STOP,
        direction=Direction.SHORT,
        entry=1.08200,
        sl=1.08400,
        tp=1.07800,
        strategy="london_breakout",
        features=group,
    )
    return long, short


def test_the_cancelled_other_side_of_a_breakout_is_never_adopted_again() -> None:
    fake = connected()
    long, short = breakout()
    signals = {long.id: long, short.id: short}
    with temporary_store() as store, rig(fake, store) as r:
        r.engine.execute(long, NOW)
        r.engine.execute(short, NOW)
        fake.set_bid("EURUSD.m", 1.08510)
        updates = r.engine.cycle(NOW + 60, signals)
        assert [(u.signal_id, u.state) for u in updates] == [
            ("long", SignalState.FILLED),
            ("long", SignalState.MANAGED),
            ("short", SignalState.FAILED),
        ]
        views = r.engine.snapshot.positions
        assert [(view.pending, view.direction) for view in views] == [
            (False, Direction.LONG.value),
        ]
        assert r.engine.cycle(NOW + 120, signals) == []
        texts = [message for _level, message in r.logs]
        assert not [text for text in texts if "Adopted" in text or "expired or" in text]


def stop(ticket: int, direction: Direction, price: float) -> BrokerOrder:
    sign = direction.sign
    return BrokerOrder(
        ticket,
        "EURUSD",
        direction,
        4 if direction is Direction.LONG else 5,
        0.1,
        price,
        round(price - 0.002 * sign, 5),
        round(price + 0.004 * sign, 5),
        LONDON,
        f"tw-{ticket}",
        0,
    )


class RacyBroker:
    """A breakout's two stop orders; the buy stop fills right after the first read."""

    mode = "paper"

    def __init__(self) -> None:
        self.waiting = {1: stop(1, Direction.LONG, 1.085), 2: stop(2, Direction.SHORT, 1.082)}
        self.filled: dict[int, BrokerPosition] = {}
        self.reads = 0
        self.cancelled: list[int] = []

    def _after_read(self) -> None:
        self.reads += 1
        order = self.waiting.pop(1) if self.reads == 1 else None
        if order is not None:
            self.filled[1] = BrokerPosition(
                1,
                order.symbol,
                order.direction,
                order.volume,
                order.price,
                order.sl,
                order.tp,
                0.0,
                0.0,
                order.magic,
                order.comment,
                0,
            )

    def positions(self, magics: Collection[int]) -> list[BrokerPosition] | None:
        found = list(self.filled.values())
        self._after_read()
        return found

    def orders(self, magics: Collection[int]) -> list[BrokerOrder] | None:
        found = list(self.waiting.values())
        self._after_read()
        return found

    def cancel(self, order: BrokerOrder) -> OrderResult:
        self.cancelled.append(order.ticket)
        self.waiting.pop(order.ticket, None)
        return OrderResult(True, 10009, "done")

    def open(self, plan: OrderPlan) -> OrderResult:
        return OrderResult(False, None, "not in this test")

    def modify(self, position: BrokerPosition, sl: float, tp: float) -> OrderResult:
        return OrderResult(False, None, "not in this test")

    def close(self, position: BrokerPosition, volume: float | None = None) -> OrderResult:
        return OrderResult(False, None, "not in this test")

    def deals(self, position: int) -> Sequence[Deal] | None:
        return []


class NoMarket:
    """Market reads without prices: the engine only syncs the orders (test only)."""

    def quote(self, symbol: str) -> Quote | None:
        return None

    def spec(self, symbol: str) -> SymbolSpec | None:
        return None

    def balance(self) -> float | None:
        return None

    def profit(
        self,
        symbol: str,
        direction: Direction,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None:
        return None


def waiting(order: BrokerOrder, signal_id: str) -> Tracked:
    return Tracked(
        mode="paper",
        ticket=order.ticket,
        pending=True,
        signal_id=signal_id,
        trade_id="",
        strategy="london_breakout",
        symbol=order.symbol,
        direction=order.direction.value,
        volume=order.volume,
        entry=order.price,
        sl=order.sl,
        tp=order.tp,
        initial_sl=order.sl,
        magic=order.magic,
        comment=order.comment,
        opened_at=NOW,
        oco_group=GROUP,
    )


def test_a_breakout_that_fills_between_two_reads_still_cancels_its_other_side() -> None:
    broker = RacyBroker()
    logs: list[str] = []
    config = Config()
    config.mode(OperatingMode.PAPER)
    with temporary_store() as store:
        trades = TradeRepository(store)
        saved = [
            asdict(waiting(broker.waiting[1], "long")),
            asdict(waiting(broker.waiting[2], "short")),
        ]
        trades.set_state(f"{STATE_KEY}:{ACCOUNT}", json.dumps(saved))
        engine = ExecutionEngine(
            config,
            market=NoMarket(),
            paper=broker,
            live=None,
            magics=MAGIC_NUMBERS,
            store=trades,
            account=lambda: ACCOUNT,
            log=lambda level, message: logs.append(message),
            utc_now=lambda: NOW,
        )
        updates = engine.cycle(NOW)
        assert [(u.signal_id, u.state) for u in updates] == [
            ("long", SignalState.FILLED),
            ("long", SignalState.MANAGED),
            ("short", SignalState.FAILED),
        ]
        assert broker.cancelled == [2] and "(OCO)" in updates[-1].reason
        assert engine.cycle(NOW + 60) == []
        assert [view.ticket for view in engine.snapshot.positions] == [1]
    assert not [text for text in logs if "Adopted" in text or "expired or" in text]


def test_the_one_position_filter_reads_right_as_the_reason() -> None:
    found = results(data(open_positions=1))
    assert found[ONE_POSITION] is False
    assert not [name for name in found if "no open position" in name]
    steps = run_filters(data(open_positions=2), FilterSettings())
    [step] = [step for step in steps if step.name == ONE_POSITION]
    assert step.detail == "2 already open" and step.value == 2


def test_a_backtest_logs_its_replay_at_debug_and_counts_as_busy() -> None:
    history = noisy_history(26, 1)
    lines: list[tuple[str, str]] = []
    during: list[str] = []
    setup = BacktestSetup(
        strategies=(create_strategy("trend_pullback"), create_strategy("london_breakout")),
        start=WEDNESDAY - 6 * DAY,
        end=WEDNESDAY,
    )
    result = run_backtest(
        history,
        setup,
        progress=lambda done, total: during.append(recent(0.0)),
        log=lambda level, message: lines.append((level, message)),
    )
    assert result.trades and lines
    assert {level for level, _message in lines} <= {"DEBUG", "WARNING", "ERROR"}
    assert any(message.startswith("Signal ") for _level, message in lines)
    assert not [message for _level, message in lines if "Adopted bot pending" in message]
    assert during and all("backtest" in found for found in during)
    assert "backtest" in recent(60.0)


def test_the_idle_cpu_budget_waits_while_a_backtest_runs() -> None:
    assert cpu_metric(PerfInputs(now=NOW, cpu_percent=8.0)).status is HealthStatus.WARNING
    loaded = cpu_metric(PerfInputs(now=NOW, cpu_percent=8.0, busy="backtest"))
    assert loaded.status is HealthStatus.OK and "A backtest ran in that time" in loaded.text
    clock = [1_000.0]
    tracker = BusyTracker(clock=lambda: clock[0])
    assert tracker.recent(CPU_WINDOW_SECONDS) == ""
    with tracker.busy("backtest"), tracker.busy("backtest"):
        assert tracker.recent(0.0) == "backtest"
    clock[0] += CPU_WINDOW_SECONDS - 1
    assert tracker.recent(CPU_WINDOW_SECONDS) == "backtest"
    clock[0] += 2
    assert tracker.recent(CPU_WINDOW_SECONDS) == ""
    logs: list[str] = []
    calm = PerfMonitor(
        lambda: PerfInputs(now=NOW, cpu_percent=8.0),
        log=lambda level, message: logs.append(f"{level} {message}"),
        clock=lambda: NOW,
        busy=lambda seconds: "backtest",
    )
    assert calm.run_once().over == [] and logs == []
    idle = PerfMonitor(
        lambda: PerfInputs(now=NOW, cpu_percent=8.0),
        log=lambda level, message: logs.append(f"{level} {message}"),
        clock=lambda: NOW,
        busy=lambda seconds: "",
    )
    assert [metric.name for metric in idle.run_once().over] == ["cpu_percent"]
    assert logs[0].startswith("WARNING Performance CPU: 8.0 % is over the budget")
