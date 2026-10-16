"""Strategy 6: daily trend following (16 October 2026). The one family with long, broad
evidence behind it, unlike the M15 lab strategies.

Time-series momentum: a market that rose over the past year tends to keep rising (Moskowitz,
Ooi and Pedersen 2012, 58 futures and currency markets; Hurst, Ooi and Pedersen 2017, 1880
to 2016, positive in every decade after costs). The edge is small (a Sharpe of about 0.2 to
0.5 for a few symbols), it wins only 35 to 45% of its trades and pays through a few long
winners, so it needs 100 or more trades before its result means anything.

Rules, long (short mirrored), checked once a day on the H4 bar that closes at `entry_hour`
broker time (12:00 by default: London is open, the rollover and its wide spread are far):
- yesterday's D1 close is above the highest high of the `breakout_days` (55) days before it
  (a Donchian breakout, as the Turtles traded it);
- and above the D1 close of `trend_days` (250, about a year) days ago (the year's trend);
- market order; stop `sl_atr` (3) x ATR(20) of D1 away, a far take profit (`reward_r`, 5R),
  and the exit comes from the trailing stop (`DEFAULT_MANAGEMENT` in
  app/core/execution_settings.py: 7 ATR of H4, about 3 ATR of D1, from 1R on).

The stop is wide, so the size is small (0.25% of 10,000 USD at 3 ATR of EURUSD is about 0.01
lot) and the commission is a tiny share of the risk. Not proven on this account: backtest it
on two years or more with the real commission first.
"""

from __future__ import annotations

import math
from typing import ClassVar

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.analysis import indicators
from app.analysis.bars import TF_SECONDS
from app.domain.signals import Direction, OrderType
from app.strategies.base import Condition, Evaluation, SetupState, Strategy
from app.strategies.context import MarketContext

DAY = 86_400
NEAR_ATR = 0.5  # a close this close to the channel counts as forming


class TrendFollowingParams(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    breakout_days: int = Field(
        default=55,
        ge=10,
        le=200,
        description="D1 close beyond the high or low of this many days",
    )
    trend_days: int = Field(
        default=250,
        ge=20,
        le=280,
        description="And beyond the D1 close of this many days ago",
    )
    atr_days: int = Field(default=20, ge=5, le=100, description="D1 ATR period")
    sl_atr: float = Field(default=3.0, ge=0.5, le=10, description="Stop distance in D1 ATR")
    reward_r: float = Field(
        default=5.0,
        ge=1,
        le=20,
        description="Far take profit in R (the trailing stop exits first)",
    )
    entry_hour: int = Field(
        default=12,
        ge=0,
        le=20,
        multiple_of=4,
        description="Broker hour of the daily check (an H4 close: 0, 4, 8, 12, 16 or 20)",
    )


def close_hour(ctx: MarketContext) -> int:
    """The broker server hour at which the newest closed entry bar closed."""
    entry = ctx.entry
    if not len(entry):
        return -1
    server_open = int(entry.server_time[-1]) if len(entry.server_time) else int(entry.time[-1])
    return (server_open + TF_SECONDS[ctx.timeframe]) % DAY // 3600


class TrendFollowing(Strategy):
    name: ClassVar[str] = "trend_following"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Daily trend following"
    description: ClassVar[str] = (
        "Once a day: when the D1 close breaks the 55-day high (low) and is above (below) the "
        "close of a year ago, buys (sells) at the market. Stop 3 ATR of D1, a trailing stop "
        "takes the exit. Few trades, about 4 in 10 win, the winners are large."
    )
    params_model: ClassVar[type[BaseModel]] = TrendFollowingParams
    entry_timeframe: ClassVar[str] = "H4"
    required_history: ClassVar[dict[str, int]] = {"H4": 20, "D1": 285}
    sessions: ClassVar[tuple[str, ...]] = ("London", "New York")
    example: ClassVar[bool] = False

    def __init__(self, params: TrendFollowingParams | None = None) -> None:
        self.cfg = params if params is not None else TrendFollowingParams()
        super().__init__(self.cfg)

    def evaluate(self, ctx: MarketContext) -> Evaluation:
        cfg = self.cfg
        history = self.history_condition(ctx)
        if not history.passed:
            return self.result(ctx, SetupState.NONE, [history], note="waiting for history")
        hour = close_hour(ctx)
        timing = Condition(
            f"daily check at {cfg.entry_hour:02d}:00 broker time",
            hour == cfg.entry_hour,
            value=hour,
            threshold=cfg.entry_hour,
        )
        if not timing.passed:
            return self.result(ctx, SetupState.NONE, [history, timing], note="not the daily check")
        d1 = ctx.bars["D1"]
        high, low, close = d1.high, d1.low, d1.close
        days = cfg.breakout_days
        atr_now = indicators.last(indicators.atr(high, low, close, cfg.atr_days))
        if len(close) <= max(days, cfg.trend_days) or not math.isfinite(atr_now) or atr_now <= 0:
            missing = Condition("D1 channel and ATR", False, detail="not enough D1 bars")
            return self.result(ctx, SetupState.NONE, [history, timing, missing])
        last = float(close[-1])
        top = float(np.max(high[-days - 1 : -1]))
        bottom = float(np.min(low[-days - 1 : -1]))
        direction: Direction | None = None
        if last > top:
            direction = Direction.LONG
        elif last < bottom:
            direction = Direction.SHORT
        breakout = Condition(
            f"D1 close beyond the {days}-day high or low",
            direction is not None,
            value=direction.value if direction is not None else None,
            detail=f"high {top:.{ctx.digits}f}, low {bottom:.{ctx.digits}f}",
        )
        conditions = [history, timing, breakout]
        if direction is None:
            near = min(top - last, last - bottom) <= NEAR_ATR * atr_now
            state = SetupState.FORMING if near else SetupState.NONE
            return self.result(ctx, state, conditions, note="inside the channel")
        sign = direction.sign
        past = float(close[-1 - cfg.trend_days])
        change = (last - past) / past * 100 if past > 0 else math.nan
        side = "above" if sign > 0 else "below"
        trend = Condition(
            f"D1 close {side} the close of {cfg.trend_days} days ago",
            (last - past) * sign > 0,
            value=round(change, 2) if math.isfinite(change) else None,
            threshold=0.0,
            detail="change in %",
        )
        conditions.append(trend)
        if not trend.passed:
            return self.result(ctx, SetupState.FORMING, conditions, note="against the year")
        entry = float(ctx.entry.close[-1])
        distance = cfg.sl_atr * atr_now
        level = top if sign > 0 else bottom
        reason = (
            f"D1 close {last:.{ctx.digits}f} broke the {days}-day "
            f"{'high' if sign > 0 else 'low'} {level:.{ctx.digits}f} and is {side} the close "
            f"of {cfg.trend_days} days ago ({change:+.1f}%); stop {cfg.sl_atr:g} ATR of D1, "
            "a trailing stop takes the exit"
        )
        signal = self.make_signal(
            ctx,
            direction,
            OrderType.MARKET,
            entry=entry,
            sl=entry - sign * distance,
            tp=entry + sign * cfg.reward_r * distance,
            reason=reason,
            expires_at=self.bars_later(ctx, 1),
            features={
                "atr_d1": round(atr_now, 6),
                "breakout_level": round(level, ctx.digits),
                "year_change_pct": round(change, 3),
            },
        )
        return self.result(ctx, SetupState.READY, conditions, [signal], note="signal")
