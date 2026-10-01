import pytest
from pydantic import ValidationError

from app.domain.config import TradingDefaults
from app.domain.modes import DEFAULT_MODE, OperatingMode


def test_defaults_are_paper_first_and_conservative() -> None:
    defaults = TradingDefaults()
    assert defaults.mode is OperatingMode.PAPER
    assert DEFAULT_MODE is OperatingMode.PAPER
    assert defaults.risk_per_trade_percent == 0.5
    assert defaults.max_total_open_risk_percent == 1.5
    assert defaults.max_daily_loss_percent == 2.0
    assert defaults.max_total_drawdown_percent == 8.0
    assert defaults.max_open_trades == 3
    assert defaults.max_trades_per_day == 6
    assert defaults.min_win_probability_percent == 60.0
    assert defaults.min_expected_value_r == 0.10
    assert defaults.margin_level_floor_percent == 300.0
    assert defaults.symbols == ["EURUSD", "GBPUSD", "XAUUSD"]
    assert defaults.entry_timeframe == "M15"
    assert defaults.context_timeframes == ["H1", "H4", "D1"]


def test_risk_above_one_percent_per_trade_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TradingDefaults(risk_per_trade_percent=2)


def test_unknown_settings_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TradingDefaults.model_validate({"martingale": True})


def test_only_semi_auto_and_auto_place_real_orders() -> None:
    assert not OperatingMode.ANALYSIS_ONLY.places_real_orders
    assert not OperatingMode.PAPER.places_real_orders
    assert OperatingMode.SEMI_AUTO.places_real_orders
    assert OperatingMode.AUTO.places_real_orders


def test_safety_summary_is_plain_language() -> None:
    summary = TradingDefaults().safety_summary()
    assert summary.startswith("Paper mode")
    assert "0.5% risk per trade" in summary
