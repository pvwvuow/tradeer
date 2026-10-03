"""Walk-forward analysis (spec C8): choose the parameters on a past window (in-sample), then
trade them on the next, unseen window (out-of-sample); roll forward and repeat.

Only the out-of-sample trades count: they were made with parameters chosen without seeing
them, which is as close to live trading as a backtest gets. The Go-Live gate (spec C9) asks
for at least 100 such trades with a positive expectancy.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from app.backtest.engine import BacktestTrade
from app.backtest.metrics import MIN_TRADES, Metrics, compute_metrics
from app.backtest.robustness import Cancelled, Runner, build, finite, score, trade_metrics
from app.strategies.base import Strategy

DAY = 86_400
Progress = Callable[[int, int], None]


@dataclass(frozen=True)
class Window:
    in_start: float
    in_end: float
    out_start: float
    out_end: float


@dataclass(frozen=True)
class Fold:
    window: Window
    params: Mapping[str, Any]
    in_score: float | None
    in_trades: int
    out_trades: int
    out_expectancy_r: float | None
    out_net: float
    note: str = ""


@dataclass(frozen=True)
class WalkForwardResult:
    strategy: str
    objective: str
    folds: tuple[Fold, ...]
    trades: tuple[BacktestTrade, ...]  # every out-of-sample trade
    metrics: Metrics
    minimum_trades: int
    cancelled: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        """The Go-Live rule: enough out-of-sample trades with a positive expectancy."""
        expectancy = self.metrics.expectancy_r
        return (
            self.metrics.trades >= self.minimum_trades and expectancy is not None and expectancy > 0
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "objective": self.objective,
            "passed": self.passed,
            "minimum_trades": self.minimum_trades,
            "cancelled": self.cancelled,
            "notes": list(self.notes),
            "folds": [asdict(fold) for fold in self.folds],
            "out_of_sample": self.metrics.to_json(),
        }


def windows(start: float, end: float, in_days: float, out_days: float) -> list[Window]:
    """Rolling windows: in-sample `in_days`, then out-of-sample `out_days`, step `out_days`."""
    if in_days <= 0 or out_days <= 0:
        raise ValueError("the in-sample and out-of-sample lengths must be positive")
    found: list[Window] = []
    cursor = start
    while cursor + (in_days + out_days) * DAY <= end + 1e-6:
        in_end = cursor + in_days * DAY
        found.append(Window(cursor, in_end, in_end, in_end + out_days * DAY))
        cursor += out_days * DAY
    return found


def _day(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d")


def walk_forward(
    name: str,
    candidates: Sequence[Mapping[str, Any]],
    runner: Runner,
    start: float,
    end: float,
    *,
    in_days: float,
    out_days: float,
    objective: str = "expectancy_r",
    minimum_in_trades: int = 10,
    minimum_trades: int = MIN_TRADES,
    start_balance: float = 10_000.0,
    others: Sequence[Strategy] = (),
    progress: Progress | None = None,
    cancelled: Cancelled | None = None,
) -> WalkForwardResult:
    if not candidates:
        raise ValueError("give at least one parameter set")
    plan = windows(start, end, in_days, out_days)
    if not plan:
        raise ValueError("the period is shorter than one in-sample plus out-of-sample window")
    built = [(params, build(name, params)) for params in candidates]
    valid = [(params, strategy) for params, strategy in built if strategy is not None]
    if not valid:
        raise ValueError(f"no valid parameter set for {name}")
    notes: list[str] = []
    if len(valid) < len(built):
        notes.append(f"{len(built) - len(valid)} invalid parameter set(s) skipped")
    total = len(plan) * (len(valid) + 1)
    done = 0
    folds: list[Fold] = []
    trades: list[BacktestTrade] = []
    stopped = False
    for window in plan:
        best: tuple[float, Mapping[str, Any], Strategy, int] | None = None
        for params, strategy in valid:
            if cancelled is not None and cancelled():
                stopped = True
                break
            result = runner([strategy, *others], window.in_start, window.in_end)
            metrics = trade_metrics(result)
            mine = sum(1 for t in result.trades if t.strategy == name)
            value = score(metrics, objective, minimum_in_trades)
            if best is None or value > best[0]:
                best = (value, params, strategy, mine)
            done += 1
            if progress is not None:
                progress(done, total)
        if stopped or best is None:
            stopped = True
            break
        value, params, strategy, in_trades = best
        note = ""
        if not (math.isfinite(value) or value > 0):
            note = f"no parameter set had {minimum_in_trades} in-sample trades; the first was used"
        result = runner([strategy, *others], window.out_start, window.out_end)
        done += 1
        if progress is not None:
            progress(done, total)
        out = [t for t in result.trades if t.strategy == name]
        trades.extend(out)
        out_r = [t.r_multiple for t in out if t.r_multiple is not None]
        folds.append(
            Fold(
                window=window,
                params=dict(params),
                in_score=finite(value),
                in_trades=in_trades,
                out_trades=len(out),
                out_expectancy_r=sum(out_r) / len(out_r) if out_r else None,
                out_net=round(sum(t.net_profit for t in out), 2),
                note=note,
            ),
        )
    if stopped:
        notes.append(f"cancelled after {len(folds)} of {len(plan)} windows")
    ordered = sorted(trades, key=lambda t: (t.close_time, t.open_time))
    times = np.asarray([t.close_time for t in ordered], dtype=np.float64)
    equity = start_balance + np.cumsum([t.net_profit for t in ordered], dtype=np.float64)
    metrics = compute_metrics(ordered, times, equity, start_balance, minimum_trades=minimum_trades)
    return WalkForwardResult(
        strategy=name,
        objective=objective,
        folds=tuple(folds),
        trades=tuple(ordered),
        metrics=metrics,
        minimum_trades=minimum_trades,
        cancelled=stopped,
        notes=tuple(notes),
    )


def summary_lines(result: WalkForwardResult) -> list[str]:
    m = result.metrics
    expectancy = "n/a" if m.expectancy_r is None else f"{m.expectancy_r:+.3f} R"
    verdict = "PASSED" if result.passed else "NOT PASSED"
    lines = [
        f"Walk-forward {result.strategy}: {len(result.folds)} windows, {m.trades} out-of-sample "
        f"trades, expectancy {expectancy}, net {m.net_profit:+,.2f} -> {verdict} (needs "
        f">= {result.minimum_trades} trades and a positive expectancy)",
    ]
    for fold in result.folds:
        w = fold.window
        out = "n/a" if fold.out_expectancy_r is None else f"{fold.out_expectancy_r:+.2f} R"
        lines.append(
            f"  in {_day(w.in_start)}..{_day(w.in_end)} -> out {_day(w.out_start)}.."
            f"{_day(w.out_end)}: {dict(fold.params)} -> {fold.out_trades} trades, {out}",
        )
    return [*lines, *result.notes]
