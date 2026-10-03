"""Labels for the model (spec C10): what each historical signal would have done.

Every signal is played forward on M5 bars with the backtest's rules and cost model
(`app/brokers/backtest_broker.py`, `app/backtest/costs.py`): bid bars, the ask is the bid plus
the bar's spread; a market entry fills at the next bar's open plus slippage; a stop order
fills when a bar crosses it (a gap at the open, the worse price) and a limit order at the
better price; an order not filled before the signal expires is dropped (no label). Then:

- **win** when the take profit is reached before the stop loss within `max_bars` bars of the
  signal's timeframe; **loss** when the stop loss is reached first; SL and TP inside the same
  bar count as a loss (spec C8, C10); in the bar an order fills only its stop loss counts;
- **timeout** after `max_bars`: closed at that bar's close and labelled by the sign of R
  (`timeout="sign"`), always as a loss (`"loss"`), or left out (`"skip"`).

The continuous outcome R is the net result of one lot (profit minus the round-turn commission
and the swap of each night) divided by the money the stop loss would lose plus the
commission, the same R the risk manager and the backtest use.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.analysis.bars import TF_SECONDS, Bars
from app.backtest import costs as cost_model
from app.backtest.costs import BacktestCosts
from app.domain.signals import Direction, OrderType, Signal
from app.mt5.models import SymbolSpec

DAY = 86_400
Outcome = Literal["win", "loss", "timeout"]


class LabelSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_bars: int = Field(
        default=48,
        ge=1,
        le=2000,
        description="Bars of the signal's timeframe before a trade counts as timed out",
    )
    timeout: Literal["sign", "loss", "skip"] = Field(
        default="sign",
        description="How a timed-out trade is labelled: by the sign of R, as a loss, or left out",
    )


@dataclass(frozen=True)
class Label:
    signal_id: str
    outcome: Outcome
    win: bool  # the training target
    r: float  # net R of the trade (costs included)
    entry_time: float  # UTC seconds of the fill
    exit_time: float  # UTC seconds the outcome was known (the purge window ends here)
    entry_price: float
    exit_price: float
    bars_held: int


def _nights(open_day: int, close_day: int, triple_weekday: int) -> int:
    """Swap nights between two server days, as the backtest broker charges them."""
    nights = 0
    for day in range(open_day + 1, close_day + 1):
        previous = datetime.fromtimestamp((day - 1) * DAY, UTC).weekday()
        if previous >= 5:
            continue
        nights += 3 if previous == triple_weekday else 1
    return nights


def _spread(bars: Bars, index: int, costs: BacktestCosts, point: float) -> float:
    return costs.spread_points(float(bars.spread[index])) * point


def label_signal(
    signal: Signal,
    m5: Bars,
    spec: SymbolSpec,
    costs: BacktestCosts,
    settings: LabelSettings | None = None,
) -> Label | None:
    """The outcome of `signal`, or None when it never filled or there are no bars after it."""
    rules = settings or LabelSettings()
    point = spec.point if spec.point > 0 else 10.0**-spec.digits
    slip = costs.slippage_points * point
    long = signal.direction is Direction.LONG
    sign = signal.direction.sign
    start = int(np.searchsorted(m5.time, signal.created_at, side="left"))
    if start >= len(m5):
        return None
    horizon = signal.created_at + rules.max_bars * TF_SECONDS[signal.timeframe]
    fill_index = -1
    entry = math.nan
    for index in range(start, len(m5)):
        opened = float(m5.time[index])
        if signal.order_type is OrderType.MARKET and index == start:
            spread = _spread(m5, index, costs, point)
            price = float(m5.open[index]) + (spread if long else 0.0)
            fill_index, entry = index, price + sign * slip
            break
        if opened >= signal.expires_at:
            return None
        spread = _spread(m5, index, costs, point)
        shift = spread if long else 0.0
        bar_open = float(m5.open[index]) + shift
        high = float(m5.high[index]) + shift
        low = float(m5.low[index]) + shift
        if signal.order_type is OrderType.STOP:
            hit = high >= signal.entry if long else low <= signal.entry
            worse = max(bar_open, signal.entry) if long else min(bar_open, signal.entry)
            price = worse + sign * slip
        else:
            hit = low <= signal.entry if long else high >= signal.entry
            price = min(bar_open, signal.entry) if long else max(bar_open, signal.entry)
        if hit:
            fill_index, entry = index, price
            break
    if fill_index < 0:
        return None
    pending = signal.order_type is not OrderType.MARKET
    sl, tp = signal.sl, signal.tp
    exit_price = math.nan
    exit_index = -1
    outcome: Outcome = "timeout"
    for index in range(fill_index, len(m5)):
        just_filled = pending and index == fill_index
        spread = _spread(m5, index, costs, point)
        shift = 0.0 if long else spread  # a buy closes at the bid, a sell at the ask
        bar_open = float(m5.open[index]) + shift
        worst = (float(m5.low[index]) if long else float(m5.high[index])) + shift
        best = (float(m5.high[index]) if long else float(m5.low[index])) + shift
        if index > fill_index or not pending:
            if not just_filled and (bar_open - sl) * sign <= 0:
                exit_price, exit_index = bar_open - sign * slip, index
                outcome = "loss"
                break
            if not just_filled and (bar_open - tp) * sign >= 0:
                exit_price, exit_index = bar_open, index
                outcome = "win"
                break
        if (worst - sl) * sign <= 0:
            exit_price, exit_index = sl - sign * slip, index
            outcome = "loss"
            break
        if not just_filled and (best - tp) * sign >= 0:
            exit_price, exit_index = tp, index
            outcome = "win"
            break
        if float(m5.time[index]) + m5.seconds >= horizon:
            close = float(m5.close[index]) + shift
            exit_price, exit_index = close, index
            break
    if exit_index < 0:
        return None  # the history ends before the outcome is known
    loss = cost_model.profit(spec, costs, signal.direction, 1.0, signal.entry, sl)
    gross = cost_model.profit(spec, costs, signal.direction, 1.0, entry, exit_price)
    if loss is None or gross is None or loss >= 0:
        return None
    server_open = int(m5.server_time[fill_index]) // DAY
    server_close = int(m5.server_time[exit_index]) // DAY
    nights = _nights(server_open, server_close, costs.triple_swap_weekday)
    swap = costs.swap_per_lot(signal.direction) * nights
    net = gross - costs.commission_per_lot + swap
    risk = -loss + costs.commission_per_lot
    r = net / risk
    if outcome == "timeout":
        if rules.timeout == "skip":
            return None
        win = rules.timeout == "sign" and r > 0
    else:
        win = outcome == "win"
    return Label(
        signal_id=signal.id,
        outcome=outcome,
        win=win,
        r=round(r, 4),
        entry_time=float(m5.time[fill_index]),
        exit_time=float(m5.time[exit_index]) + m5.seconds,
        entry_price=entry,
        exit_price=exit_price,
        bars_held=exit_index - fill_index + 1,
    )
