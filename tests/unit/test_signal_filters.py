"""Signal filters (spec C5): value, threshold and pass or fail for every check."""

from dataclasses import replace

from app.core.clock import BrokerClock
from app.domain.probability import baseline
from app.domain.signals import Direction
from app.engine.filters import FilterInput, FilterSettings, in_rollover, run_filters
from app.mt5.models import SymbolTradeMode
from tests.unit.signal_helpers import MORNING, make_signal


def data(**changes: object) -> FilterInput:
    values = FilterInput(
        signal=make_signal(),
        now=float(MORNING),
        clock=BrokerClock.assumed(),
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
    return replace(values, **changes)  # type: ignore[arg-type]


def results(item: FilterInput, settings: FilterSettings | None = None) -> dict[str, object]:
    return {step.name: step.passed for step in run_filters(item, settings or FilterSettings())}


def test_a_clean_signal_passes_and_unknown_probability_is_not_applied() -> None:
    found = results(data())
    assert found["win probability"] is None and found["expected value (R)"] is None
    assert [name for name, passed in found.items() if passed is False] == []
    strict = results(data(), FilterSettings(require_probability=True))
    assert strict["win probability"] is False


def test_probability_and_ev_thresholds() -> None:
    low = data(probability=baseline(15, 40), expected_value=0.05)
    found = results(low)
    assert found["win probability"] is False and found["expected value (R)"] is False
    high = results(data(probability=baseline(30, 40), expected_value=1.2))
    assert high["win probability"] is True and high["expected value (R)"] is True


def test_spread_duplicates_losses_and_trade_mode() -> None:
    assert results(data(spread=0.0005))["spread vs ATR"] is False
    assert results(data(spread=float("nan")))["spread vs ATR"] is False
    twin = results(data(duplicate=True))
    assert twin["one pending signal per symbol, strategy and side"] is False
    position = results(data(open_positions=1))
    assert position["no open position of this strategy on the symbol"] is False
    assert results(data(bars_since_loss=1))["cooldown after a loss"] is False
    assert results(data(losses_in_row=3))["pause after consecutive losses"] is False
    paused = results(data(losses_in_row=3, hours_since_losses=23.5))
    assert paused["pause after consecutive losses"] is False
    over = results(data(losses_in_row=3, hours_since_losses=24.0))
    assert over["pause after consecutive losses"] is True  # the pause ends after 24 hours
    closed = results(data(trade_mode=SymbolTradeMode.CLOSE_ONLY))
    assert closed["symbol open for new trades"] is False
    long_only = results(data(trade_mode=SymbolTradeMode.LONG_ONLY))
    assert long_only["symbol open for new trades"] is True
    short = make_signal(direction=Direction.SHORT, sl=1.101, tp=1.098)
    wrong_side = results(data(signal=short, trade_mode=SymbolTradeMode.LONG_ONLY))
    assert wrong_side["symbol open for new trades"] is False


def test_time_filters() -> None:
    asia = make_signal(created_at=float(MORNING - 8 * 3600))  # 02:00 UTC
    assert results(data(signal=asia))["trading session"] is False
    off = FilterSettings(check_sessions=False)
    assert results(data(signal=asia), off)["trading session"] is True
    friday = MORNING + 2 * 86_400 + 9 * 3600  # Friday 19:00 UTC = 15:00 New York
    late = make_signal(created_at=float(friday))
    assert results(data(signal=late, now=float(friday)))["Friday close"] is False
    assert in_rollover(23 * 60 + 30, "23:00", "01:00") and in_rollover(30, "23:00", "01:00")
    assert not in_rollover(12 * 60, "23:00", "01:00")
    assert results(data(data_ok=False))["fresh data"] is False
    saturday = float(MORNING + 3 * 86_400)
    assert results(data(now=saturday))["market open"] is False
