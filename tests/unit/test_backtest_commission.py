"""A backtest with a commission of 0 uses the one live trades pay (PC log, 16 October 2026)."""

from __future__ import annotations

from datetime import date

from app.backtest.costs import BacktestCosts
from app.backtest.service import BacktestRequest, CommissionSource, filled_commission


class History:
    def __init__(self, value: float | None) -> None:
        self.value = value
        self.asked: list[tuple[str | None, str]] = []

    def measured_commission(self, account: str | None, symbol: str) -> float | None:
        self.asked.append((account, symbol))
        return self.value


class Broken:
    def measured_commission(self, account: str | None, symbol: str) -> float | None:
        raise RuntimeError(f"database locked ({account}, {symbol})")


def request(commission: float = 0.0) -> BacktestRequest:
    return BacktestRequest(
        symbol="EURUSD",
        start=date(2026, 4, 16),
        end=date(2026, 10, 15),
        costs=BacktestCosts(commission_per_lot=commission),
    )


def test_a_zero_commission_is_filled_from_the_history() -> None:
    history = History(6.7312)
    filled, note = filled_commission(request(), history, "3120419")
    assert filled.costs.commission_per_lot == 6.73
    assert history.asked == [("3120419", "EURUSD")]
    assert "6.73" in note
    assert "EURUSD" in note


def test_a_commission_on_the_form_is_kept() -> None:
    history = History(6.73)
    filled, note = filled_commission(request(4.0), history, "3120419")
    assert filled.costs.commission_per_lot == 4.0
    assert note == ""
    assert history.asked == []


def test_no_history_leaves_the_request_alone() -> None:
    sources: list[CommissionSource | None] = [
        History(None),
        History(0.0),
        History(float("nan")),
        Broken(),
        None,
    ]
    for source in sources:
        filled, note = filled_commission(request(), source, "3120419")
        assert filled.costs.commission_per_lot == 0.0
        assert note == ""


def test_the_run_text_names_the_strategies_and_costs() -> None:
    text = request(6.73).describe()
    assert "trend_pullback" in text
    assert "commission 6.73 a lot" in text
    assert "reversed" not in text
