"""Trendline break exits, stacking and the symbol list (23 October 2026)."""

from __future__ import annotations

from typing import Any

from app.analysis.bars import Bars
from app.core.clock import BrokerClock
from app.domain.probability import baseline
from app.domain.signals import Direction, SignalRecord, SignalState, signal_id
from app.engine.execution import ExecutionSnapshot, PositionView
from app.engine.filters import FilterInput, FilterSettings, run_filters
from app.engine.signal_pipeline import judged_settings
from app.mt5.models import SymbolTradeMode
from app.strategies.base import Held
from app.strategies.context import MarketContext
from app.strategies.registry import create_strategy
from app.strategies.trendline_break import (
    SYMBOLS,
    TrendlineBreak,
    TrendlineBreakParams,
    symbol_allowed,
)
from tests.unit.signal_helpers import MORNING, make_signal, pipeline
from tests.unit.strategy_helpers import context
from tests.unit.trendline_scenes import PULLBACK, PULLBACK_END, m5_bars, mirror

CLOCK = BrokerClock.assumed()
ENTRY = PULLBACK_END  # the pullback entry closed on the last of these bars
# The pullback scene goes on: higher, a higher low (175), a high (200), then a fall.
RUN = [
    (0, 100), (60, 130), (90, 115), (130, 160), (150, 150), (175, 156), (195, 152),
    (220, 163), (260, 190), (290, 175), (330, 200), (380, 150),
]  # fmt: skip
# Two rising lows (120, 135) under a long, then a fall through the line between them.
LINE = [(0, 100), (30, 140), (50, 120), (80, 170), (100, 135), (110, 160), (150, 100)]


def ctx_for(bars: Bars) -> MarketContext:
    return context({"M5": bars}, timeframe="M5", digits=2)


def since_of(points: list[tuple[int, int]], count: int) -> int:
    return int(m5_bars(points, count).time[-1])


def renamed(bars: Bars, symbol: str) -> Bars:
    return Bars.build(
        symbol,
        "M5",
        time=bars.time,
        open=bars.open,
        high=bars.high,
        low=bars.low,
        close=bars.close,
    )


def test_a_long_closes_when_the_price_breaks_the_last_higher_low() -> None:
    held = [Held("a", Direction.LONG, since_of(RUN, ENTRY))]
    strategy = TrendlineBreak()
    assert strategy.exit_check(ctx_for(m5_bars(RUN, 350)), held) == {}
    found = strategy.exit_check(ctx_for(m5_bars(RUN, 360)), held)
    assert set(found) == {"a"} and "higher low" in found["a"]


def test_a_short_closes_when_the_price_breaks_the_last_lower_high() -> None:
    mirrored = mirror(RUN)
    held = [Held("b", Direction.SHORT, since_of(mirrored, ENTRY))]
    strategy = TrendlineBreak()
    assert strategy.exit_check(ctx_for(m5_bars(mirrored, 350)), held) == {}
    found = strategy.exit_check(ctx_for(m5_bars(mirrored, 360)), held)
    assert set(found) == {"b"} and "lower high" in found["b"]


def test_a_long_closes_when_the_price_breaks_the_rising_line_under_it() -> None:
    held = [Held("c", Direction.LONG, since_of(LINE, 36))]
    strategy = TrendlineBreak()
    assert strategy.exit_check(ctx_for(m5_bars(LINE, 123)), held) == {}
    found = strategy.exit_check(ctx_for(m5_bars(LINE, 124)), held)
    assert set(found) == {"c"} and "trendline" in found["c"]


def test_nothing_closes_when_the_exit_is_off_nothing_is_held_or_it_is_another_strategy() -> None:
    bars = m5_bars(RUN, 360)
    held = [Held("a", Direction.LONG, since_of(RUN, ENTRY))]
    off = TrendlineBreak(TrendlineBreakParams(exit_on_trend_change=False))
    assert off.exit_check(ctx_for(bars), held) == {}
    assert TrendlineBreak().exit_check(ctx_for(bars), []) == {}
    assert create_strategy("london_breakout").exit_check(ctx_for(bars), held) == {}


def test_symbols_are_matched_by_the_start_of_the_name() -> None:
    for name in ("US30", "US30.p", "nas100.p", "USTEC", "DJ30"):
        assert symbol_allowed(name, SYMBOLS)
    for name in ("EURUSD", "XAUUSD", "GER40"):
        assert not symbol_allowed(name, SYMBOLS)
    assert symbol_allowed("EURUSD", "")


def test_only_the_index_symbols_are_traded() -> None:
    bars = m5_bars(PULLBACK, PULLBACK_END)
    for name in ("US30.p", "NAS100.p"):
        assert TrendlineBreak().evaluate(ctx_for(renamed(bars, name))).signal is not None
    other = TrendlineBreak().evaluate(ctx_for(renamed(bars, "EURUSD")))
    assert other.signal is None and other.note == "symbol not traded"


def test_it_stacks_and_is_judged_by_r_while_the_others_are_not() -> None:
    assert TrendlineBreak.stackable and not TrendlineBreak.judged_by_win_rate
    london = create_strategy("london_breakout")
    assert not london.stackable and london.judged_by_win_rate


def gate_results(settings: FilterSettings) -> dict[str, bool | None]:
    values = FilterInput(
        signal=make_signal(),
        now=float(MORNING),
        clock=CLOCK,
        atr=0.0008,
        spread=0.00002,
        probability=baseline(400, 1000),
        expected_value=-0.3,
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
    names = ("win probability", "expected value (R)")
    return {step.name: step.passed for step in run_filters(values, settings) if step.name in names}


def test_a_strategy_judged_by_r_skips_the_win_rate_gates() -> None:
    settings = FilterSettings()
    assert gate_results(settings) == {"win probability": False, "expected value (R)": False}
    judged = judged_settings(TrendlineBreak(), settings)
    assert gate_results(judged) == {"win probability": True, "expected value (R)": True}
    assert judged_settings(create_strategy("london_breakout"), settings) == settings


class FakeExecutor:
    def __init__(self, views: tuple[PositionView, ...]) -> None:
        self.snapshot = ExecutionSnapshot(positions=views)
        self.closed: list[tuple[str, int]] = []

    def request_close(self, mode: str, ticket: int) -> None:
        self.closed.append((mode, ticket))


def open_record(since: int) -> SignalRecord:
    signal = make_signal(
        id=signal_id("trendline_break", "hash", "NAS100", "M5", since),
        symbol="NAS100",
        timeframe="M5",
        strategy="trendline_break",
        bar_time=since,
    )
    for state in (
        SignalState.PENDING_APPROVAL,
        SignalState.APPROVED,
        SignalState.SENT,
        SignalState.FILLED,
        SignalState.MANAGED,
    ):
        signal = signal.with_state(state, float(MORNING), "test")
    return SignalRecord(
        signal,
        make_signal_trace(signal),
        baseline(0, 0),
        None,
        0.0,
        1.0,
        "",
    )


def make_signal_trace(signal: Any) -> Any:
    from tests.unit.signal_helpers import make_record

    return make_record(signal).trace


def test_the_pipeline_closes_a_position_whose_trend_turned() -> None:
    record = open_record(since_of(RUN, ENTRY))
    view = PositionView(
        mode="paper",
        ticket=7,
        symbol="NAS100",
        direction="buy",
        volume=1.0,
        entry=150.0,
        sl=140.0,
        tp=190.0,
        profit=None,
        strategy="trendline_break",
        pending=False,
        signal_id=record.id,
    )
    fake = FakeExecutor((view,))
    executor: Any = fake
    logs: list[tuple[str, str]] = []
    signals = pipeline(("trendline_break",), logs=logs)
    signals._records[record.id] = record
    signals._executor = executor
    strategy = TrendlineBreak()
    signals._exits(strategy, ctx_for(m5_bars(RUN, 350)))
    assert fake.closed == []
    signals._exits(strategy, ctx_for(m5_bars(RUN, 360)))
    assert fake.closed == [("paper", 7)]
    assert any("higher low" in message for _, message in logs)
    assert "exit on trend change" in [step.name for step in record.trace.steps]
