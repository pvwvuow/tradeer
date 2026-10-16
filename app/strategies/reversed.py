"""Reversed strategies (16 October 2026, the owner's idea after 1 win in 12 live trades).

`X_reversed` runs strategy X's rules unchanged and takes the OPPOSITE trade: the other
direction, the stop loss at X's take profit and the take profit at X's stop loss, the same
entry. A buy stop becomes a sell limit at the same price (and a buy limit a sell stop), a
market order stays a market order. Its own name, magic number and settings, so MT5, the
journal and the stats keep it apart from X; the reason of every signal starts with
"REVERSED" and the MT5 comment with "tw-rev". Off by default; in the backtest it is not
ticked by default either. A lab idea, not proven to be profitable: the costs are paid on
both sides, so X losing does not make its reverse win.

Pure like every strategy: one inner X per reversed strategy, the same `MarketContext` gives
the same `Evaluation`.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from app.domain.signals import Direction, OrderType, Signal, signal_id
from app.strategies.base import Evaluation, Strategy
from app.strategies.channel_breakout import ChannelBreakout
from app.strategies.context import MarketContext
from app.strategies.ema_momentum import EmaMomentum
from app.strategies.london_breakout import LondonBreakout
from app.strategies.range_reversion import RangeReversion
from app.strategies.trend_pullback import TrendPullback

SUFFIX = "_reversed"
ID_PREFIX = "rev-"  # the MT5 comment then starts with "tw-rev" (app.domain.orders.short_id)
FLIPPED_ORDER: dict[OrderType, OrderType] = {
    OrderType.MARKET: OrderType.MARKET,
    OrderType.STOP: OrderType.LIMIT,
    OrderType.LIMIT: OrderType.STOP,
}
SIDE = {Direction.LONG: "buy", Direction.SHORT: "sell"}


def is_reversed(name: str) -> bool:
    return name.endswith(SUFFIX)


def opposite(direction: Direction) -> Direction:
    return Direction.SHORT if direction is Direction.LONG else Direction.LONG


def order_words(direction: Direction, order: OrderType) -> str:
    kind = "" if order is OrderType.MARKET else f" {order.value}"
    return f"{SIDE[direction]}{kind}"


class ReversedStrategy(Strategy):
    """The opposite trade of `inner_kind`; subclasses only name the strategy they reverse."""

    inner_kind: ClassVar[type[Strategy]]
    example: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        inner = cls.inner_kind
        cls.name = f"{inner.name}{SUFFIX}"
        cls.version = inner.version
        cls.title = f"{inner.title} (reversed)"
        cls.description = (
            f"The opposite of {inner.title}: when its rules fire, this one trades the other "
            "way, with the stop loss at its take profit and the take profit at its stop loss "
            "(a buy stop becomes a sell limit at the same price). Its rules: "
            f"{inner.description}"
        )
        cls.params_model = inner.params_model
        cls.entry_timeframe = inner.entry_timeframe
        cls.required_history = inner.required_history
        cls.sessions = inner.sessions

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self.inner = self.inner_kind(self.params)

    def evaluate(self, ctx: MarketContext) -> Evaluation:
        found = self.inner.evaluate(ctx)
        signals = tuple(self.flip(signal) for signal in found.signals)
        note = f"reversed {self.inner.name}: {found.note}" if found.note else found.note
        return Evaluation(
            self.name,
            found.symbol,
            found.timeframe,
            found.bar_time,
            found.state,
            found.conditions,
            signals,
            note,
        )

    def flip(self, signal: Signal) -> Signal:
        """The opposite trade of one of the inner strategy's signals."""
        inner = self.inner.name
        direction = opposite(signal.direction)
        order = FLIPPED_ORDER[signal.order_type]
        features: dict[str, float | str] = {}
        for key, value in signal.features.items():
            renamed = value
            if key == "oco_group" and isinstance(value, str):
                renamed = value.replace(f":{inner}:", f":{self.name}:")
            features[key] = renamed
        features["reversed_of"] = inner
        features["original_side"] = order_words(signal.direction, signal.order_type)
        key = f"{self.params_hash}:{direction.value}"
        reason = (
            f"REVERSED {inner}: its rules said {order_words(signal.direction, signal.order_type)}"
            f", this trade is the opposite ({order_words(direction, order)}, SL at its TP, TP "
            f"at its SL). Its reason: {signal.reason}"
        )
        bar = signal.bar_time
        new_id = ID_PREFIX + signal_id(self.name, key, signal.symbol, signal.timeframe, bar)
        return Signal(
            id=new_id,
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            direction=direction,
            order_type=order,
            entry=signal.entry,
            sl=signal.tp,
            tp=signal.sl,
            reason=reason,
            strategy=self.name,
            strategy_version=self.version,
            params_hash=self.params_hash,
            bar_time=bar,
            created_at=signal.created_at,
            expires_at=signal.expires_at,
            digits=signal.digits,
            config_id=signal.config_id,
            features=features,
        )


class TrendPullbackReversed(ReversedStrategy):
    inner_kind = TrendPullback


class LondonBreakoutReversed(ReversedStrategy):
    inner_kind = LondonBreakout


class RangeReversionReversed(ReversedStrategy):
    inner_kind = RangeReversion


class ChannelBreakoutReversed(ReversedStrategy):
    inner_kind = ChannelBreakout


class EmaMomentumReversed(ReversedStrategy):
    inner_kind = EmaMomentum


REVERSED: tuple[type[ReversedStrategy], ...] = (
    TrendPullbackReversed,
    LondonBreakoutReversed,
    RangeReversionReversed,
    ChannelBreakoutReversed,
    EmaMomentumReversed,
)
