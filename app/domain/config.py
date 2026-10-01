"""Validated user defaults (spec B4): conservative values, Paper mode first."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.modes import DEFAULT_MODE, OperatingMode


def _default_symbols() -> list[str]:
    return ["EURUSD", "GBPUSD", "XAUUSD"]


def _default_context_timeframes() -> list[str]:
    return ["H1", "H4", "D1"]


class TradingDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    symbols: list[str] = Field(default_factory=_default_symbols, min_length=1)
    entry_timeframe: str = "M15"
    context_timeframes: list[str] = Field(default_factory=_default_context_timeframes)
    risk_per_trade_percent: float = Field(default=0.5, gt=0, le=1)
    max_total_open_risk_percent: float = Field(default=1.5, gt=0, le=5)
    max_daily_loss_percent: float = Field(default=2.0, gt=0, le=10)
    max_total_drawdown_percent: float = Field(default=8.0, gt=0, le=50)
    max_open_trades: int = Field(default=3, ge=1, le=20)
    max_trades_per_day: int = Field(default=6, ge=1, le=50)
    min_win_probability_percent: float = Field(default=60.0, ge=50, le=100)
    min_expected_value_r: float = Field(default=0.10, ge=0)
    margin_level_floor_percent: float = Field(default=300.0, ge=100)
    mode: OperatingMode = DEFAULT_MODE

    def safety_summary(self) -> str:
        return (
            f"{self.mode.label} mode · {self.risk_per_trade_percent:g}% risk per trade · "
            f"max {self.max_open_trades} open trades"
        )
