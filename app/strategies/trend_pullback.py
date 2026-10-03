"""Example strategy 1 (spec C4): trend pullback. Not proven to be profitable.

Long (short mirrored): H1 EMA50 above EMA200 and ADX(H1) above 20; on M15 the price pulls
back into the EMA20 zone and RSI(14) crosses back above 50. SL beyond the last confirmed M15
swing or 1.5 ATR, whichever is farther, capped at 3 ATR; TP 2R. No entry when a key level
lies within 1R in the trade direction. Market order at the close of the signal bar.
"""

from __future__ import annotations

import math
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.analysis import indicators
from app.analysis.structure import SwingKind, find_swings
from app.domain.signals import Direction, OrderType
from app.strategies.base import Condition, Evaluation, SetupState, Strategy
from app.strategies.context import MarketContext


class TrendPullbackParams(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    fast_ema_h1: int = Field(default=50, ge=5, le=400, description="H1 fast EMA")
    slow_ema_h1: int = Field(default=200, ge=10, le=500, description="H1 slow EMA")
    min_adx_h1: float = Field(default=20.0, ge=0, le=60, description="Minimum ADX(14) on H1")
    ema_m15: int = Field(default=20, ge=5, le=100, description="M15 EMA of the pullback zone")
    zone_atr: float = Field(
        default=0.25,
        ge=0,
        le=2,
        description="Half width of the EMA zone in ATR",
    )
    pullback_bars: int = Field(
        default=5,
        ge=1,
        le=30,
        description="Bars in which the zone is touched",
    )
    rsi_period: int = Field(default=14, ge=2, le=50, description="RSI period on M15")
    rsi_level: float = Field(default=50.0, ge=10, le=90, description="RSI level to cross back over")
    sl_atr: float = Field(default=1.5, gt=0, le=10, description="Minimum stop distance in ATR")
    max_sl_atr: float = Field(default=3.0, gt=0, le=20, description="Maximum stop distance in ATR")
    swing_buffer_atr: float = Field(
        default=0.1,
        ge=0,
        le=2,
        description="Stop beyond the swing, ATR",
    )
    reward_r: float = Field(default=2.0, gt=0, le=10, description="Take profit in R")
    level_block_r: float = Field(default=1.0, ge=0, le=5, description="No key level within this R")
    expiry_bars: int = Field(
        default=2,
        ge=1,
        le=20,
        description="Bars until an unused signal expires",
    )


class TrendPullback(Strategy):
    name: ClassVar[str] = "trend_pullback"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "Trend pullback"
    description: ClassVar[str] = (
        "Trades with the H1 trend after an M15 pullback to the EMA20 zone, when RSI turns back "
        "over 50. SL beyond the last swing or 1.5 ATR (max 3 ATR), TP 2R."
    )
    params_model: ClassVar[type[BaseModel]] = TrendPullbackParams
    entry_timeframe: ClassVar[str] = "M15"
    required_history: ClassVar[dict[str, int]] = {"M15": 120, "H1": 300}
    sessions: ClassVar[tuple[str, ...]] = ("London", "New York")

    def __init__(self, params: TrendPullbackParams | None = None) -> None:
        self.cfg = params if params is not None else TrendPullbackParams()
        super().__init__(self.cfg)

    def evaluate(self, ctx: MarketContext) -> Evaluation:
        cfg = self.cfg
        history = self.history_condition(ctx)
        if not history.passed:
            return self.result(ctx, SetupState.NONE, [history], note="waiting for history")
        hourly = ctx.bars["H1"]
        m15 = ctx.entry
        fast = indicators.last(indicators.ema(hourly.close, cfg.fast_ema_h1))
        slow = indicators.last(indicators.ema(hourly.close, cfg.slow_ema_h1))
        adx = indicators.last(indicators.adx(hourly.high, hourly.low, hourly.close)[0])
        digits = ctx.digits
        if not (math.isfinite(fast) and math.isfinite(slow)) or fast == slow:
            trend = Condition("H1 trend (EMA50 vs EMA200)", False, detail="no clear EMA order")
            return self.result(ctx, SetupState.NONE, [history, trend], note="no H1 trend")
        direction = Direction.LONG if fast > slow else Direction.SHORT
        sign = direction.sign
        word = "above" if fast > slow else "below"
        trend = Condition(
            f"H1 trend (EMA{cfg.fast_ema_h1} vs EMA{cfg.slow_ema_h1})",
            True,
            value=direction.value,
            detail=f"EMA{cfg.fast_ema_h1} {fast:.{digits}f} {word} EMA{cfg.slow_ema_h1} "
            f"{slow:.{digits}f}",
        )
        strong = adx > cfg.min_adx_h1
        strength = Condition("H1 ADX(14)", strong, value=adx, threshold=cfg.min_adx_h1)
        conditions = [history, trend, strength]
        if not strength.passed:
            return self.result(ctx, SetupState.NONE, conditions, note="H1 trend too weak")

        ema = indicators.ema(m15.close, cfg.ema_m15)
        atr = indicators.atr(m15.high, m15.low, m15.close)
        rsi = indicators.rsi(m15.close, cfg.rsi_period)
        atr_now = indicators.last(atr)
        close = m15.last_close
        if not (math.isfinite(atr_now) and atr_now > 0 and math.isfinite(ema[-1])):
            missing = Condition("M15 ATR and EMA", False, detail="not enough M15 bars")
            return self.result(ctx, SetupState.NONE, [*conditions, missing])
        window = slice(len(m15) - cfg.pullback_bars, len(m15))
        zone_edge = ema[window] + sign * cfg.zone_atr * atr[window]
        if direction is Direction.LONG:
            reach = float((m15.low[window] - zone_edge).min())  # <= 0: a low touched the zone
            beyond = float(ema[-1] - cfg.zone_atr * atr_now - close)  # > 0: closed below
        else:
            reach = float((zone_edge - m15.high[window]).min())
            beyond = float(close - (ema[-1] + cfg.zone_atr * atr_now))
        touched = reach <= 0 and beyond <= 0
        broke = "" if beyond <= 0 else "; the close broke through the zone"
        zone = f"zone EMA{cfg.ema_m15} \u00b1 {cfg.zone_atr:g} ATR"
        pullback = Condition(
            f"M15 pullback to the EMA{cfg.ema_m15} zone",
            touched,
            value=round(reach / atr_now, 2),
            threshold=0.0,
            detail=f"in the last {cfg.pullback_bars} bars; {zone}{broke}",
        )
        conditions.append(pullback)
        previous, current = float(rsi[-2]), float(rsi[-1])
        if direction is Direction.LONG:
            crossed = previous <= cfg.rsi_level < current
        else:
            crossed = previous >= cfg.rsi_level > current
        side = "above" if direction is Direction.LONG else "below"
        turn = Condition(
            f"M15 RSI({cfg.rsi_period}) crosses back {side} {cfg.rsi_level:g}",
            crossed,
            value=round(current, 1),
            threshold=cfg.rsi_level,
            detail=f"previous bar {previous:.1f}",
        )
        conditions.append(turn)
        if not touched:
            return self.result(ctx, SetupState.NONE, conditions, note="no pullback yet")
        if not crossed:
            return self.result(ctx, SetupState.FORMING, conditions, note="waiting for RSI")

        entry = close
        stop_distance, stop_detail = self._stop_distance(ctx, direction, entry, atr_now)
        capped = stop_distance > cfg.max_sl_atr * atr_now
        if capped:
            stop_distance = cfg.max_sl_atr * atr_now
        stop = Condition(
            "stop loss distance",
            True,
            value=round(stop_distance / atr_now, 2),
            threshold=cfg.max_sl_atr,
            detail=stop_detail + (f", capped at {cfg.max_sl_atr:g} ATR" if capped else ""),
        )
        conditions.append(stop)
        sl = entry - sign * stop_distance
        tp = entry + sign * cfg.reward_r * stop_distance
        blocker = self._level_in_the_way(ctx, direction, entry, stop_distance)
        level = Condition(
            f"no key level within {cfg.level_block_r:g}R",
            blocker is None,
            value=blocker,
            detail="in the trade direction",
        )
        conditions.append(level)
        if blocker is not None:
            return self.result(ctx, SetupState.FORMING, conditions, note="key level in the way")
        reason = (
            f"H1 {'uptrend' if sign > 0 else 'downtrend'} (EMA{cfg.fast_ema_h1} {word} "
            f"EMA{cfg.slow_ema_h1}, ADX {adx:.0f}), M15 pullback to EMA{cfg.ema_m15}, RSI back "
            f"{side} {cfg.rsi_level:g} ({current:.1f})"
        )
        signal = self.make_signal(
            ctx,
            direction,
            OrderType.MARKET,
            entry=entry,
            sl=sl,
            tp=tp,
            reason=reason,
            expires_at=self.bars_later(ctx, cfg.expiry_bars),
            features={
                "adx_h1": round(adx, 2),
                "rsi_m15": round(current, 2),
                "ema_gap_h1_atr": round((fast - slow) / atr_now, 3),
                "stop_atr": round(stop_distance / atr_now, 3),
            },
        )
        return self.result(ctx, SetupState.READY, conditions, [signal], note="signal")

    def _stop_distance(
        self,
        ctx: MarketContext,
        direction: Direction,
        entry: float,
        atr: float,
    ) -> tuple[float, str]:
        cfg = self.cfg
        minimum = cfg.sl_atr * atr
        kind = SwingKind.LOW if direction is Direction.LONG else SwingKind.HIGH
        swing = next(
            (item for item in reversed(find_swings(ctx.entry)) if item.kind is kind),
            None,
        )
        if swing is None:
            return minimum, f"{cfg.sl_atr:g} ATR (no confirmed swing)"
        level = swing.price - direction.sign * cfg.swing_buffer_atr * atr
        distance = (entry - level) * direction.sign
        name = "low" if kind is SwingKind.LOW else "high"
        if distance <= minimum:
            return minimum, f"{cfg.sl_atr:g} ATR (the swing {name} is closer)"
        return distance, f"beyond the swing {name} {swing.price:.{ctx.digits}f}"

    def _level_in_the_way(
        self,
        ctx: MarketContext,
        direction: Direction,
        entry: float,
        risk: float,
    ) -> str | None:
        if ctx.levels is None or self.cfg.level_block_r <= 0:
            return None
        limit = self.cfg.level_block_r * risk
        for level in ctx.levels.levels:
            ahead = (level.price - entry) * direction.sign
            if 0 < ahead <= limit:
                return f"{level.kind} {level.price:.{ctx.digits}f}"
        return None
