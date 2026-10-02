"""Example strategy 2 (spec C4): London breakout. Not proven to be profitable.

The Asia range (00:00 to 07:00) must be 0.5 to 1.5 times the D1-normalized ATR. At the London
open (08:00) a buy stop and a sell stop go 0.1 ATR beyond the range (one cancels the other,
so one trade per symbol per day); SL on the opposite side of the range, capped at 1.5 ATR;
TP 1.5R; both orders are cancelled at 11:00 if not triggered.

The times use London time by default (summer time included); `clock` switches them to the
broker's server time or UTC. "D1-normalized ATR" is ATR(14) of the daily bars scaled to the
length of the range: ATR(D1) x sqrt(range hours / 24) (ADR 52).
"""

from __future__ import annotations

import math
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analysis import indicators
from app.analysis.bars import TF_SECONDS
from app.core.clock import HOUR, eu_dst_active
from app.domain.signals import Direction, OrderType, Signal
from app.strategies.base import Condition, Evaluation, SetupState, Strategy
from app.strategies.context import MarketContext

DAY = 86_400
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


def minutes_of(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


class LondonBreakoutParams(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    clock: Literal["london", "broker", "utc"] = Field(
        default="london",
        description="Clock of the times",
    )
    range_start: str = Field(default="00:00", pattern=HHMM, description="Asia range starts")
    range_end: str = Field(default="07:00", pattern=HHMM, description="Asia range ends")
    entry_time: str = Field(default="08:00", pattern=HHMM, description="London open: stop orders")
    cancel_time: str = Field(default="11:00", pattern=HHMM, description="Cancel untriggered orders")
    min_range_atr: float = Field(default=0.5, ge=0, le=5, description="Narrowest range in ATR")
    max_range_atr: float = Field(default=1.5, gt=0, le=10, description="Widest range in ATR")
    buffer_atr: float = Field(
        default=0.1,
        ge=0,
        le=2,
        description="Stop order beyond the range, ATR",
    )
    max_sl_atr: float = Field(default=1.5, gt=0, le=10, description="Maximum stop distance in ATR")
    reward_r: float = Field(default=1.5, gt=0, le=10, description="Take profit in R")

    @model_validator(mode="after")
    def _times_in_order(self) -> LondonBreakoutParams:
        times = [self.range_start, self.range_end, self.entry_time, self.cancel_time]
        values = [minutes_of(value) for value in times]
        if not values[0] < values[1] <= values[2] < values[3]:
            raise ValueError("Times must be in order: range start < range end <= open < cancel")
        if self.min_range_atr >= self.max_range_atr:
            raise ValueError("The narrowest range must be smaller than the widest")
        return self


class LondonBreakout(Strategy):
    name: ClassVar[str] = "london_breakout"
    version: ClassVar[str] = "1.0.0"
    title: ClassVar[str] = "London breakout"
    description: ClassVar[str] = (
        "Places a buy stop and a sell stop beyond the Asia range at the London open when the "
        "range is 0.5 to 1.5 ATR wide. SL at the other side (max 1.5 ATR), TP 1.5R, cancelled "
        "at 11:00, one trade per symbol per day."
    )
    params_model: ClassVar[type[BaseModel]] = LondonBreakoutParams
    entry_timeframe: ClassVar[str] = "M15"
    required_history: ClassVar[dict[str, int]] = {"M15": 100, "D1": 20}
    sessions: ClassVar[tuple[str, ...]] = ("London",)

    def __init__(self, params: LondonBreakoutParams | None = None) -> None:
        self.cfg = params if params is not None else LondonBreakoutParams()
        super().__init__(self.cfg)

    def offset(self, ctx: MarketContext, utc_seconds: float) -> int:
        """Seconds to add to UTC to get the strategy's clock."""
        if self.cfg.clock == "utc":
            return 0
        if self.cfg.clock == "broker":
            return int(round(ctx.clock.offset_at(utc_seconds) * HOUR))
        return HOUR if eu_dst_active(utc_seconds) else 0

    def evaluate(self, ctx: MarketContext) -> Evaluation:
        cfg = self.cfg
        history = self.history_condition(ctx)
        if not history.passed:
            return self.result(ctx, SetupState.NONE, [history], note="waiting for history")
        close = ctx.close_time
        offset = self.offset(ctx, close)
        local = close + offset
        midnight = local - local % DAY - offset  # UTC time of the local midnight
        now_minutes = (local % DAY) // 60
        start, end = minutes_of(cfg.range_start), minutes_of(cfg.range_end)
        opening, cancel = minutes_of(cfg.entry_time), minutes_of(cfg.cancel_time)
        clock_name = {"london": "London time", "broker": "server time", "utc": "UTC"}[cfg.clock]
        complete = Condition(
            "Asia range complete",
            now_minutes >= end,
            value=f"{now_minutes // 60:02d}:{now_minutes % 60:02d}",
            threshold=cfg.range_end,
            detail=f"{cfg.range_start} to {cfg.range_end} {clock_name}",
        )
        conditions = [history, complete]
        if not complete.passed:
            return self.result(ctx, SetupState.NONE, conditions, note="the Asia range is building")
        m15 = ctx.entry
        seconds = TF_SECONDS[self.entry_timeframe]
        window_start, window_end = midnight + start * 60, midnight + end * 60
        inside = (m15.time >= window_start) & (m15.time + seconds <= window_end)
        expected = (end - start) * 60 // seconds
        count = int(inside.sum())
        enough = count >= max(expected // 2, 1)
        conditions.append(
            Condition("bars in the Asia range", enough, value=count, threshold=expected // 2),
        )
        if not enough:
            return self.result(ctx, SetupState.NONE, conditions, note="no Asia range today")
        high = float(m15.high[inside].max())
        low = float(m15.low[inside].min())
        daily = ctx.bars["D1"]
        atr_d1 = indicators.last(indicators.atr(daily.high, daily.low, daily.close))
        atr = atr_d1 * math.sqrt((end - start) / 60 / 24)
        if not (math.isfinite(atr) and atr > 0):
            conditions.append(Condition("D1 ATR", False, detail="not enough daily bars"))
            return self.result(ctx, SetupState.NONE, conditions)
        ratio = (high - low) / atr
        width = Condition(
            "Asia range width",
            cfg.min_range_atr <= ratio <= cfg.max_range_atr,
            value=round(ratio, 2),
            threshold=f"{cfg.min_range_atr:g} to {cfg.max_range_atr:g}",
            detail=f"{high - low:.{ctx.digits}f} wide; ATR = ATR(D1) x sqrt(range hours / 24)",
        )
        conditions.append(width)
        if now_minutes < opening:
            state = SetupState.FORMING if width.passed else SetupState.NONE
            return self.result(ctx, state, conditions, note="waiting for the London open")
        is_open_bar = now_minutes < opening + seconds // 60
        at_open = Condition(
            "London open bar",
            is_open_bar,
            value=complete.value,
            threshold=cfg.entry_time,
            detail="stop orders are placed once, at the open",
        )
        conditions.append(at_open)
        if not is_open_bar or not width.passed:
            note = "orders are placed only at the London open" if width.passed else "range"
            return self.result(ctx, SetupState.NONE, conditions, note=note)
        price = m15.last_close
        expires = float(midnight + cancel * 60)
        signals: list[Signal] = []
        for direction in (Direction.LONG, Direction.SHORT):
            signal = self._order(ctx, direction, high, low, atr, price, expires, conditions)
            if signal is not None:
                signals.append(signal)
        state = SetupState.READY if signals else SetupState.NONE
        return self.result(ctx, state, conditions, signals, note="stop orders at the open")

    def _order(
        self,
        ctx: MarketContext,
        direction: Direction,
        high: float,
        low: float,
        atr: float,
        price: float,
        expires: float,
        conditions: list[Condition],
    ) -> Signal | None:
        cfg = self.cfg
        sign = direction.sign
        trigger = high + cfg.buffer_atr * atr if sign > 0 else low - cfg.buffer_atr * atr
        side = "buy stop above" if sign > 0 else "sell stop below"
        room = (trigger - price) * sign
        conditions.append(
            Condition(
                f"price has not passed the {side.split()[0]} stop",
                room > 0,
                value=round(room / atr, 2),
                threshold=0.0,
                detail=f"{side} the range at {trigger:.{ctx.digits}f}",
            ),
        )
        if room <= 0:
            return None
        opposite = low if sign > 0 else high
        distance = (trigger - opposite) * sign
        capped = distance > cfg.max_sl_atr * atr
        if capped:
            distance = cfg.max_sl_atr * atr
        sl = trigger - sign * distance
        tp = trigger + sign * cfg.reward_r * distance
        day = ctx.close_time - ctx.close_time % DAY
        stop_text = f", SL capped at {cfg.max_sl_atr:g} ATR" if capped else ", SL at the other side"
        reason = (
            f"Asia range {low:.{ctx.digits}f} to {high:.{ctx.digits}f} "
            f"({(high - low) / atr:.2f} ATR); {side} at the London open{stop_text}"
        )
        return self.make_signal(
            ctx,
            direction,
            OrderType.STOP,
            entry=trigger,
            sl=sl,
            tp=tp,
            reason=reason,
            expires_at=expires,
            features={
                "range_atr": round((high - low) / atr, 3),
                "stop_atr": round(distance / atr, 3),
                "oco_group": f"{ctx.symbol}:{self.name}:{day}",
            },
        )
