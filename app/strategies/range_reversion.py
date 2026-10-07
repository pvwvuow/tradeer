"""Lab strategy 3: range reversion. Not proven to be profitable.

When H1 has no trend (ADX(14) below 22), a M15 close outside the 20-bar band (SMA20 +- 2
standard deviations) that closes back inside is a stretch the range is likely to undo. Long
(short mirrored): the previous M15 bar closed below the lower band, this one closes above it
and RSI(14) is still below 45. SL beyond the low of the last 3 bars plus 0.2 ATR, at least
1 ATR and at most 2.5 ATR; TP at the band's middle (SMA20), only when that is 1R or more
away. Market order at the close of the signal bar. Trades the quiet hours (Asia, London).
"""

from __future__ import annotations

import math
from typing import ClassVar

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.analysis import indicators
from app.analysis.bars import FloatArray
from app.domain.signals import Direction, OrderType
from app.strategies.base import Condition, Evaluation, SetupState, Strategy
from app.strategies.context import MarketContext


class RangeReversionParams(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    max_adx_h1: float = Field(default=22.0, ge=5, le=60, description="Maximum ADX(14) on H1")
    band_period: int = Field(default=20, ge=5, le=100, description="M15 bars of the band")
    band_width: float = Field(default=2.0, gt=0, le=5, description="Band width in deviations")
    rsi_period: int = Field(default=14, ge=2, le=50, description="RSI period on M15")
    rsi_limit: float = Field(
        default=45.0,
        ge=10,
        le=50,
        description="Long below this RSI, short above 100 minus it",
    )
    swing_bars: int = Field(default=3, ge=1, le=20, description="Bars whose extreme sets the SL")
    buffer_atr: float = Field(default=0.2, ge=0, le=2, description="SL beyond the extreme, ATR")
    sl_atr: float = Field(default=1.0, gt=0, le=10, description="Minimum stop distance in ATR")
    max_sl_atr: float = Field(default=2.5, gt=0, le=20, description="Maximum stop distance in ATR")
    min_reward_r: float = Field(
        default=1.0,
        gt=0,
        le=10,
        description="Smallest distance to the band's middle, in R",
    )
    expiry_bars: int = Field(
        default=2,
        ge=1,
        le=20,
        description="Bars until an unused signal expires",
    )


def bands(close: FloatArray, period: int, width: float) -> tuple[FloatArray, FloatArray]:
    """The band's middle (SMA) and half width (`width` population deviations), NaN early."""
    middle = indicators.sma(close, period)
    half = np.full(len(close), np.nan, dtype=np.float64)
    if 0 < period <= len(close):
        windows = np.lib.stride_tricks.sliding_window_view(close, period)
        half[period - 1 :] = width * windows.std(axis=1)
    return middle, half


class RangeReversion(Strategy):
    name: ClassVar[str] = "range_reversion"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Range reversion"
    description: ClassVar[str] = (
        "Without an H1 trend, fades a M15 close outside the 20-bar band once the next bar "
        "closes back inside. SL beyond the last 3 bars (1 to 2.5 ATR), TP at the band's middle."
    )
    params_model: ClassVar[type[BaseModel]] = RangeReversionParams
    entry_timeframe: ClassVar[str] = "M15"
    required_history: ClassVar[dict[str, int]] = {"M15": 120, "H1": 60}
    sessions: ClassVar[tuple[str, ...]] = ("Asia", "London")

    def __init__(self, params: RangeReversionParams | None = None) -> None:
        self.cfg = params if params is not None else RangeReversionParams()
        super().__init__(self.cfg)

    def evaluate(self, ctx: MarketContext) -> Evaluation:
        cfg = self.cfg
        history = self.history_condition(ctx)
        if not history.passed:
            return self.result(ctx, SetupState.NONE, [history], note="waiting for history")
        hourly = ctx.bars["H1"]
        adx = indicators.last(indicators.adx(hourly.high, hourly.low, hourly.close)[0])
        calm = math.isfinite(adx) and adx < cfg.max_adx_h1
        quiet = Condition(
            f"H1 ADX(14) below {cfg.max_adx_h1:g} (no trend)",
            calm,
            value=round(adx, 1) if math.isfinite(adx) else None,
            threshold=cfg.max_adx_h1,
        )
        conditions = [history, quiet]
        if not calm:
            return self.result(ctx, SetupState.NONE, conditions, note="H1 is trending")

        m15 = ctx.entry
        close = m15.close
        middle, half = bands(close, cfg.band_period, cfg.band_width)
        atr_now = indicators.last(indicators.atr(m15.high, m15.low, close))
        values = (middle[-1], middle[-2], half[-1], half[-2], atr_now)
        if not all(math.isfinite(value) for value in values) or atr_now <= 0:
            missing = Condition("M15 band and ATR", False, detail="not enough M15 bars")
            return self.result(ctx, SetupState.NONE, [*conditions, missing])
        previous, last = float(close[-2]), float(close[-1])
        lower_before, lower_now = middle[-2] - half[-2], middle[-1] - half[-1]
        upper_before, upper_now = middle[-2] + half[-2], middle[-1] + half[-1]
        direction: Direction | None = None
        if previous < lower_before and last > lower_now:
            direction = Direction.LONG
        elif previous > upper_before and last < upper_now:
            direction = Direction.SHORT
        back = Condition(
            f"M15 close back inside the {cfg.band_period}-bar band",
            direction is not None,
            value=direction.value if direction is not None else None,
            detail=f"band {lower_now:.{ctx.digits}f} to {upper_now:.{ctx.digits}f}",
        )
        conditions.append(back)
        if direction is None:
            return self.result(ctx, SetupState.NONE, conditions, note="no stretch")
        sign = direction.sign
        rsi_now = indicators.last(indicators.rsi(close, cfg.rsi_period))
        limit = cfg.rsi_limit if direction is Direction.LONG else 100.0 - cfg.rsi_limit
        stretched = math.isfinite(rsi_now) and (rsi_now - limit) * sign < 0
        side = "below" if direction is Direction.LONG else "above"
        momentum = Condition(
            f"M15 RSI({cfg.rsi_period}) still {side} {limit:g}",
            stretched,
            value=round(rsi_now, 1) if math.isfinite(rsi_now) else None,
            threshold=limit,
        )
        conditions.append(momentum)
        if not stretched:
            return self.result(ctx, SetupState.FORMING, conditions, note="RSI already turned")

        window = slice(len(m15) - cfg.swing_bars, len(m15))
        if direction is Direction.LONG:
            extreme = float(m15.low[window].min()) - cfg.buffer_atr * atr_now
        else:
            extreme = float(m15.high[window].max()) + cfg.buffer_atr * atr_now
        distance = (last - extreme) * sign
        distance = min(max(distance, cfg.sl_atr * atr_now), cfg.max_sl_atr * atr_now)
        target = float(middle[-1])
        reward = (target - last) * sign / distance
        room = Condition(
            f"band middle at least {cfg.min_reward_r:g}R away",
            reward >= cfg.min_reward_r,
            value=round(reward, 2),
            threshold=cfg.min_reward_r,
            detail=f"SL {distance / atr_now:.2f} ATR, target {target:.{ctx.digits}f}",
        )
        conditions.append(room)
        if not room.passed:
            return self.result(ctx, SetupState.FORMING, conditions, note="target too close")
        reason = (
            f"H1 without a trend (ADX {adx:.0f}), M15 closed back inside the band after a "
            f"stretch {'down' if sign > 0 else 'up'}, RSI {rsi_now:.1f}; target the middle"
        )
        signal = self.make_signal(
            ctx,
            direction,
            OrderType.MARKET,
            entry=last,
            sl=last - sign * distance,
            tp=target,
            reason=reason,
            expires_at=self.bars_later(ctx, cfg.expiry_bars),
            features={
                "adx_h1": round(adx, 2),
                "rsi_m15": round(rsi_now, 2),
                "band_atr": round(2 * float(half[-1]) / atr_now, 3),
                "stop_atr": round(distance / atr_now, 3),
            },
        )
        return self.result(ctx, SetupState.READY, conditions, [signal], note="signal")
