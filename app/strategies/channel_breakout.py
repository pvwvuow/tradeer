"""Lab strategy 4: channel breakout with the H1 trend. Not proven to be profitable.

Long (short mirrored): H1 EMA50 above EMA200; a M15 bar closes above the highest high of
the 40 bars before it, and the bar before closed inside that channel (a fresh break). The
channel must be 2 to 10 ATR wide: a narrower one is noise, a wider one an exhausted move.
The close must clear the edge by at least 0.3 ATR (a close a hair past the edge is often a
false break), and no key level (support for a sell, resistance for a buy) may sit within
0.3 H1 ATR ahead of the entry. SL 2 ATR, TP 2R. Market order at the close of the signal bar.

8 October 2026: a sell 0.1 ATR below the channel low, right on an H1 support, was stopped
out. Both checks were added for that (version 1.1.0).
"""

from __future__ import annotations

import math
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.analysis import indicators
from app.domain.signals import Direction, OrderType
from app.strategies.base import Condition, Evaluation, SetupState, Strategy
from app.strategies.context import MarketContext


class ChannelBreakoutParams(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    fast_ema_h1: int = Field(default=50, ge=5, le=400, description="H1 fast EMA")
    slow_ema_h1: int = Field(default=200, ge=10, le=500, description="H1 slow EMA")
    channel_bars: int = Field(default=40, ge=5, le=300, description="M15 bars of the channel")
    min_width_atr: float = Field(default=2.0, ge=0, le=50, description="Narrowest channel, ATR")
    max_width_atr: float = Field(default=10.0, gt=0, le=100, description="Widest channel, ATR")
    min_break_atr: float = Field(
        default=0.3,
        ge=0,
        le=5,
        description="Smallest close past the edge, ATR",
    )
    level_clearance_atr: float = Field(
        default=0.3,
        ge=0,
        le=5,
        description="Room to the next key level, H1 ATR",
    )
    sl_atr: float = Field(default=2.0, gt=0, le=10, description="Stop distance in ATR")
    reward_r: float = Field(default=2.0, gt=0, le=10, description="Take profit in R")
    expiry_bars: int = Field(
        default=2,
        ge=1,
        le=20,
        description="Bars until an unused signal expires",
    )


def level_ahead(
    ctx: MarketContext,
    price: float,
    sign: int,
    clearance_atr: float,
) -> tuple[float, str] | None:
    """The nearest key level within `clearance_atr` H1 ATR ahead of `price` in the trade's
    direction (support below a sell, resistance above a buy): its distance in ATR and its
    text. None when there is none or no levels are known."""
    levels = ctx.levels
    if levels is None or not (math.isfinite(levels.atr) and levels.atr > 0):
        return None
    found: tuple[float, str] | None = None
    for level in levels.levels:
        ahead = (level.price - price) * sign / levels.atr
        if 0 <= ahead <= clearance_atr and (found is None or ahead < found[0]):
            text = f"{level.kind} {level.price:.{ctx.digits}f}"
            found = (ahead, text)
    return found


class ChannelBreakout(Strategy):
    name: ClassVar[str] = "channel_breakout"
    version: ClassVar[str] = "1.1.0"
    title: ClassVar[str] = "Channel breakout"
    description: ClassVar[str] = (
        "With the H1 trend, buys a fresh M15 close at least 0.3 ATR above the 40-bar high "
        "(sells below the low) when the channel is 2 to 10 ATR wide and no key level is just "
        "ahead. SL 2 ATR, TP 2R."
    )
    params_model: ClassVar[type[BaseModel]] = ChannelBreakoutParams
    entry_timeframe: ClassVar[str] = "M15"
    required_history: ClassVar[dict[str, int]] = {"M15": 120, "H1": 300}
    sessions: ClassVar[tuple[str, ...]] = ("London", "New York")

    def __init__(self, params: ChannelBreakoutParams | None = None) -> None:
        self.cfg = params if params is not None else ChannelBreakoutParams()
        super().__init__(self.cfg)

    def evaluate(self, ctx: MarketContext) -> Evaluation:
        cfg = self.cfg
        history = self.history_condition(ctx)
        if not history.passed:
            return self.result(ctx, SetupState.NONE, [history], note="waiting for history")
        hourly = ctx.bars["H1"]
        fast = indicators.last(indicators.ema(hourly.close, cfg.fast_ema_h1))
        slow = indicators.last(indicators.ema(hourly.close, cfg.slow_ema_h1))
        if not (math.isfinite(fast) and math.isfinite(slow)) or fast == slow:
            trend = Condition("H1 trend (EMA50 vs EMA200)", False, detail="no clear EMA order")
            return self.result(ctx, SetupState.NONE, [history, trend], note="no H1 trend")
        direction = Direction.LONG if fast > slow else Direction.SHORT
        sign = direction.sign
        trend = Condition(
            f"H1 trend (EMA{cfg.fast_ema_h1} vs EMA{cfg.slow_ema_h1})",
            True,
            value=direction.value,
        )
        conditions = [history, trend]

        m15 = ctx.entry
        count = cfg.channel_bars
        if len(m15) < count + 3:
            missing = Condition("M15 channel", False, detail="not enough M15 bars")
            return self.result(ctx, SetupState.NONE, [*conditions, missing])
        atr_now = indicators.last(indicators.atr(m15.high, m15.low, m15.close))
        if not (math.isfinite(atr_now) and atr_now > 0):
            missing = Condition("M15 ATR", False, detail="not enough M15 bars")
            return self.result(ctx, SetupState.NONE, [*conditions, missing])
        before = slice(len(m15) - 1 - count, len(m15) - 1)
        high, low = float(m15.high[before].max()), float(m15.low[before].min())
        width = (high - low) / atr_now
        sized = cfg.min_width_atr <= width <= cfg.max_width_atr
        channel = Condition(
            f"M15 {count}-bar channel {cfg.min_width_atr:g} to {cfg.max_width_atr:g} ATR wide",
            sized,
            value=round(width, 2),
            detail=f"{low:.{ctx.digits}f} to {high:.{ctx.digits}f}",
        )
        conditions.append(channel)
        edge = high if direction is Direction.LONG else low
        earlier = slice(len(m15) - 2 - count, len(m15) - 2)  # the channel of the bar before
        if direction is Direction.LONG:
            edge_before = float(m15.high[earlier].max())
        else:
            edge_before = float(m15.low[earlier].min())
        last, previous = float(m15.close[-1]), float(m15.close[-2])
        beyond = (last - edge) * sign / atr_now
        broke = beyond > 0 and (previous - edge_before) * sign <= 0
        word = "above the high" if direction is Direction.LONG else "below the low"
        breakout = Condition(
            f"fresh M15 close {word}",
            broke,
            value=round(beyond, 2),
            threshold=0.0,
            detail=f"edge {edge:.{ctx.digits}f}",
        )
        conditions.append(breakout)
        clear = beyond >= cfg.min_break_atr
        conditions.append(
            Condition(
                f"close at least {cfg.min_break_atr:g} ATR beyond the edge",
                clear,
                value=round(beyond, 2),
                threshold=cfg.min_break_atr,
                detail="a close a hair past the edge is often a false break",
            ),
        )
        blocker = level_ahead(ctx, last, sign, cfg.level_clearance_atr)
        kind = "support below" if direction is Direction.SHORT else "resistance above"
        room = Condition(
            f"no {kind} within {cfg.level_clearance_atr:g} H1 ATR",
            blocker is None,
            value=round(blocker[0], 2) if blocker is not None else None,
            threshold=cfg.level_clearance_atr,
            detail=blocker[1] if blocker is not None else "room to run",
        )
        conditions.append(room)
        if not sized:
            return self.result(ctx, SetupState.NONE, conditions, note="channel too narrow or wide")
        if not broke:
            near = abs(last - edge) <= atr_now
            state = SetupState.FORMING if near else SetupState.NONE
            return self.result(ctx, state, conditions, note="no breakout")
        if not clear:
            return self.result(ctx, SetupState.NONE, conditions, note="break too small")
        if blocker is not None:
            note = f"{kind.split()[0]} just ahead: {blocker[1]}"
            return self.result(ctx, SetupState.NONE, conditions, note=note)
        distance = cfg.sl_atr * atr_now
        reason = (
            f"H1 {'uptrend' if sign > 0 else 'downtrend'}, M15 closed {beyond:.1f} ATR {word} "
            f"of the last {count} bars ({width:.1f} ATR channel)"
        )
        signal = self.make_signal(
            ctx,
            direction,
            OrderType.MARKET,
            entry=last,
            sl=last - sign * distance,
            tp=last + sign * cfg.reward_r * distance,
            reason=reason,
            expires_at=self.bars_later(ctx, cfg.expiry_bars),
            features={
                "channel_atr": round(width, 3),
                "break_atr": round(beyond, 3),
                "ema_gap_h1_atr": round((fast - slow) / atr_now, 3),
            },
        )
        return self.result(ctx, SetupState.READY, conditions, [signal], note="signal")
