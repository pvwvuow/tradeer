"""Precision fixes after the London breakout losses of 8 October 2026.

EURUSD and GBPUSD were sold a minute apart on the first touch below the Asia range (no close
beyond it) and both stopped out when the dollar turned: two trades on one idea.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from app.analysis.bars import Bars
from app.core.clock import BrokerClock
from app.domain.probability import baseline
from app.domain.signals import Direction, OrderType, SignalRecord, SignalState, signal_id
from app.engine.currency_guard import exposure, legs, shared_bet
from app.engine.filters import SAME_BET, FilterInput, FilterSettings, run_filters
from app.mt5.models import SymbolTradeMode
from app.strategies.base import Evaluation, SetupState
from app.strategies.london_breakout import LondonBreakout, LondonBreakoutParams, first_break
from tests.unit.signal_helpers import MORNING, make_record, make_signal, pipeline
from tests.unit.strategy_helpers import bars, context, frames, london_days

CLOCK = BrokerClock.assumed()


def test_currency_legs_and_shared_bets() -> None:
    assert legs("EURUSD") == ("EUR", "USD")
    assert legs("GBPUSD.a") == ("GBP", "USD")
    assert legs("XAUUSDm") == ("XAU", "USD")
    assert legs("US30") is None and legs("BTCUSD") is None
    assert exposure("EURUSD", Direction.SHORT) == {"EUR": -1, "USD": 1}
    assert shared_bet("GBPUSD", Direction.SHORT, "EURUSD", Direction.SHORT) == "long USD"
    assert shared_bet("USDJPY", Direction.LONG, "EURUSD", Direction.SHORT) == "long USD"
    assert shared_bet("GBPUSD", Direction.LONG, "EURUSD", Direction.SHORT) == ""
    assert shared_bet("EURUSD", Direction.SHORT, "EURUSD", Direction.SHORT) == ""
    assert shared_bet("US30", Direction.LONG, "EURUSD", Direction.LONG) == ""


def check(settings: FilterSettings | None = None, **changes: object) -> bool | None:
    values = FilterInput(
        signal=make_signal(),
        now=float(MORNING),
        clock=CLOCK,
        atr=0.0008,
        spread=0.00002,
        probability=baseline(0, 0),
        expected_value=None,
        duplicate=False,
        open_positions=0,
        losses_in_row=0,
        bars_since_loss=None,
        sessions=("London", "New York"),
        events=(),
        data_ok=True,
        data_text="fresh",
        trade_mode=SymbolTradeMode.FULL,
    )
    item = replace(values, **changes)  # type: ignore[arg-type]
    steps = run_filters(item, settings or FilterSettings())
    found = [step for step in steps if step.name == SAME_BET]
    assert len(found) == 1
    return found[0].passed


def test_the_filter_refuses_a_second_bet_on_one_currency() -> None:
    assert check() is True
    assert check(same_bet=1, same_bet_of="london_breakout EURUSD sell (long USD)") is False
    off = FilterSettings(one_bet_per_currency=False)
    assert check(off, same_bet=1) is True


def sold(symbol: str, strategy: str = "london_breakout") -> SignalRecord:
    signal = make_signal(
        id=signal_id(strategy, "hash", symbol, "M15", MORNING - 3600),
        symbol=symbol,
        strategy=strategy,
        direction=Direction.SHORT,
        sl=1.101,
        tp=1.098,
    )
    for state in (SignalState.PENDING_APPROVAL, SignalState.APPROVED, SignalState.SENT):
        signal = signal.with_state(state, float(MORNING), "test")
    return make_record(signal)


def test_a_eurusd_sell_blocks_a_gbpusd_sell_but_not_a_buy() -> None:
    signals = pipeline()
    euro = sold("EURUSD")
    signals._records[euro.id] = euro
    sell = make_signal(symbol="GBPUSD", direction=Direction.SHORT, sl=1.101, tp=1.098)
    assert signals._same_bet(sell) == ["london_breakout EURUSD sell (long USD)"]
    assert signals._same_bet(make_signal(symbol="GBPUSD")) == []
    assert signals._same_bet(make_signal(symbol="USDJPY")) == [
        "london_breakout EURUSD sell (long USD)",
    ]


def test_first_break_needs_closes_in_a_row() -> None:
    assert first_break([1.0, 1.2, 1.3], 1.1, 0.9, 1) == (1, 1)
    assert first_break([1.0, 1.2, 1.0, 1.2, 1.3], 1.1, 0.9, 2) == (4, 1)
    assert first_break([1.0, 0.8, 1.2], 1.1, 0.9, 1) == (1, -1)
    assert first_break([1.0, 1.05], 1.1, 0.9, 1) == (-1, 0)


def morning(extra: list[float]) -> Bars:
    """london_days up to 07:00 UTC (08:00 London) plus `extra` M15 closes after the open."""
    base = london_days()
    closes = np.r_[base.close, np.asarray(extra, dtype=np.float64)]
    return bars(closes, start=int(base.time[0]), wick=0.0002)


def evaluate(extra: list[float], confirm_bars: int = 1) -> Evaluation:
    params = LondonBreakoutParams(entry_mode="close", confirm_bars=confirm_bars)
    return LondonBreakout(params).evaluate(context(frames(morning(extra))))


def test_close_entry_waits_for_a_close_beyond_the_range() -> None:
    inside = evaluate([1.1000])
    assert inside.signals == () and inside.state is SetupState.FORMING
    broke = evaluate([1.1000, 1.0970])
    assert len(broke.signals) == 1
    signal = broke.signals[0]
    assert signal.direction is Direction.SHORT and signal.order_type is OrderType.MARKET
    assert signal.entry == 1.097 and signal.sl > signal.entry > signal.tp
    later = evaluate([1.1000, 1.0970, 1.0965])
    assert later.signals == ()  # only the day's first confirmed break is traded


def test_close_entry_can_ask_for_two_closes_and_skips_a_chase() -> None:
    assert evaluate([1.1000, 1.0970], confirm_bars=2).signals == ()
    assert len(evaluate([1.1000, 1.0970, 1.0968], confirm_bars=2).signals) == 1
    far = evaluate([1.1000, 1.0930])
    assert far.signals == () and far.note == "the break ran too far"


def test_stop_orders_stay_the_default() -> None:
    assert LondonBreakoutParams().entry_mode == "stop"
    assert LondonBreakout.version == "1.1.0"
