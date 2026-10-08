"""One trade per symbol and side across strategies (8 October 2026: a channel breakout sold
EURUSD on top of the London breakout's open sell, two trades on one idea)."""

from __future__ import annotations

from dataclasses import replace

from app.core.clock import BrokerClock
from app.domain.probability import baseline
from app.domain.signals import Direction, SignalRecord, SignalState, signal_id
from app.engine.filters import FilterInput, FilterSettings, run_filters
from app.mt5.models import SymbolTradeMode
from tests.unit.signal_helpers import MORNING, make_record, make_signal, pipeline
from tests.unit.strategy_helpers import analysis_at, london_days

CLOCK = BrokerClock.assumed()
NAME = "one trade per symbol and side (all strategies)"


def filled(strategy: str, direction: Direction = Direction.LONG) -> SignalRecord:
    """An open position of `strategy` on EURUSD."""
    sell = direction is Direction.SHORT
    signal = make_signal(
        id=signal_id(strategy, "hash", "EURUSD", "M15", MORNING - 3600),
        strategy=strategy,
        direction=direction,
        sl=1.101 if sell else 1.099,
        tp=1.098 if sell else 1.102,
    )
    at = float(MORNING)
    for state in (
        SignalState.PENDING_APPROVAL,
        SignalState.APPROVED,
        SignalState.SENT,
        SignalState.FILLED,
    ):
        signal = signal.with_state(state, at, "test")
    return make_record(signal)


def check(**changes: object) -> bool | None:
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
    steps = run_filters(replace(values, **changes), FilterSettings())  # type: ignore[arg-type]
    found = [step for step in steps if step.name == NAME]
    assert len(found) == 1
    return found[0].passed


def test_the_filter_refuses_a_second_trade_on_the_same_side() -> None:
    assert check() is True
    assert check(same_side=1, same_side_of="london_breakout") is False


def test_another_strategys_open_trade_blocks_the_same_side_only() -> None:
    signals = pipeline()  # london_breakout: a buy stop and a sell stop at the London open
    other = filled("channel_breakout", Direction.LONG)
    signals._records[other.id] = other
    m15 = london_days()
    analysis, now = analysis_at(m15, len(m15))
    created = signals.on_analysis(analysis, clock=CLOCK, spec=None, spread=2e-5, now=now)
    by_side = {record.signal.direction: record for record in created}
    assert set(by_side) == {Direction.LONG, Direction.SHORT}
    buy, sell = by_side[Direction.LONG], by_side[Direction.SHORT]
    assert buy.signal.state is SignalState.FILTERED_OUT
    assert NAME in buy.reject_reason
    step = next(step for step in buy.trace.steps if step.name == NAME)
    assert step.detail == "already a buy by channel_breakout"
    assert sell.signal.state is SignalState.PENDING_APPROVAL


def test_the_same_strategy_and_closed_trades_do_not_count() -> None:
    signals = pipeline()
    mine = filled("london_breakout", Direction.SHORT)
    closed = filled("trend_pullback", Direction.SHORT)
    closed_signal = closed.signal
    for state in (SignalState.MANAGED, SignalState.CLOSED):
        closed_signal = closed_signal.with_state(state, float(MORNING), "test")
    signals._records[mine.id] = mine
    signals._records[closed.id] = closed.with_signal(closed_signal)
    probe = make_signal(strategy="london_breakout", direction=Direction.SHORT)
    assert signals._same_side(probe) == []
    other = make_signal(strategy="ema_momentum", direction=Direction.SHORT)
    assert [item.strategy for item in signals._same_side(other)] == ["london_breakout"]
