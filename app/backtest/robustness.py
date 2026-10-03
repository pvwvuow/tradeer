"""Shared pieces of walk-forward and sensitivity tests: running one parameter set and scoring
its result."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from pydantic import ValidationError

from app.backtest.engine import BacktestResult, BacktestSetup, History, run_backtest
from app.backtest.metrics import Metrics, compute_metrics
from app.strategies.base import Strategy
from app.strategies.registry import create_strategy

# One replay: (strategies, start, end) -> result. Tests pass a fast fake.
Runner = Callable[[Sequence[Strategy], float, float], BacktestResult]
Cancelled = Callable[[], bool]

OBJECTIVES: dict[str, str] = {
    "expectancy_r": "Expectancy (R per trade)",
    "profit_factor": "Profit factor",
    "net_profit": "Net profit",
    "win_rate": "Win rate",
    "sharpe": "Sharpe ratio",
}


def history_runner(
    history: History,
    base: BacktestSetup,
    cancelled: Cancelled | None = None,
) -> Runner:
    """Runs `base` with other strategies and another period on `history`."""

    def run(strategies: Sequence[Strategy], start: float, end: float) -> BacktestResult:
        setup = replace(base, strategies=tuple(strategies), start=start, end=end)
        return run_backtest(history, setup, cancelled=cancelled)

    return run


def build(name: str, params: Mapping[str, Any]) -> Strategy | None:
    """The strategy with these params, or None when the combination is invalid."""
    try:
        return create_strategy(name, params)
    except (KeyError, ValidationError, ValueError):
        return None


def score(metrics: Metrics, objective: str, minimum_trades: int) -> float:
    """Higher is better; -inf when there are too few trades to judge."""
    if metrics.trades < minimum_trades:
        return -math.inf
    value = getattr(metrics, objective)
    if value is None:
        # A profit factor without losses is the best possible; a missing Sharpe is unknown.
        return math.inf if objective == "profit_factor" and metrics.net_profit > 0 else -math.inf
    return float(value)


def trade_metrics(result: BacktestResult) -> Metrics:
    return compute_metrics(result.trades, result.times, result.equity, result.start_balance)


def finite(value: float) -> float | None:
    return value if math.isfinite(value) else None
