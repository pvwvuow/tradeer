"""Strategy 7: trendline break on M5 (23 October 2026), the owner's own method.

The owner trades NAS100 and US30 by hand: when the price breaks the trendline of the move
that came before, he enters at once in the direction of the break; the stop goes behind the
last low (high) before the line. Then, with the trend, every correction that draws a small
trendline and breaks it is another entry, the stop behind the low (high) of that correction.
He takes half at 2R, moves the rest to break-even, and closes it when the trend turns. He
does not trade news, a wide spread or a range. He estimates a win rate near 50%. Not proven:
backtest it on both symbols with the real commission before it trades anything.

Rules, long (short mirrored), on the newest closed M5 bar:
- only the symbols in `symbols` (US30 and NAS100 and their usual broker names);
- the two latest confirmed swing highs fall (A then B, B lower), `min_span_bars` to
  `max_span_bars` bars apart: the line through them is the trendline;
- this bar closes above the line and the bar before did not, and no close since B was above
  it (a fresh break), within `max_age_bars` bars of B;
- the first entry (the structure's last break is not up) and the pullback entry (the last
  break is up: the line is a correction inside an uptrend) are both allowed unless turned off;
- the stop is the lowest low since A (the end of the move that the line contained) minus
  `sl_buffer_atr` ATR; it must be `min_risk_atr` to `max_risk_atr` ATR away;
- not a range (the last `range_bars` bars span at least `min_range_atr` ATR), no high impact
  news close by, and the spread is at most `max_spread_risk` of the stop distance.

There is no limit on open trades: the strategy is `stackable`, so every new break is taken
while earlier ones are open (the risk settings still cap them). Its trades are judged by
R, not by a win probability gate (`judged_by_win_rate` is False).

The exit is the management (`DEFAULT_MANAGEMENT` in app/core/execution_settings.py): half
closed at 2R and the stop of the rest to the entry. The rest is also closed when the trend
turns (`exit_check`, version 1.1.0): a close beyond the last higher low (lower high) that
formed after the entry, or a fresh break of the opposite trendline. The far take profit
(`reward_r`) only ends a run that never turns.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.analysis import indicators
from app.analysis.bars import Bars
from app.analysis.structure import Swing, SwingKind, Trend, analyze_structure, find_swings
from app.calendar.models import Impact, matches_symbol
from app.domain.signals import Direction, OrderType, Signal
from app.strategies.base import Condition, Evaluation, Held, SetupState, Strategy
from app.strategies.context import MarketContext

NEAR_ATR = 0.5  # a close this close to the line counts as forming
VIEW_BARS = 400  # the newest bars the swings are looked for in
MIN_EXIT_BARS = 20  # fewest bars the exit check looks at
SYMBOLS = "US30,NAS100,US100,USTEC,DJ30,WS30"


class TrendlineBreakParams(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    symbols: str = Field(
        default=SYMBOLS,
        description="Symbols it trades, by name start, comma separated (empty: all)",
    )
    swing_strength: int = Field(default=3, ge=2, le=10, description="Bars that confirm a swing")
    min_span_bars: int = Field(default=8, ge=3, le=200, description="Shortest line, in bars")
    max_span_bars: int = Field(default=120, ge=10, le=400, description="Longest line, in bars")
    max_age_bars: int = Field(default=36, ge=2, le=200, description="Bars after B to break")
    sl_buffer_atr: float = Field(default=0.1, ge=0, le=2, description="Stop buffer, ATR")
    min_risk_atr: float = Field(default=0.5, gt=0, le=10, description="Smallest stop, ATR")
    max_risk_atr: float = Field(default=4.0, gt=0, le=20, description="Widest stop, ATR")
    reward_r: float = Field(default=5.0, ge=2, le=20, description="Far take profit in R")
    range_bars: int = Field(default=48, ge=10, le=300, description="Bars of the range check")
    min_range_atr: float = Field(default=4.0, ge=0, le=50, description="Narrowest move, ATR")
    max_spread_risk: float = Field(default=0.15, gt=0, le=1, description="Spread / stop")
    allow_first: bool = Field(default=True, description="Trade the first break of a move")
    allow_pullback: bool = Field(default=True, description="Trade breaks of corrections")
    news_before_min: int = Field(default=30, ge=0, le=240, description="No entry before news")
    news_after_min: int = Field(default=15, ge=0, le=240, description="No entry after news")
    news_currency: str = Field(default="USD", description="Currency of the news to avoid")
    expiry_bars: int = Field(default=2, ge=1, le=20, description="Bars until a signal expires")
    exit_on_trend_change: bool = Field(default=True, description="Close when the trend turns")
    exit_swing_strength: int = Field(
        default=6,
        ge=2,
        le=20,
        description="Bars that confirm the higher low (lower high) the exit watches",
    )


@dataclass(frozen=True)
class Line:
    """A trendline through two swings of one kind: A (older) and B (newer)."""

    a_index: int
    b_index: int
    a_price: float
    b_price: float

    @property
    def slope(self) -> float:
        return (self.b_price - self.a_price) / (self.b_index - self.a_index)

    def at(self, index: int) -> float:
        return self.b_price + self.slope * (index - self.b_index)


@dataclass(frozen=True)
class Cut:
    """Where the newest closed bar stands against a trendline."""

    broke: bool  # a fresh close beyond the line: not the bar before, nor any bar since B
    beyond: float  # the close beyond the line, in price, in the direction asked
    level: float  # the line at the newest bar
    age: int  # bars after B


def find_line(
    view_swings: list[Swing],
    direction: Direction,
    min_span: int,
    max_span: int,
) -> Line | None:
    """The line through the two latest swing highs (to buy) or lows (to sell) when it runs
    against the trade: falling highs, rising lows."""
    kind = SwingKind.HIGH if direction is Direction.LONG else SwingKind.LOW
    points = [swing for swing in view_swings if swing.kind is kind]
    if len(points) < 2:
        return None
    a, b = points[-2], points[-1]
    if (b.price - a.price) * direction.sign >= 0:  # buying needs falling highs: b < a
        return None
    if not min_span <= b.index - a.index <= max_span:
        return None
    return Line(a.index, b.index, a.price, b.price)


def fresh_break(bars: Bars, line: Line, sign: float) -> Cut:
    """Did the newest close cut the line for the first time, going the way of `sign`?"""
    last = len(bars) - 1
    close, before = float(bars.close[last]), float(bars.close[last - 1])
    level, level_before = line.at(last), line.at(last - 1)
    beyond = (close - level) * sign
    fresh_cut = all(
        (float(bars.close[i]) - line.at(i)) * sign <= 0 for i in range(line.b_index + 1, last)
    )
    broke = beyond > 0 and (before - level_before) * sign <= 0 and fresh_cut
    return Cut(broke, beyond, level, last - line.b_index)


def symbol_allowed(symbol: str, allowed: str) -> bool:
    """Is the symbol one of the comma separated name starts ("US30" allows "US30.p")?"""
    wanted = [part.strip().upper() for part in allowed.split(",") if part.strip()]
    if not wanted:
        return True
    name = symbol.upper()
    return any(name.startswith(start) for start in wanted)


def news_block(ctx: MarketContext, cfg: TrendlineBreakParams) -> str:
    """The title of a high impact event too close to trade, "" when there is none."""
    before, after = cfg.news_before_min * 60, cfg.news_after_min * 60
    for event in ctx.events:
        if event.impact is not Impact.HIGH:
            continue
        same = event.currency.upper() == cfg.news_currency.upper()
        if not same and not matches_symbol(event, ctx.symbol):
            continue
        if ctx.now - after <= event.time <= ctx.now + before:
            return event.title
    return ""


class TrendlineBreak(Strategy):
    name: ClassVar[str] = "trendline_break"
    version: ClassVar[str] = "1.1.0"
    title: ClassVar[str] = "Trendline break (M5)"
    description: ClassVar[str] = (
        "For NAS100 and US30. Enters when an M5 close breaks the trendline through the last "
        "two swing highs (lows), first break or a correction inside the trend, and again on "
        "every later correction (no limit on open trades). Stop behind the move's extreme, "
        "half out at 2R, the rest to break-even and closed when the trend turns. Not in "
        "news, a range or a wide spread."
    )
    params_model: ClassVar[type[BaseModel]] = TrendlineBreakParams
    entry_timeframe: ClassVar[str] = "M5"
    required_history: ClassVar[dict[str, int]] = {"M5": 200}
    sessions: ClassVar[tuple[str, ...]] = ("London", "New York")
    example: ClassVar[bool] = False
    stackable: ClassVar[bool] = True
    judged_by_win_rate: ClassVar[bool] = False

    def __init__(self, params: TrendlineBreakParams | None = None) -> None:
        self.cfg = params if params is not None else TrendlineBreakParams()
        super().__init__(self.cfg)

    def evaluate(self, ctx: MarketContext) -> Evaluation:
        cfg = self.cfg
        history = self.history_condition(ctx)
        if not history.passed:
            return self.result(ctx, SetupState.NONE, [history], note="waiting for history")
        listed = Condition(
            f"symbol is one of {cfg.symbols or 'any'}",
            symbol_allowed(ctx.symbol, cfg.symbols),
            detail=ctx.symbol,
        )
        if not listed.passed:
            return self.result(ctx, SetupState.NONE, [history, listed], note="symbol not traded")
        view = ctx.entry.tail(VIEW_BARS)
        atr = indicators.last(indicators.atr(view.high, view.low, view.close))
        if not (math.isfinite(atr) and atr > 0):
            missing = Condition("M5 ATR", False, detail="not enough M5 bars")
            return self.result(ctx, SetupState.NONE, [history, listed, missing])
        span = float(view.high[-cfg.range_bars :].max() - view.low[-cfg.range_bars :].min())
        moved = span / atr
        trending = Condition(
            f"not a range: {cfg.range_bars} bars span {cfg.min_range_atr:g} ATR",
            moved >= cfg.min_range_atr,
            value=round(moved, 2),
            threshold=cfg.min_range_atr,
        )
        title = news_block(ctx, cfg)
        calm = Condition("no high impact news close", not title, detail=title or "clear")
        conditions = [history, listed, trending, calm]
        if not (trending.passed and calm.passed):
            return self.result(ctx, SetupState.NONE, conditions, note="range or news")
        swings = find_swings(view, cfg.swing_strength)
        structure = analyze_structure(view, cfg.swing_strength)
        best: tuple[SetupState, list[Condition], Signal | None, str] | None = None
        for direction in (Direction.LONG, Direction.SHORT):
            state, found, signal, note = self._side(
                ctx, cfg, view, swings, structure.bias, direction, atr
            )
            rank = (state.rank, signal is not None)
            if best is None or rank > (best[0].rank, best[2] is not None):
                best = (state, found, signal, note)
        assert best is not None
        state, found, signal, note = best
        conditions += found
        signals = [signal] if signal is not None else []
        return self.result(ctx, state, conditions, signals, note=note)

    def exit_check(self, ctx: MarketContext, held: Sequence[Held]) -> Mapping[str, str]:
        """The held positions whose trend turned: the newest close is beyond the last higher
        low (lower high) that formed after the entry, or it broke the opposite trendline.
        Runs on every new bar, also in a range or during news."""
        cfg = self.cfg
        if not cfg.exit_on_trend_change or not held:
            return {}
        view = ctx.entry.tail(VIEW_BARS)
        if len(view) < MIN_EXIT_BARS:
            return {}
        swings = find_swings(view, cfg.swing_strength)
        steady = find_swings(view, cfg.exit_swing_strength)
        found: dict[str, str] = {}
        for item in held:
            why = self._turned(cfg, view, swings, steady, item)
            if why:
                found[item.id] = why
        return found

    def _turned(
        self,
        cfg: TrendlineBreakParams,
        bars: Bars,
        swings: list[Swing],
        steady: list[Swing],
        item: Held,
    ) -> str:
        last = len(bars) - 1
        entry = int(np.searchsorted(bars.time, item.since))
        if entry >= last:
            return ""
        sign = item.direction.sign
        close = float(bars.close[last])
        kind = SwingKind.LOW if sign > 0 else SwingKind.HIGH
        after = [
            swing
            for swing in steady
            if swing.kind is kind and swing.index > entry and swing.confirmed_index <= last
        ]
        if after:
            level = after[-1].price
            if (close - level) * sign < 0:
                word = "higher low" if sign > 0 else "lower high"
                return f"trend turned: closed beyond the {word} {level:.5g} formed after the entry"
        opposite = Direction.SHORT if sign > 0 else Direction.LONG
        line = find_line(swings, opposite, cfg.min_span_bars, cfg.max_span_bars)
        if line is not None:
            cut = fresh_break(bars, line, opposite.sign)
            if cut.broke and cut.age <= cfg.max_age_bars:
                return f"trend turned: broke the opposite trendline ({cut.level:.5g})"
        return ""

    def _side(
        self,
        ctx: MarketContext,
        cfg: TrendlineBreakParams,
        bars: Bars,
        swings: list[Swing],
        bias: Trend | None,
        direction: Direction,
        atr: float,
    ) -> tuple[SetupState, list[Condition], Signal | None, str]:
        sign = direction.sign
        word = "falling highs" if sign > 0 else "rising lows"
        line = find_line(swings, direction, cfg.min_span_bars, cfg.max_span_bars)
        found = [
            Condition(
                f"{direction.value}: trendline through two {word}",
                line is not None,
                detail="no line" if line is None else f"slope {line.slope:+.5g} per bar",
            ),
        ]
        if line is None:
            return SetupState.NONE, found, None, "no trendline"
        close = float(bars.close[len(bars) - 1])
        cut = fresh_break(bars, line, sign)
        level, beyond, age, broke = cut.level, cut.beyond, cut.age, cut.broke
        found.append(
            Condition(
                f"{direction.value}: fresh close beyond the line within {cfg.max_age_bars} bars",
                broke and age <= cfg.max_age_bars,
                value=round(beyond / atr, 2),
                threshold=0.0,
                detail=f"line {level:.{ctx.digits}f}, {age} bars after B",
            ),
        )
        if not (broke and age <= cfg.max_age_bars):
            near = abs(close - level) <= NEAR_ATR * atr and age <= cfg.max_age_bars
            state = SetupState.FORMING if near else SetupState.NONE
            return state, found, None, "no break"
        with_trend = bias is (Trend.UP if sign > 0 else Trend.DOWN)
        kind = "pullback" if with_trend else "first"
        allowed = cfg.allow_pullback if with_trend else cfg.allow_first
        found.append(Condition(f"{direction.value}: {kind} entry allowed", allowed))
        if not allowed:
            return SetupState.NONE, found, None, f"{kind} entries are off"
        if sign > 0:
            extreme = float(bars.low[line.a_index :].min())
            stop = extreme - cfg.sl_buffer_atr * atr
        else:
            extreme = float(bars.high[line.a_index :].max())
            stop = extreme + cfg.sl_buffer_atr * atr
        risk = (close - stop) * sign
        sized = cfg.min_risk_atr * atr <= risk <= cfg.max_risk_atr * atr
        found.append(
            Condition(
                f"{direction.value}: stop {cfg.min_risk_atr:g} to {cfg.max_risk_atr:g} ATR away",
                sized,
                value=round(risk / atr, 2),
                detail=f"stop {stop:.{ctx.digits}f}",
            ),
        )
        if not sized:
            return SetupState.NONE, found, None, "stop too small or too wide"
        cheap = not math.isfinite(ctx.spread) or ctx.spread <= cfg.max_spread_risk * risk
        found.append(
            Condition(
                f"spread at most {cfg.max_spread_risk:.0%} of the stop",
                cheap,
                value=None if not math.isfinite(ctx.spread) else round(ctx.spread / risk, 3),
                threshold=cfg.max_spread_risk,
            ),
        )
        if not cheap:
            return SetupState.NONE, found, None, "spread too wide"
        reason = (
            f"M5 close {close:.{ctx.digits}f} broke the trendline through two {word} "
            f"({kind} entry, line {level:.{ctx.digits}f}); stop behind {stop:.{ctx.digits}f}, "
            "half out at 2R, the rest to break-even"
        )
        signal = self.make_signal(
            ctx,
            direction,
            OrderType.MARKET,
            entry=close,
            sl=stop,
            tp=close + sign * cfg.reward_r * risk,
            reason=reason,
            expires_at=self.bars_later(ctx, cfg.expiry_bars),
            features={
                "kind": kind,
                "risk_atr": round(risk / atr, 3),
                "line_age_bars": float(age),
                "slope_atr_per_bar": round(line.slope / atr, 4),
            },
        )
        return SetupState.READY, found, signal, "signal"
