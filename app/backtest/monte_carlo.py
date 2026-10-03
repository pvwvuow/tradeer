"""Monte-Carlo robustness (spec C8): the same trades in thousands of other orders.

Each trade's result is a return on the equity it was taken with (the risk manager sizes by a
share of the account), so a simulation compounds the returns in a new order. "shuffle" keeps
every trade once (only the order and so the drawdown change); "bootstrap" draws trades with
replacement (the final result changes too). Reported: the max drawdown distribution, the
final return distribution, and the risk of ruin, the share of runs that lose at least the
ruin level from a peak.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np
import numpy.typing as npt

from app.backtest.engine import BacktestTrade

PERCENTILES = (50, 90, 95, 99)
Method = Literal["shuffle", "bootstrap"]
FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class MonteCarloResult:
    method: str
    runs: int
    trades: int
    ruin_percent: float  # the drawdown that counts as ruin
    risk_of_ruin: float  # 0..1
    drawdown_percentiles: dict[str, float]  # "50" -> max drawdown % (positive)
    return_percentiles: dict[str, float]  # "5", "50", "95" -> final return %
    original_drawdown: float
    worse_than_original: float  # share of runs with a deeper drawdown than the real order
    max_drawdowns: tuple[float, ...] = ()  # every run, for the histogram

    def to_json(self) -> dict[str, Any]:
        found = asdict(self)
        found["max_drawdowns"] = [round(value, 3) for value in self.max_drawdowns]
        return found


def trade_returns(trades: Sequence[BacktestTrade], start_balance: float) -> FloatArray:
    """Each closed trade's net result divided by the balance just before it closed."""
    ordered = sorted(trades, key=lambda t: (t.close_time, t.open_time))
    balance = start_balance
    found: list[float] = []
    for trade in ordered:
        found.append(trade.net_profit / balance if balance > 0 else 0.0)
        balance += trade.net_profit
    return np.asarray(found, dtype=np.float64)


def _max_drawdowns(paths: FloatArray) -> FloatArray:
    """Rows of compounded equity (start 1.0) -> each row's max drawdown in percent."""
    with_start = np.concatenate([np.ones((paths.shape[0], 1)), paths], axis=1)
    peaks = np.maximum.accumulate(with_start, axis=1)
    depth = 1.0 - with_start / peaks
    return np.asarray(depth.max(axis=1) * 100.0, dtype=np.float64)


def simulate(
    returns: FloatArray,
    *,
    runs: int = 1000,
    method: Method = "shuffle",
    ruin_percent: float = 50.0,
    seed: int = 7,
) -> MonteCarloResult:
    count = len(returns)
    if count == 0:
        empty = {str(p): 0.0 for p in PERCENTILES}
        return MonteCarloResult(method, 0, 0, ruin_percent, 0.0, empty, empty, 0.0, 0.0)
    rng = np.random.default_rng(seed)
    if method == "bootstrap":
        picks = rng.integers(0, count, size=(runs, count))
    else:
        picks = np.argsort(rng.random((runs, count)), axis=1)
    paths = np.cumprod(1.0 + returns[picks], axis=1)
    drawdowns = _max_drawdowns(paths)
    finals = (paths[:, -1] - 1.0) * 100.0
    original = float(_max_drawdowns(np.cumprod(1.0 + returns)[None, :])[0])
    return MonteCarloResult(
        method=method,
        runs=runs,
        trades=count,
        ruin_percent=ruin_percent,
        risk_of_ruin=float(np.mean(drawdowns >= ruin_percent)),
        drawdown_percentiles={str(p): float(np.percentile(drawdowns, p)) for p in PERCENTILES},
        return_percentiles={str(p): float(np.percentile(finals, p)) for p in (5, 50, 95)},
        original_drawdown=original,
        worse_than_original=float(np.mean(drawdowns > original + 1e-9)),
        max_drawdowns=tuple(float(value) for value in drawdowns),
    )


def monte_carlo(
    trades: Sequence[BacktestTrade],
    start_balance: float,
    **options: Any,
) -> MonteCarloResult:
    return simulate(trade_returns(trades, start_balance), **options)


def summary_lines(result: MonteCarloResult) -> list[str]:
    if not result.trades:
        return ["Monte-Carlo: no trades to reshuffle"]
    dd = result.drawdown_percentiles
    return [
        f"Monte-Carlo ({result.method}, {result.runs} runs of {result.trades} trades): max "
        f"drawdown median {dd['50']:.1f}%, 95% of runs below {dd['95']:.1f}%",
        f"Real order drawdown {result.original_drawdown:.1f}%; "
        f"{result.worse_than_original * 100:.0f}% of runs were worse",
        f"Risk of ruin (a {result.ruin_percent:.0f}% drawdown): {result.risk_of_ruin * 100:.1f}%",
    ]
