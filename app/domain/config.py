"""Validated, conservative user defaults for the workstation."""

from pydantic import BaseModel, Field


class TradingDefaults(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["EURUSD", "GBPUSD", "XAUUSD"])
    entry_timeframe: str = "M15"
    context_timeframes: list[str] = Field(default_factory=lambda: ["H1", "H4", "D1"])
    risk_per_trade_percent: float = Field(default=0.5, gt=0, le=1)
    max_total_open_risk_percent: float = Field(default=1.5, gt=0, le=10)
    max_daily_loss_percent: float = Field(default=2, gt=0, le=10)
    max_total_drawdown_percent: float = Field(default=8, gt=0, le=50)
    max_open_trades: int = Field(default=3, ge=1, le=100)
    max_trades_per_day: int = Field(default=6, ge=1, le=100)
    min_win_probability_percent: float = Field(default=60, ge=0, le=100)
    min_expected_value_r: float = Field(default=0.10, ge=0)
    margin_level_floor_percent: float = Field(default=300, ge=0)
    mode: str = "paper"

    def safety_summary(self) -> str:
        return (
            f"Paper mode · {self.risk_per_trade_percent:g}% risk per trade · "
            f"max {self.max_open_trades} open trades"
        )
