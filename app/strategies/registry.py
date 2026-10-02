"""The built-in strategies by name, and building one from saved settings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.strategies.base import Strategy, StrategyInfo
from app.strategies.london_breakout import LondonBreakout
from app.strategies.trend_pullback import TrendPullback

STRATEGIES: dict[str, type[Strategy]] = {
    TrendPullback.name: TrendPullback,
    LondonBreakout.name: LondonBreakout,
}


def strategy_info(name: str) -> StrategyInfo:
    kind = STRATEGIES[name]
    return StrategyInfo(
        name=kind.name,
        version=kind.version,
        title=kind.title,
        description=kind.description,
        entry_timeframe=kind.entry_timeframe,
        example=kind.example,
        defaults=kind.params_model().model_dump(mode="json"),
    )


def create_strategy(name: str, params: Mapping[str, Any] | None = None) -> Strategy:
    """A strategy with validated params. Raises KeyError or pydantic's ValidationError."""
    kind = STRATEGIES[name]
    return kind(kind.params_model.model_validate(dict(params or {})))
