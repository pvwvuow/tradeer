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

# One magic number per strategy (spec C7): MT5 marks every bot order and position with it, so
# the risk limits count each strategy's trades and manual trades (magic 0) apart. Never reuse
# or change a number: old positions and history are matched by it.
MAGIC_NUMBERS: dict[str, int] = {
    TrendPullback.name: 26_070_001,
    LondonBreakout.name: 26_070_002,
}


def strategy_for_magic(magic: int) -> str:
    """The strategy name of a bot magic number, "" for manual trades and other EAs."""
    for name, number in MAGIC_NUMBERS.items():
        if number == magic:
            return name
    return ""


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
