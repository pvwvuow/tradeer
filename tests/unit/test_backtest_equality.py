"""Backtest = live (spec C8, G1, G3 row 10): on identical data the backtest replay and the live
analysis loop (MarketWatch on MT5 bars, polled every cycle) produce identical signals, with
identical strategy checks, features and prices."""

from __future__ import annotations

import numpy as np

from app.backtest.engine import BacktestSetup, Replay
from app.backtest.history import load_history
from app.core.clock import BrokerClock
from app.domain.signals import SignalRecord
from app.engine.filters import FilterSettings
from app.engine.market_watch import MarketWatch
from app.engine.signal_pipeline import SignalPipeline
from app.mt5.gateway import MT5Gateway
from app.mt5.market_data import MarketData
from app.strategies.registry import create_strategy
from tests.fakes.fake_mt5 import FakeMT5, FakeSymbol
from tests.unit.storage_helpers import temporary_store
from tests.unit.strategy_helpers import WEDNESDAY

DAY = 86_400
START = WEDNESDAY - DAY  # Tuesday 29 September 2026, 00:00 UTC
END = WEDNESDAY + DAY


def random_walk_path(seed: int = 11):  # type: ignore[no-untyped-def]
    """A seeded random walk per minute with slow trend swings (both strategies trade on it),
    long enough for the D1 warm-up of the backtest history."""
    first = START - 520 * DAY
    minutes = int((END + DAY - first) // 60) + 1
    index = np.arange(minutes)
    rng = np.random.default_rng(seed)
    drift = 0.00003 / 5 * np.sin(2 * np.pi * index / (1440 * 9))
    walk = np.cumsum(drift + rng.normal(0.0, 0.00025 / np.sqrt(5), minutes))
    grid = first + 60.0 * index
    return lambda moments: np.interp(moments, grid, walk)


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def strategies() -> list:  # type: ignore[type-arg]
    return [create_strategy("trend_pullback"), create_strategy("london_breakout")]


def fingerprint(record: SignalRecord) -> tuple[object, ...]:
    signal = record.signal
    checks = tuple(
        (step.name, step.passed, step.value, step.threshold)
        for step in record.trace.steps
        if step.stage in ("strategy", "features")
    )
    return (
        signal.id,
        signal.direction,
        signal.order_type,
        signal.entry,
        signal.sl,
        signal.tp,
        signal.bar_time,
        signal.created_at,
        signal.expires_at,
        tuple(sorted(signal.features.items())),
        checks,
    )


def live_signals(
    fake: FakeMT5,
    gateway: MT5Gateway,
    clock: Clock,
) -> tuple[list[SignalRecord], BrokerClock]:
    found: list[SignalRecord] = []
    pipeline = SignalPipeline(lambda: strategies_live, FilterSettings, utc_now=clock)
    strategies_live = strategies()
    data = MarketData(gateway, BrokerClock.assumed(), utc_now=clock)
    watch = MarketWatch(
        data,
        symbols=lambda: ["EURUSD"],
        connected=lambda: True,
        signals=pipeline,
        utc_now=clock,
    )
    original = pipeline.on_analysis

    def spy(*args: object, **kwargs: object) -> list[SignalRecord]:
        created = original(*args, **kwargs)  # type: ignore[arg-type]
        found.extend(created)
        return created

    pipeline.on_analysis = spy  # type: ignore[method-assign]
    moment = START + 300
    while moment <= END:
        clock.now = moment
        watch.cycle()
        moment += 300
    return found, watch.clock


def test_backtest_and_live_give_identical_signals_on_identical_data() -> None:
    clock = Clock(START)
    symbol = FakeSymbol("EURUSD.m", 1.08345, path=random_walk_path())
    fake = FakeMT5(now=clock, ticks_follow_bars=True, symbols=[symbol])
    fake.initialize()
    gateway = MT5Gateway(lambda: fake, idle_seconds=0.02)
    gateway.start()
    try:
        live, broker_clock = live_signals(fake, gateway, clock)
        clock.now = END
        history, _ = load_history(gateway, "EURUSD", "EURUSD.m", START, END, broker_clock)
    finally:
        gateway.stop()
    assert live, "the synthetic market gave no live signal to compare"
    setup = BacktestSetup(strategies=tuple(strategies()), start=START, end=END)
    with temporary_store() as store:
        replay = Replay(history, setup, store, lambda level, message: None)
        step = history.bars["M5"]
        first = int((step.time < START).sum())
        stop = int((step.time < END).sum())
        for index in range(first, stop):
            replay.bar(step, index)
        replayed = sorted(replay.signals(), key=lambda r: (r.signal.created_at, r.id))
    live_sorted = sorted(live, key=lambda r: (r.signal.created_at, r.id))
    assert [fingerprint(r) for r in replayed] == [fingerprint(r) for r in live_sorted]
