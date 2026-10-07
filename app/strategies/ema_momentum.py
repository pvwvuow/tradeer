"""Lab strategy 5: EMA momentum cross. Not proven to be profitable.

Long (short mirrored): on M15 the EMA9 crosses above the EMA21 while the close is above the
EMA100, ADX(14) is above 25 and RSI(14) is between 50 and 70 (momentum, not yet stretched).
SL 1.5 ATR, TP 1.5R. Market order at the close of the signal bar. Uses M15 alone, so it
trades turns the H1 filters of the other strategies would still miss.
"""

from __future__ import annotations

import math
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.analysis import indicators
from app.domain.signals import Direction, OrderType
from app.strategies.base import Condition, Evaluation, SetupState, Strategy
from app.strategies.context import MarketContext


class EmaMomentumParams(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    fast_ema: int = Field(default=9, ge=2, le=100, description="M15 fast EMA")
    slow_ema: int = Field(default=21, ge=3, le=200, description="M15 slow EMA")
    trend_ema: int = Field(default=100, ge=10, le=400, description="M15 trend EMA")
    min_adx: float = Field(default=25.0, ge=0, le=60, description="Minimum ADX(14) on M15")
    rsi_period: int = Field(default=14, ge=2, le=50, description="RSI period on M15")
    rsi_max: float = Field(
        default=70.0,
        ge=50,
        le=95,
        description="Long up to this RSI, short down to 100 minus it",
    )
    sl_atr: float = Field(default=1.5, gt=0, le=10, description="Stop distance in ATR")
    reward_r: float = Field(default=1.5, gt=0, le=10, description="Take profit in R")
    expiry_bars: int = Field(
        default=2,
        ge=1,
        le=20,
        description="Bars until an unused signal expires",
    )


class EmaMomentum(Strategy):
    name: ClassVar[str] = "ema_momentum"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "EMA momentum"
    description: ClassVar[str] = (
        "Trades a M15 EMA9/EMA21 cross on the side of the EMA100 when ADX is above 25 and RSI "
        "is between 50 and 70 (30 and 50 short). SL 1.5 ATR, TP 1.5R."
    )
    params_model: ClassVar[type[BaseModel]] = EmaMomentumParams
    entry_timeframe: ClassVar[str] = "M15"
    required_history: ClassVar[dict[str, int]] = {"M15": 200}
    sessions: ClassVar[tuple[str, ...]] = ("London", "New York")

    def __init__(self, params: EmaMomentumParams | None = None) -> None:
        self.cfg = params if params is not None else EmaMomentumParams()
        super().__init__(self.cfg)

    def evaluate(self, ctx: MarketContext) -> Evaluation:
        cfg = self.cfg
        history = self.history_condition(ctx)
        if not history.passed:
            return self.result(ctx, SetupState.NONE, [history], note="waiting for history")
        m15 = ctx.entry
        close = m15.close
        fast = indicators.ema(close, cfg.fast_ema)
        slow = indicators.ema(close, cfg.slow_ema)
        trend = indicators.last(indicators.ema(close, cfg.trend_ema))
        atr_now = indicators.last(indicators.atr(m15.high, m15.low, close))
        values = (fast[-1], fast[-2], slow[-1], slow[-2], trend, atr_now)
        if not all(math.isfinite(value) for value in values) or atr_now <= 0:
            missing = Condition("M15 EMAs and ATR", False, detail="not enough M15 bars")
            return self.result(ctx, SetupState.NONE, [history, missing])
        direction: Direction | None = None
        if fast[-2] <= slow[-2] and fast[-1] > slow[-1]:
            direction = Direction.LONG
        elif fast[-2] >= slow[-2] and fast[-1] < slow[-1]:
            direction = Direction.SHORT
        cross = Condition(
            f"M15 EMA{cfg.fast_ema} crosses EMA{cfg.slow_ema}",
            direction is not None,
            value=direction.value if direction is not None else None,
        )
        conditions = [history, cross]
        if direction is None:
            near = abs(float(fast[-1] - slow[-1])) <= 0.1 * atr_now
            state = SetupState.FORMING if near else SetupState.NONE
            return self.result(ctx, state, conditions, note="no cross")
        sign = direction.sign
        last = float(close[-1])
        side = "above" if direction is Direction.LONG else "below"
        with_trend = Condition(
            f"close {side} the EMA{cfg.trend_ema}",
            (last - trend) * sign > 0,
            value=round((last - trend) / atr_now, 2),
            threshold=0.0,
        )
        adx = indicators.last(indicators.adx(m15.high, m15.low, close)[0])
        strong = Condition(
            "M15 ADX(14)",
            math.isfinite(adx) and adx > cfg.min_adx,
            value=round(adx, 1) if math.isfinite(adx) else None,
            threshold=cfg.min_adx,
        )
        rsi_now = indicators.last(indicators.rsi(close, cfg.rsi_period))
        low, high = (50.0, cfg.rsi_max) if sign > 0 else (100.0 - cfg.rsi_max, 50.0)
        paced = Condition(
            f"M15 RSI({cfg.rsi_period}) between {low:g} and {high:g}",
            math.isfinite(rsi_now) and low <= rsi_now <= high,
            value=round(rsi_now, 1) if math.isfinite(rsi_now) else None,
        )
        conditions.extend([with_trend, strong, paced])
        if not (with_trend.passed and strong.passed and paced.passed):
            return self.result(ctx, SetupState.FORMING, conditions, note="cross without momentum")
        distance = cfg.sl_atr * atr_now
        reason = (
            f"M15 EMA{cfg.fast_ema} crossed {'above' if sign > 0 else 'below'} "
            f"EMA{cfg.slow_ema} {side} the EMA{cfg.trend_ema}, ADX {adx:.0f}, RSI {rsi_now:.1f}"
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
                "adx_m15": round(adx, 2),
                "rsi_m15": round(rsi_now, 2),
                "trend_gap_atr": round((last - trend) / atr_now, 3),
            },
        )
        return self.result(ctx, SetupState.READY, conditions, [signal], note="signal")
