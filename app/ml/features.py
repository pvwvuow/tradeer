"""Model features (spec C10): only what is known when the signal is made.

Every feature comes from the strategy's `MarketContext` (closed bars, levels, structure,
volatility, calendar) and the signal itself, so live trading and the backtest replay compute
the same numbers (the replay runs this same code). Prices are normalized by the entry
timeframe's ATR and signed in the trade's direction ("aligned"), so one model can learn from
buys and sells on several symbols. A value that cannot be computed is NaN, which the model
handles as "missing".

The list is versioned: `schema_hash()` changes whenever a name, the order or
`FEATURE_VERSION` changes, and the registry refuses a model trained on another schema.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import numpy.typing as npt

from app.analysis import indicators
from app.analysis.bars import Bars
from app.analysis.sessions import Session, active_sessions
from app.analysis.structure import Trend
from app.calendar.models import Impact, matches_symbol
from app.domain.signals import OrderType, Signal
from app.strategies.context import MarketContext

FEATURE_VERSION = 1
PREFIX = "ml."  # the key prefix inside `Signal.features`
STRATEGY_NAMES: tuple[str, ...] = ("trend_pullback", "london_breakout")
NEWS_CAP_MINUTES = 1440.0
FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Feature:
    name: str
    meaning: str  # plain words, used by the explanations
    high: str  # what a high value means, e.g. "strong trend"
    low: str


FEATURES: tuple[Feature, ...] = (
    Feature("direction", "buy (+1) or sell (-1)", "a buy", "a sell"),
    Feature("rr", "reward to risk", "a far target", "a near target"),
    Feature("sl_atr", "stop distance in ATR", "a wide stop", "a tight stop"),
    Feature("spread_atr", "spread in ATR", "a wide spread", "a narrow spread"),
    Feature(
        "ema20_dist_atr",
        "price vs EMA20 (trade side)",
        "price far past EMA20",
        "price at EMA20",
    ),
    Feature(
        "ema50_dist_atr",
        "price vs EMA50 (trade side)",
        "price far past EMA50",
        "price at EMA50",
    ),
    Feature(
        "ema200_dist_atr",
        "price vs EMA200 (trade side)",
        "price far past EMA200",
        "price at EMA200",
    ),
    Feature(
        "ema20_slope_atr",
        "EMA20 slope (trade side)",
        "momentum with the trade",
        "momentum against the trade",
    ),
    Feature(
        "rsi_aligned",
        "RSI minus 50 (trade side)",
        "RSI with the trade",
        "RSI against the trade",
    ),
    Feature("adx", "ADX", "a strong trend", "a weak trend"),
    Feature("atr_percentile", "daily ATR percentile", "high volatility", "low volatility"),
    Feature("bb_width_atr", "Bollinger width in ATR", "a wide range", "a tight range"),
    Feature("body_ratio", "candle body share", "a full-bodied candle", "a small-bodied candle"),
    Feature(
        "close_position",
        "close inside the candle (trade side)",
        "a close at the candle's end",
        "a close against the trade",
    ),
    Feature(
        "htf_alignment",
        "higher timeframes on the trade side",
        "higher timeframes agree",
        "higher timeframes disagree",
    ),
    Feature(
        "structure_h1",
        "H1 structure (trade side)",
        "H1 structure agrees",
        "H1 structure disagrees",
    ),
    Feature(
        "structure_h4",
        "H4 structure (trade side)",
        "H4 structure agrees",
        "H4 structure disagrees",
    ),
    Feature(
        "event_h1",
        "last H1 break (trade side)",
        "a fresh H1 break with the trade",
        "a fresh H1 break against the trade",
    ),
    Feature(
        "target_level_atr",
        "room to the next level in ATR",
        "open room to the target",
        "a level close ahead",
    ),
    Feature("stop_level_atr", "support behind in ATR", "no level behind", "a level just behind"),
    Feature(
        "adr_used",
        "share of the daily range used",
        "most of the day's range used",
        "little of the day's range used",
    ),
    Feature("news_minutes", "minutes to high-impact news", "no news soon", "news soon"),
    Feature("session_asia", "Asia session", "the Asia session", "outside Asia"),
    Feature("session_london", "London session", "the London session", "outside London"),
    Feature("session_new_york", "New York session", "the New York session", "outside New York"),
    Feature("hour_sin", "hour of day (sine)", "this time of day", "this time of day"),
    Feature("hour_cos", "hour of day (cosine)", "this time of day", "this time of day"),
    Feature("weekday_sin", "weekday (sine)", "this day of the week", "this day of the week"),
    Feature("weekday_cos", "weekday (cosine)", "this day of the week", "this day of the week"),
    Feature(
        "strategy_trend_pullback",
        "trend pullback strategy",
        "the trend pullback",
        "another strategy",
    ),
    Feature(
        "strategy_london_breakout",
        "London breakout strategy",
        "the London breakout",
        "another strategy",
    ),
    Feature("order_market", "market order (1) or pending (0)", "a market entry", "a pending entry"),
)
FEATURE_NAMES: tuple[str, ...] = tuple(feature.name for feature in FEATURES)
BY_NAME: dict[str, Feature] = {feature.name: feature for feature in FEATURES}


def schema_hash(names: Sequence[str] = FEATURE_NAMES, version: int = FEATURE_VERSION) -> str:
    text = f"v{version}:" + ",".join(names)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _finite(value: float) -> float:
    return float(value) if math.isfinite(value) else math.nan


def _ema_distance(close: FloatArray, period: int, atr: float, sign: int) -> float:
    if len(close) < period or not atr > 0:
        return math.nan
    line = indicators.ema(close, period)
    return _finite((close[-1] - line[-1]) / atr * sign)


def _alignment(bars: Mapping[str, Bars], sign: int) -> float:
    votes: list[float] = []
    for timeframe in ("H1", "H4", "D1"):
        series = bars.get(timeframe)
        if series is None or len(series) < 50:
            continue
        line = indicators.ema(series.close, 50)
        if math.isfinite(line[-1]):
            votes.append(float(np.sign(series.close[-1] - line[-1])) * sign)
    return sum(votes) / len(votes) if votes else math.nan


def _trend_vote(trend: Trend | None, sign: int) -> float:
    if trend is None:
        return math.nan
    if trend is Trend.RANGE:
        return 0.0
    return float(sign if trend is Trend.UP else -sign)


def _news_minutes(ctx: MarketContext, now: float) -> float:
    ahead = [
        event.time
        for event in ctx.events
        if event.impact is Impact.HIGH and event.time >= now and matches_symbol(event, ctx.symbol)
    ]
    if not ahead:
        return NEWS_CAP_MINUTES
    return min((min(ahead) - now) / 60.0, NEWS_CAP_MINUTES)


def compute(signal: Signal, ctx: MarketContext) -> dict[str, float]:
    """Every feature of `FEATURE_NAMES` for one signal (NaN when unknown)."""
    sign = signal.direction.sign
    entry = ctx.entry
    found: dict[str, float] = dict.fromkeys(FEATURE_NAMES, math.nan)
    found["direction"] = float(sign)
    found["rr"] = _finite(signal.rr)
    found["order_market"] = 1.0 if signal.order_type is OrderType.MARKET else 0.0
    for name in STRATEGY_NAMES:
        found[f"strategy_{name}"] = 1.0 if signal.strategy == name else 0.0
    moment = datetime.fromtimestamp(signal.created_at, UTC)
    hour = moment.hour + moment.minute / 60.0
    found["hour_sin"] = math.sin(2 * math.pi * hour / 24.0)
    found["hour_cos"] = math.cos(2 * math.pi * hour / 24.0)
    found["weekday_sin"] = math.sin(2 * math.pi * moment.weekday() / 7.0)
    found["weekday_cos"] = math.cos(2 * math.pi * moment.weekday() / 7.0)
    sessions = active_sessions(signal.created_at)
    found["session_asia"] = 1.0 if Session.ASIA in sessions else 0.0
    found["session_london"] = 1.0 if Session.LONDON in sessions else 0.0
    found["session_new_york"] = 1.0 if Session.NEW_YORK in sessions else 0.0
    found["news_minutes"] = _news_minutes(ctx, signal.created_at)
    found["htf_alignment"] = _alignment(ctx.bars, sign)
    structures = ctx.structures
    h1 = structures.get("H1")
    h4 = structures.get("H4")
    found["structure_h1"] = _trend_vote(h1.trend if h1 is not None else None, sign)
    found["structure_h4"] = _trend_vote(h4.trend if h4 is not None else None, sign)
    event = h1.last_event if h1 is not None else None
    found["event_h1"] = _trend_vote(event.direction, sign) if event is not None else 0.0
    volatility = ctx.volatility
    if volatility is not None:
        found["atr_percentile"] = _finite(volatility.atr_percentile / 100.0)
        found["adr_used"] = _finite(volatility.adr_used_percent / 100.0)
    levels = ctx.levels
    if levels is not None:
        ahead = levels.nearest_resistance if sign > 0 else levels.nearest_support
        behind = levels.nearest_support if sign > 0 else levels.nearest_resistance
        found["target_level_atr"] = abs(ahead.distance_atr) if ahead is not None else 10.0
        found["stop_level_atr"] = abs(behind.distance_atr) if behind is not None else 10.0
    if len(entry) < 30:
        return found
    close = entry.close
    atr = indicators.last(indicators.atr(entry.high, entry.low, close, 14))
    if not atr > 0:
        return found
    found["sl_atr"] = _finite(signal.risk / atr)
    if math.isfinite(ctx.spread):
        found["spread_atr"] = _finite(ctx.spread / atr)
    found["ema20_dist_atr"] = _ema_distance(close, 20, atr, sign)
    found["ema50_dist_atr"] = _ema_distance(close, 50, atr, sign)
    found["ema200_dist_atr"] = _ema_distance(close, 200, atr, sign)
    line = indicators.ema(close, 20)
    if len(line) > 5 and math.isfinite(line[-6]):
        found["ema20_slope_atr"] = _finite((line[-1] - line[-6]) / atr * sign)
    found["rsi_aligned"] = _finite((indicators.last(indicators.rsi(close, 14)) - 50.0) * sign)
    found["adx"] = _finite(indicators.last(indicators.adx(entry.high, entry.low, close, 14)[0]))
    window = close[-20:]
    found["bb_width_atr"] = _finite(4.0 * float(np.std(window)) / atr)
    high, low, opened = float(entry.high[-1]), float(entry.low[-1]), float(entry.open[-1])
    span = high - low
    if span > 0:
        last_close = float(close[-1])
        found["body_ratio"] = abs(last_close - opened) / span
        position = (last_close - low) / span if sign > 0 else (high - last_close) / span
        found["close_position"] = position
    return found


def stored(values: Mapping[str, float]) -> dict[str, float | str]:
    """The features as `Signal.features` entries (rounded, NaN kept out)."""
    return {PREFIX + key: round(value, 5) for key, value in values.items() if math.isfinite(value)}


def from_signal(features: Mapping[str, float | str]) -> dict[str, float]:
    """The model features saved in a signal (missing ones are NaN)."""
    found = dict.fromkeys(FEATURE_NAMES, math.nan)
    for name in FEATURE_NAMES:
        value = features.get(PREFIX + name)
        if isinstance(value, int | float) and math.isfinite(float(value)):
            found[name] = float(value)
    return found


def vector(values: Mapping[str, float], names: Sequence[str] = FEATURE_NAMES) -> FloatArray:
    return np.asarray([values.get(name, math.nan) for name in names], dtype=np.float64)


def has_features(features: Mapping[str, float | str]) -> bool:
    return any(key.startswith(PREFIX) for key in features)
