"""Parameter sensitivity (spec C8): one result per pair of values of two parameters.

A good parameter choice sits on a plateau: its neighbours do almost as well. A best cell
whose neighbours do much worse is a sharp peak, a sign the parameters were fitted to noise
(overfitting). The heatmap on the Backtest page shows the grid.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.backtest.robustness import Cancelled, Runner, build, score, trade_metrics
from app.strategies.base import Strategy

PLATEAU_SHARE = 0.5  # the neighbours keep at least half of the best result
Progress = Callable[[int, int], None]


@dataclass(frozen=True)
class SensitivityResult:
    strategy: str
    metric: str
    x_name: str
    x_values: tuple[Any, ...]
    y_name: str
    y_values: tuple[Any, ...]
    values: tuple[tuple[float | None, ...], ...]  # [y][x]; None: too few trades or invalid
    trades: tuple[tuple[int, ...], ...]
    best: tuple[int, int] | None  # (y, x)
    neighbour_share: float | None  # mean of the neighbours / best
    cancelled: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def stable(self) -> bool:
        return self.neighbour_share is not None and self.neighbour_share >= PLATEAU_SHARE

    def verdict(self) -> str:
        if self.best is None:
            return "No cell had enough trades to judge."
        y, x = self.best
        where = f"{self.x_name}={self.x_values[x]}, {self.y_name}={self.y_values[y]}"
        if self.neighbour_share is None:
            return f"Best at {where}; it has no neighbours with results to compare."
        share = f"{self.neighbour_share * 100:.0f}%"
        if self.stable:
            return f"Stable plateau: the neighbours of the best cell ({where}) keep {share}."
        return (
            f"Sharp peak at {where}: its neighbours keep only {share}. The best values are "
            "probably fitted to noise (overfitting); prefer the middle of a good area."
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "metric": self.metric,
            "x": {"name": self.x_name, "values": list(self.x_values)},
            "y": {"name": self.y_name, "values": list(self.y_values)},
            "values": [list(row) for row in self.values],
            "trades": [list(row) for row in self.trades],
            "best": list(self.best) if self.best is not None else None,
            "neighbour_share": self.neighbour_share,
            "stable": self.stable,
            "verdict": self.verdict(),
            "cancelled": self.cancelled,
            "notes": list(self.notes),
        }


def plateau(
    values: Sequence[Sequence[float | None]],
) -> tuple[tuple[int, int] | None, float | None]:
    """The best cell and the mean of its (up to 8) neighbours divided by its value."""
    best: tuple[int, int] | None = None
    top = -math.inf
    for y, row in enumerate(values):
        for x, value in enumerate(row):
            if value is not None and value > top:
                best, top = (y, x), value
    if best is None:
        return None, None
    around = [
        values[y][x]
        for y in range(best[0] - 1, best[0] + 2)
        for x in range(best[1] - 1, best[1] + 2)
        if (y, x) != best and 0 <= y < len(values) and 0 <= x < len(values[y])
    ]
    if not around:
        return best, None
    if top <= 0:
        return best, 0.0
    # A neighbour without a result (too few trades, invalid) counts as nothing.
    share = sum(max(value, 0.0) for value in around if value is not None) / (len(around) * top)
    return best, share


def sensitivity(
    name: str,
    base: Mapping[str, Any],
    x_name: str,
    x_values: Sequence[Any],
    y_name: str,
    y_values: Sequence[Any],
    runner: Runner,
    start: float,
    end: float,
    *,
    metric: str = "expectancy_r",
    minimum_trades: int = 10,
    others: Sequence[Strategy] = (),
    progress: Progress | None = None,
    cancelled: Cancelled | None = None,
) -> SensitivityResult:
    if x_name == y_name:
        raise ValueError("choose two different parameters")
    if not x_values or not y_values:
        raise ValueError("give at least one value per parameter")
    values: list[list[float | None]] = []
    trades: list[list[int]] = []
    notes: list[str] = []
    total = len(x_values) * len(y_values)
    done = 0
    stopped = False
    invalid = 0
    for y_value in y_values:
        row: list[float | None] = []
        counts: list[int] = []
        for x_value in x_values:
            if cancelled is not None and cancelled():
                stopped = True
                break
            strategy = build(name, {**base, x_name: x_value, y_name: y_value})
            if strategy is None:
                invalid += 1
                row.append(None)
                counts.append(0)
            else:
                result = runner([strategy, *others], start, end)
                metrics = trade_metrics(result)
                value = score(metrics, metric, minimum_trades)
                row.append(value if math.isfinite(value) else None)
                counts.append(sum(1 for t in result.trades if t.strategy == name))
            done += 1
            if progress is not None:
                progress(done, total)
        values.append(row + [None] * (len(x_values) - len(row)))
        trades.append(counts + [0] * (len(x_values) - len(counts)))
        if stopped:
            break
    while len(values) < len(y_values):
        values.append([None] * len(x_values))
        trades.append([0] * len(x_values))
    if invalid:
        notes.append(f"{invalid} invalid combination(s) left empty")
    if stopped:
        notes.append(f"cancelled after {done} of {total} runs")
    best, share = plateau(values)
    return SensitivityResult(
        strategy=name,
        metric=metric,
        x_name=x_name,
        x_values=tuple(x_values),
        y_name=y_name,
        y_values=tuple(y_values),
        values=tuple(tuple(row) for row in values),
        trades=tuple(tuple(row) for row in trades),
        best=best,
        neighbour_share=share,
        cancelled=stopped,
        notes=tuple(notes),
    )
