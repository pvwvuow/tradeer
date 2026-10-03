"""Scores for probability forecasts (spec C10): ROC-AUC, log-loss, Brier score, the calibration
curve, and expectancy and profit factor per probability bucket. Pure numpy."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from app.domain.probability import wilson_interval

FloatArray = npt.NDArray[np.float64]
EPSILON = 1e-6
BUCKETS: tuple[float, ...] = (0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 1.0)


def _clip(probabilities: FloatArray) -> FloatArray:
    clipped = np.clip(np.asarray(probabilities, dtype=np.float64), EPSILON, 1.0 - EPSILON)
    return np.asarray(clipped, dtype=np.float64)


def roc_auc(labels: FloatArray, scores: FloatArray) -> float | None:
    """The chance a random win scores above a random loss (ties count half); None if one
    class is missing."""
    y = np.asarray(labels, dtype=np.float64)
    s = np.asarray(scores, dtype=np.float64)
    wins = int(np.count_nonzero(y > 0.5))
    losses = len(y) - wins
    if wins == 0 or losses == 0:
        return None
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=np.float64)
    sorted_scores = s[order]
    index = 0
    while index < len(s):
        stop = index
        while stop + 1 < len(s) and sorted_scores[stop + 1] == sorted_scores[index]:
            stop += 1
        ranks[order[index : stop + 1]] = (index + stop) / 2.0 + 1.0
        index = stop + 1
    total = float(ranks[y > 0.5].sum())
    return (total - wins * (wins + 1) / 2.0) / (wins * losses)


def log_loss(labels: FloatArray, probabilities: FloatArray) -> float:
    y = np.asarray(labels, dtype=np.float64)
    p = _clip(probabilities)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def brier(labels: FloatArray, probabilities: FloatArray) -> float:
    y = np.asarray(labels, dtype=np.float64)
    p = np.asarray(probabilities, dtype=np.float64)
    return float(np.mean((p - y) ** 2))


@dataclass(frozen=True)
class CalibrationPoint:
    predicted: float  # mean prediction in the bin
    actual: float  # win rate in the bin
    count: int


def calibration_curve(
    labels: FloatArray,
    probabilities: FloatArray,
    bins: int = 10,
) -> tuple[CalibrationPoint, ...]:
    y = np.asarray(labels, dtype=np.float64)
    p = np.asarray(probabilities, dtype=np.float64)
    edges = np.linspace(0.0, 1.0, bins + 1)
    which = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bins - 1)
    found: list[CalibrationPoint] = []
    for index in range(bins):
        mask = which == index
        count = int(np.count_nonzero(mask))
        if count:
            found.append(
                CalibrationPoint(float(p[mask].mean()), float(y[mask].mean()), count),
            )
    return tuple(found)


@dataclass(frozen=True)
class Bucket:
    """Out-of-sample trades whose predicted win chance fell in [low, high)."""

    low: float
    high: float
    count: int
    win_rate: float | None
    win_low: float | None  # Wilson 95% interval of the win rate
    win_high: float | None
    expectancy_r: float | None
    profit_factor: float | None

    def label(self) -> str:
        return f"{self.low * 100:.0f}-{self.high * 100:.0f}%"


def profit_factor_r(results: FloatArray) -> float | None:
    gains = float(results[results > 0].sum())
    losses = float(-results[results < 0].sum())
    return gains / losses if losses > 0 else None


def bucket_table(
    labels: FloatArray,
    probabilities: FloatArray,
    results_r: FloatArray,
    edges: Sequence[float] = BUCKETS,
) -> tuple[Bucket, ...]:
    y = np.asarray(labels, dtype=np.float64)
    p = np.asarray(probabilities, dtype=np.float64)
    r = np.asarray(results_r, dtype=np.float64)
    found: list[Bucket] = []
    for low, high in zip(edges[:-1], edges[1:], strict=True):
        top = high if high < 1.0 else 1.0 + EPSILON
        mask = (p >= low) & (p < top)
        count = int(np.count_nonzero(mask))
        if not count:
            found.append(Bucket(low, high, 0, None, None, None, None, None))
            continue
        wins = int(np.count_nonzero(y[mask] > 0.5))
        lower, upper = wilson_interval(wins, count)
        found.append(
            Bucket(
                low=low,
                high=high,
                count=count,
                win_rate=wins / count,
                win_low=lower,
                win_high=upper,
                expectancy_r=float(r[mask].mean()),
                profit_factor=profit_factor_r(r[mask]),
            ),
        )
    return tuple(found)


@dataclass(frozen=True)
class Scores:
    count: int
    auc: float | None
    log_loss: float
    brier: float
    win_rate: float

    def to_json(self) -> dict[str, Any]:
        found: dict[str, Any] = clean(asdict(self))
        return found


def scores(labels: FloatArray, probabilities: FloatArray) -> Scores:
    y = np.asarray(labels, dtype=np.float64)
    return Scores(
        count=len(y),
        auc=roc_auc(y, probabilities),
        log_loss=log_loss(y, probabilities),
        brier=brier(y, probabilities),
        win_rate=float(y.mean()) if len(y) else math.nan,
    )


def clean(value: Any) -> Any:
    """JSON-safe: NaN and infinity become None, tuples become lists."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [clean(item) for item in value]
    return value
