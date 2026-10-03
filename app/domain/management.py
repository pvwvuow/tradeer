"""Position management rules (spec C7): break-even, ATR trailing, partial close, time exit,
and the running MFE/MAE. Pure: the execution engine applies the actions through a broker.

Every rule is off by default and set per strategy. The stop loss only ever moves toward
profit, and nothing is changed inside the broker's freeze level.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from pydantic import BaseModel, ConfigDict, Field

from app.domain.orders import exit_price, frozen, round_price
from app.domain.signals import Direction


class ManagementSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    break_even_at_r: float = Field(
        default=0.0,
        ge=0,
        le=10,
        description="Move the SL to the entry at this profit in R (0 = off)",
    )
    break_even_offset_points: int = Field(
        default=0,
        ge=0,
        le=10_000,
        description="Points beyond the entry for the break-even SL (covers costs)",
    )
    trailing_atr: float = Field(
        default=0.0,
        ge=0,
        le=10,
        description="Trail the SL this many ATR behind the best price (0 = off)",
    )
    trailing_start_r: float = Field(
        default=1.0,
        ge=0,
        le=10,
        description="Start trailing at this profit in R",
    )
    partial_close_at_r: float = Field(
        default=0.0,
        ge=0,
        le=10,
        description="Close part of the position at this profit in R (0 = off)",
    )
    partial_close_percent: float = Field(
        default=50.0,
        gt=0,
        lt=100,
        description="Part of the volume to close (%)",
    )
    time_exit_bars: int = Field(
        default=0,
        ge=0,
        le=10_000,
        description="Close after this many entry bars (0 = off)",
    )


@dataclass(frozen=True)
class ManagedPosition:
    ticket: int
    symbol: str
    direction: Direction
    entry: float
    sl: float
    tp: float
    initial_sl: float
    volume: float
    opened_at: float  # UTC seconds
    bar_seconds: int
    atr: float
    digits: int
    point: float
    tick_size: float
    stops_level: int
    freeze_level: int
    volume_min: float
    volume_step: float
    best_r: float = 0.0  # MFE in R
    worst_r: float = 0.0  # MAE in R (negative)
    break_even_done: bool = False
    partial_done: bool = False

    @property
    def risk(self) -> float:
        return abs(self.entry - self.initial_sl)

    def r_at(self, price: float) -> float:
        if self.risk <= 0:
            return 0.0
        return (price - self.entry) * self.direction.sign / self.risk


@dataclass(frozen=True)
class Action:
    kind: str  # modify_sl, partial_close, close
    reason: str
    sl: float | None = None
    volume: float | None = None


def track(position: ManagedPosition, bid: float, ask: float) -> ManagedPosition:
    """Update the maximum favourable and adverse excursion (MFE/MAE) in R."""
    r = position.r_at(exit_price(position.direction, bid, ask))
    if not math.isfinite(r):
        return position
    return replace(position, best_r=max(position.best_r, r), worst_r=min(position.worst_r, r))


def _better(direction: Direction, new: float, old: float) -> bool:
    return (new - old) * direction.sign > 0


def _allowed_sl(position: ManagedPosition, sl: float, price: float) -> bool:
    if position.point <= 0:
        return True
    distance = (price - sl) * position.direction.sign / position.point
    if distance < max(position.stops_level, 1):
        return False
    return not frozen(price, position.sl, position.freeze_level, position.point)


def plan(
    position: ManagedPosition,
    settings: ManagementSettings,
    bid: float,
    ask: float,
    now: float,
) -> list[Action]:
    """The actions for this price. At most one SL change; a close replaces everything else."""
    price = exit_price(position.direction, bid, ask)
    r = position.r_at(price)
    sign = position.direction.sign
    if settings.time_exit_bars > 0 and position.bar_seconds > 0:
        bars = int((now - position.opened_at) // position.bar_seconds)
        if bars >= settings.time_exit_bars:
            reason = f"time exit after {bars} bars"
            return [Action("close", reason, volume=position.volume)]
    actions: list[Action] = []
    partial = settings.partial_close_at_r
    if partial > 0 and not position.partial_done and r >= partial:
        part = _part(position, settings.partial_close_percent)
        if part > 0:
            actions.append(Action("partial_close", f"partial close at {r:.2f} R", volume=part))
    candidates: list[tuple[float, str]] = []
    even = settings.break_even_at_r
    if even > 0 and not position.break_even_done and r >= even:
        offset = settings.break_even_offset_points * position.point
        candidates.append((position.entry + sign * offset, f"break-even at {r:.2f} R"))
    trailing_ok = math.isfinite(position.atr) and position.atr > 0
    if settings.trailing_atr > 0 and trailing_ok and position.best_r >= settings.trailing_start_r:
        best_price = position.entry + sign * position.best_r * position.risk
        level = best_price - sign * settings.trailing_atr * position.atr
        candidates.append((level, f"ATR trailing ({settings.trailing_atr:g} ATR)"))
    best: tuple[float, str] | None = None
    for level, reason in candidates:
        level = round_price(level, position.digits, position.tick_size)
        if not _better(position.direction, level, position.sl):
            continue
        if not _allowed_sl(position, level, price):
            continue
        if best is None or _better(position.direction, level, best[0]):
            best = (level, reason)
    if best is not None:
        actions.append(Action("modify_sl", best[1], sl=best[0]))
    return actions


def _part(position: ManagedPosition, percent: float) -> float:
    step = position.volume_step if position.volume_step > 0 else 0.01
    raw = position.volume * percent / 100.0
    part = math.floor(raw / step + 1e-9) * step
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    part = round(part, decimals)
    rest = round(position.volume - part, decimals)
    if part < position.volume_min or rest < position.volume_min:
        return 0.0
    return part
