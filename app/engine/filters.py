"""Signal filters (spec C5): pure checks, each recorded with value, threshold and pass/fail.

`None` as the result means "not applied", with the reason in the detail (for example a
probability that needs more history). Any `False` filters the signal out.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.analysis.sessions import active_sessions, market_open
from app.calendar.models import CalendarEvent, Impact, matches_symbol
from app.core.clock import HOUR, BrokerClock, us_dst_active
from app.domain.config import TradingDefaults
from app.domain.probability import BASELINE_MIN_SAMPLES, ProbabilityEstimate
from app.domain.signals import Direction, Signal
from app.mt5.models import SymbolTradeMode
from app.observability.decision_trace import TraceStep

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
_DEFAULTS = TradingDefaults()


class FilterSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    min_probability_percent: float = Field(
        default=_DEFAULTS.min_win_probability_percent,
        ge=0,
        le=100,
        description="Minimum win probability (%)",
    )
    min_ev_r: float = Field(default=_DEFAULTS.min_expected_value_r, description="Minimum EV in R")
    require_probability: bool = Field(
        default=False,
        description="Filter out signals whose probability is still unknown",
    )
    baseline_min_samples: int = Field(
        default=BASELINE_MIN_SAMPLES,
        ge=10,
        le=1000,
        description="Resolved signals needed for a baseline",
    )
    cooldown_bars_after_loss: int = Field(
        default=3,
        ge=0,
        le=100,
        description="Bars to wait after a loss",
    )
    pause_after_losses: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Pause after this many losses",
    )
    pause_hours: float = Field(
        default=24.0,
        ge=0,
        le=720,
        description="Pause length after those losses (hours)",
    )
    max_spread_atr: float = Field(
        default=0.25,
        gt=0,
        le=5,
        description="Max spread as a share of ATR",
    )
    max_spread_sl: float = Field(
        default=0.15,
        gt=0,
        le=1,
        description="Max spread as a share of the SL",
    )
    check_sessions: bool = Field(default=True, description="Only enter in the strategy's sessions")
    rollover_start: str = Field(
        default="23:00",
        pattern=HHMM,
        description="Rollover starts (server)",
    )
    rollover_end: str = Field(default="01:00", pattern=HHMM, description="Rollover ends (server)")
    friday_close_hours: float = Field(
        default=2.0,
        ge=0,
        le=12,
        description="No entries before the close",
    )
    monday_open_hours: float = Field(
        default=2.0,
        ge=0,
        le=12,
        description="No entries after the open",
    )
    news_minutes: int = Field(
        default=30,
        ge=0,
        le=240,
        description="News blackout before and after",
    )


@dataclass(frozen=True)
class FilterInput:
    signal: Signal
    now: float
    clock: BrokerClock
    atr: float
    spread: float
    probability: ProbabilityEstimate
    expected_value: float | None
    duplicate: bool
    open_positions: int
    losses_in_row: int
    bars_since_loss: int | None
    sessions: Sequence[str]
    events: Sequence[CalendarEvent]
    data_ok: bool
    data_text: str
    trade_mode: SymbolTradeMode
    duplicate_of: str = ""
    hours_since_losses: float | None = None  # since the newest loss of the losing streak


def _minutes(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def _new_york(utc_seconds: float) -> tuple[int, float]:
    offset = -4 if us_dst_active(utc_seconds) else -5
    local = datetime.fromtimestamp(utc_seconds + offset * HOUR, UTC)
    return local.weekday(), local.hour + local.minute / 60.0


def hours_to_weekly_close(utc_seconds: float) -> float | None:
    """Hours until Friday 17:00 New York, on Fridays only."""
    weekday, hour = _new_york(utc_seconds)
    return 17.0 - hour if weekday == 4 and hour < 17 else None


def hours_since_weekly_open(utc_seconds: float) -> float | None:
    """Hours since Sunday 17:00 New York, on Sunday evenings only."""
    weekday, hour = _new_york(utc_seconds)
    return hour - 17.0 if weekday == 6 and hour >= 17 else None


def in_rollover(server_minutes: int, start: str, end: str) -> bool:
    first, last = _minutes(start), _minutes(end)
    if first <= last:
        return first <= server_minutes < last
    return server_minutes >= first or server_minutes < last


def run_filters(data: FilterInput, settings: FilterSettings) -> list[TraceStep]:
    at = data.now
    signal = data.signal
    steps: list[TraceStep] = []

    def add(
        name: str,
        passed: bool | None,
        value: float | int | str | None = None,
        threshold: float | int | str | None = None,
        detail: str = "",
    ) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            value = None
        steps.append(TraceStep("filter", name, passed, value, threshold, detail, at))

    probability = data.probability
    if probability.known and probability.value is not None:
        percent = probability.value * 100
        add(
            "win probability",
            percent >= settings.min_probability_percent,
            round(percent, 1),
            settings.min_probability_percent,
            probability.text(),
        )
    else:
        unknown = "" if settings.require_probability else "; not applied until it is known"
        add(
            "win probability",
            False if settings.require_probability else None,
            None,
            settings.min_probability_percent,
            probability.text() + unknown,
        )
    if data.expected_value is not None and math.isfinite(data.expected_value):
        add(
            "expected value (R)",
            data.expected_value >= settings.min_ev_r,
            round(data.expected_value, 3),
            settings.min_ev_r,
            "probability x reward - (1 - probability) - costs",
        )
    else:
        add(
            "expected value (R)",
            False if settings.require_probability else None,
            None,
            settings.min_ev_r,
            "needs a known probability",
        )
    add(
        "one pending signal per symbol, strategy and side",
        not data.duplicate,
        detail=f"already pending: {data.duplicate_of}" if data.duplicate else "",
    )
    add(
        "no open position of this strategy on the symbol",
        data.open_positions == 0,
        data.open_positions,
        0,
    )
    waited = data.bars_since_loss
    add(
        "cooldown after a loss",
        waited is None or waited >= settings.cooldown_bars_after_loss,
        waited,
        settings.cooldown_bars_after_loss,
        "no recent loss" if waited is None else f"{waited} bars since the last loss",
    )
    since = data.hours_since_losses
    paused = data.losses_in_row >= settings.pause_after_losses
    over = since is not None and since >= settings.pause_hours
    pause_text = f"{data.losses_in_row} losses in a row"
    if paused and since is not None:
        pause_text += f", {since:.1f} h of the {settings.pause_hours:g} h pause"
    add(
        "pause after consecutive losses",
        not paused or over,
        data.losses_in_row,
        settings.pause_after_losses,
        pause_text,
    )
    spread, atr, risk = data.spread, data.atr, signal.risk
    known = math.isfinite(spread) and spread >= 0
    add(
        "spread vs ATR",
        known and atr > 0 and spread / atr <= settings.max_spread_atr,
        round(spread / atr, 3) if known and atr > 0 else None,
        settings.max_spread_atr,
        f"spread {spread:.{signal.digits}f}" if known else "no live spread",
    )
    add(
        "spread vs stop distance",
        known and risk > 0 and spread / risk <= settings.max_spread_sl,
        round(spread / risk, 3) if known and risk > 0 else None,
        settings.max_spread_sl,
    )
    active = [session.value for session in active_sessions(signal.created_at)]
    allowed = list(data.sessions)
    in_session = not settings.check_sessions or not allowed or any(s in active for s in allowed)
    add(
        "trading session",
        in_session,
        " + ".join(active) or "none",
        " or ".join(allowed) or "any",
        "" if settings.check_sessions else "session check is off",
    )
    server = signal.created_at + data.clock.offset_at(signal.created_at) * HOUR
    server_minutes = int(server % 86_400 // 60)
    rollover = in_rollover(server_minutes, settings.rollover_start, settings.rollover_end)
    add(
        "rollover hours",
        not rollover,
        f"{server_minutes // 60:02d}:{server_minutes % 60:02d}",
        f"{settings.rollover_start} to {settings.rollover_end}",
        "server time",
    )
    to_close = hours_to_weekly_close(signal.created_at)
    add(
        "Friday close",
        to_close is None or to_close > settings.friday_close_hours,
        round(to_close, 2) if to_close is not None else None,
        settings.friday_close_hours,
        "hours to the weekly close" if to_close is not None else "not Friday evening",
    )
    since_open = hours_since_weekly_open(signal.created_at)
    add(
        "Monday open",
        since_open is None or since_open >= settings.monday_open_hours,
        round(since_open, 2) if since_open is not None else None,
        settings.monday_open_hours,
        "hours since the weekly open" if since_open is not None else "not the weekly open",
    )
    window = settings.news_minutes * 60
    news = [
        event
        for event in data.events
        if event.impact is Impact.HIGH
        and matches_symbol(event, signal.symbol)
        and abs(event.time - signal.created_at) <= window
    ]
    add(
        "news blackout",
        not news,
        news[0].short(signal.created_at) if news else None,
        f"+-{settings.news_minutes} min",
        "high-impact events for this symbol's currencies",
    )
    add("fresh data", data.data_ok, detail=data.data_text)
    add("market open", market_open(data.now))
    mode = data.trade_mode
    side_ok = (mode is SymbolTradeMode.LONG_ONLY and signal.direction is Direction.LONG) or (
        mode is SymbolTradeMode.SHORT_ONLY and signal.direction is Direction.SHORT
    )
    add(
        "symbol open for new trades",
        mode in (SymbolTradeMode.FULL, SymbolTradeMode.UNKNOWN) or side_ok,
        mode.value,
        "full",
    )
    return steps
