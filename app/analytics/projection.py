"""Risk of ruin and a Monte-Carlo equity projection from the real trades (spec C11).

Each closed trade's result is a return on the balance before it; the projection draws those
returns with replacement for the next N trades and reports the 5th, 50th and 95th percentile
of the balance after each step. The risk of ruin uses the backtest's simulation on the same
returns."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from app.analytics.stats import ordered
from app.analytics.trades import TradeRecord
from app.backtest.monte_carlo import MonteCarloResult, simulate

FloatArray = npt.NDArray[np.float64]
MIN_TRADES = 20


def trade_returns(trades: Sequence[TradeRecord], start_balance: float) -> FloatArray:
    balance = start_balance
    found: list[float] = []
    for trade in ordered(trades):
        found.append(trade.net_profit / balance if balance > 0 else 0.0)
        balance += trade.net_profit
    return np.asarray(found, dtype=np.float64)


@dataclass(frozen=True)
class Projection:
    steps: tuple[int, ...]
    low: tuple[float, ...]  # 5th percentile balance after each step
    median: tuple[float, ...]
    high: tuple[float, ...]  # 95th percentile
    ruin: MonteCarloResult
    note: str


def project(
    trades: Sequence[TradeRecord],
    start_balance: float,
    *,
    horizon: int = 100,
    runs: int = 1000,
    ruin_percent: float = 50.0,
    seed: int = 11,
) -> Projection | None:
    """None without a balance or with fewer than 20 trades."""
    if start_balance <= 0 or len(trades) < MIN_TRADES:
        return None
    returns = trade_returns(trades, start_balance)
    balance = start_balance + sum(t.net_profit for t in trades)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(returns), size=(runs, horizon))
    paths = balance * np.cumprod(1.0 + returns[picks], axis=1)
    low, median, high = (np.percentile(paths, q, axis=0) for q in (5, 50, 95))
    ruin = simulate(returns, runs=runs, method="bootstrap", ruin_percent=ruin_percent, seed=seed)
    note = (
        f"{horizon} more trades drawn from your {len(returns)} closed trades: "
        f"{runs} runs. Past trades do not promise future ones."
    )
    return Projection(
        steps=tuple(range(1, horizon + 1)),
        low=tuple(float(v) for v in low),
        median=tuple(float(v) for v in median),
        high=tuple(float(v) for v in high),
        ruin=ruin,
        note=note,
    )
