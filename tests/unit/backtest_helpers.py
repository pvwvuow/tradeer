"""Synthetic histories for the backtest tests (test code only)."""

from __future__ import annotations

import numpy as np

from app.analysis.bars import Bars
from app.backtest.engine import History
from app.core.clock import BrokerClock, DstScheme
from app.mt5.models import SymbolSpec, SymbolTradeMode
from tests.unit.strategy_helpers import WEDNESDAY, aggregate, bars, wave

DAY = 86_400
# Server time = UTC keeps the synthetic days simple; the clock is measured, as after a tick.
UTC_CLOCK = BrokerClock(0.0, DstScheme.FIXED, measured=True)


def fx_spec(name: str = "EURUSD", *, stops_level: int = 0) -> SymbolSpec:
    return SymbolSpec(
        name=name,
        description="",
        digits=5,
        point=1e-5,
        tick_size=1e-5,
        tick_value=1.0,
        contract_size=100_000.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=stops_level,
        freeze_level=0,
        filling_mode=3,
        trade_mode=SymbolTradeMode.FULL,
        currency_margin=name[:3],
        currency_profit=name[3:6],
        visible=True,
    )


def frames_from_m5(m5: Bars) -> dict[str, Bars]:
    return {
        "M5": m5,
        "M15": aggregate(m5, "M15"),
        "H1": aggregate(m5, "H1"),
        "H4": aggregate(m5, "H4"),
        "D1": aggregate(m5, "D1"),
    }


def trend_history(days: int = 30, direction: int = 1, *, end: int = WEDNESDAY) -> History:
    """M5 bars of a steady trend with pullbacks every 8 hours, ending at `end` (UTC)."""
    count = days * DAY // 300
    closes = wave(count, drift=direction * 0.00004 / 3, amp=0.0012, period=96)
    m5 = bars(closes, "M5", start=end - count * 300, wick=0.0001)
    return History("EURUSD", "EURUSD", fx_spec(), UTC_CLOCK, frames_from_m5(m5))


def flat_history(prices: list[tuple[float, float, float, float]], start: int) -> History:
    """M5 bars from (open, high, low, close) rows, spread 10 points."""
    rows = np.asarray(prices, dtype=np.float64)
    m5 = Bars.build(
        "EURUSD",
        "M5",
        time=start + 300 * np.arange(len(rows)),
        open=rows[:, 0],
        high=rows[:, 1],
        low=rows[:, 2],
        close=rows[:, 3],
        spread=np.full(len(rows), 10),
    )
    return History("EURUSD", "EURUSD", fx_spec(), UTC_CLOCK, {"M5": m5})


def noisy_history(days: int = 30, seed: int = 1, *, end: int = WEDNESDAY) -> History:
    """A seeded random walk with slow trend swings: both example strategies trade on it."""
    count = days * DAY // 300
    rng = np.random.default_rng(seed)
    index = np.arange(count)
    trend = 0.00003 * np.sin(2 * np.pi * index / (288 * 9))
    closes = 1.1 + np.cumsum(trend + rng.normal(0.0, 0.00025, count))
    m5 = bars(closes, "M5", start=end - count * 300, wick=0.00012)
    return History("EURUSD", "EURUSD", fx_spec(), UTC_CLOCK, frames_from_m5(m5))
