"""The built-in strategies by name, and building one from saved settings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.strategies.base import Strategy, StrategyInfo
from app.strategies.channel_breakout import ChannelBreakout
from app.strategies.ema_momentum import EmaMomentum
from app.strategies.london_breakout import LondonBreakout
from app.strategies.range_reversion import RangeReversion
from app.strategies.trend_pullback import TrendPullback

# The two spec examples, then the lab strategies (7 October 2026): different ideas (a range
# fade, a channel breakout, a M15 momentum cross) so a demo account running all of them at
# once shows which kind of trade works on which symbol and session.
STRATEGIES: dict[str, type[Strategy]] = {
    TrendPullback.name: TrendPullback,
    LondonBreakout.name: LondonBreakout,
    RangeReversion.name: RangeReversion,
    ChannelBreakout.name: ChannelBreakout,
    EmaMomentum.name: EmaMomentum,
}
# The lab strategies start off: on a real account every strategy that is on needs its own
# Go-Live approval before Auto may trade, so a new one must be turned on by choice (the
# Strategies page; docs/STRATEGY_LAB.md).
OFF_BY_DEFAULT: frozenset[str] = frozenset(
    {RangeReversion.name, ChannelBreakout.name, EmaMomentum.name},
)

# One magic number per strategy (spec C7): MT5 marks every bot order and position with it, so
# the risk limits count each strategy's trades and manual trades (magic 0) apart. Never reuse
# or change a number: old positions and history are matched by it. 26_070_098 is the demo
# test's (app.domain.history.TEST_MAGIC).
MAGIC_NUMBERS: dict[str, int] = {
    TrendPullback.name: 26_070_001,
    LondonBreakout.name: 26_070_002,
    RangeReversion.name: 26_070_003,
    ChannelBreakout.name: 26_070_004,
    EmaMomentum.name: 26_070_005,
}


def strategy_for_magic(magic: int) -> str:
    """The strategy name of a bot magic number, "" for manual trades and other EAs."""
    for name, number in MAGIC_NUMBERS.items():
        if number == magic:
            return name
    return ""


def on_by_default(name: str) -> bool:
    """Whether a strategy is on before the user chose: the spec examples yes, the lab no."""
    return name not in OFF_BY_DEFAULT


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
