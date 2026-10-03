"""Drift monitoring (spec C10): does the live model still match reality?

- **Calibration drift**: over the last `window` closed trades whose signal had a model
  probability, the real win rate against the mean predicted probability. A gap larger than
  two standard errors (and at least 5 points) is a warning.
- **Feature drift**: the population stability index (PSI) of every feature on recent
  signals against the training data. PSI above 0.25 is a warning (0.1 to 0.25: watch).

Either warning suggests training again. Fewer than `min_trades` results give no verdict.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from app.ml import features as feature_set

PSI_WARN = 0.25
PSI_WATCH = 0.1
EPSILON = 1e-4


def psi(edges: Sequence[float], expected: Sequence[float], values: Sequence[float]) -> float | None:
    finite = np.asarray([v for v in values if math.isfinite(v)], dtype=np.float64)
    if not expected or len(finite) < 20:
        return None
    counts = np.bincount(
        np.searchsorted(np.asarray(edges, dtype=np.float64), finite, side="right"),
        minlength=len(expected),
    )
    actual = np.maximum(counts / len(finite), EPSILON)
    reference = np.maximum(np.asarray(expected, dtype=np.float64), EPSILON)
    return float(np.sum((actual - reference) * np.log(actual / reference)))


@dataclass(frozen=True)
class DriftReport:
    trades: int
    mean_predicted: float | None
    win_rate: float | None
    feature_psi: tuple[tuple[str, float], ...]  # largest first
    warnings: tuple[str, ...]

    @property
    def retrain(self) -> bool:
        return bool(self.warnings)

    def lines(self) -> list[str]:
        found: list[str] = []
        if self.mean_predicted is not None and self.win_rate is not None:
            found.append(
                f"Last {self.trades} trades: won {self.win_rate * 100:.0f}%, the model "
                f"expected {self.mean_predicted * 100:.0f}% on average",
            )
        else:
            found.append(f"{self.trades} closed trades with a model probability so far")
        top = [f"{name} {value:.2f}" for name, value in self.feature_psi[:3]]
        if top:
            found.append("Feature drift (PSI): " + ", ".join(top))
        found.extend(self.warnings)
        if not self.warnings:
            found.append("No drift warning")
        return found


def drift_report(
    results: Sequence[tuple[float, bool]],
    recent_features: Sequence[Mapping[str, float]],
    reference: Sequence[tuple[Sequence[float], Sequence[float]]],
    *,
    window: int = 50,
    min_trades: int = 20,
) -> DriftReport:
    """`results`: (predicted probability, won) of closed trades, newest first."""
    last = list(results[:window])
    warnings: list[str] = []
    mean_predicted: float | None = None
    win_rate: float | None = None
    if len(last) >= min_trades:
        predicted = np.asarray([p for p, _ in last], dtype=np.float64)
        won = np.asarray([1.0 if w else 0.0 for _, w in last], dtype=np.float64)
        mean_predicted = float(predicted.mean())
        win_rate = float(won.mean())
        error = math.sqrt(max(mean_predicted * (1 - mean_predicted), 1e-6) / len(last))
        gap = win_rate - mean_predicted
        if abs(gap) > max(2 * error, 0.05):
            side = "fewer" if gap < 0 else "more"
            warnings.append(
                f"Live trades won {side} than predicted ({win_rate * 100:.0f}% vs "
                f"{mean_predicted * 100:.0f}%): the model may be out of date; train it again",
            )
    scores: list[tuple[str, float]] = []
    for index, name in enumerate(feature_set.FEATURE_NAMES):
        if index >= len(reference):
            break
        edges, shares = reference[index]
        value = psi(edges, shares, [row.get(name, math.nan) for row in recent_features])
        if value is not None:
            scores.append((name, round(value, 3)))
    scores.sort(key=lambda item: -item[1])
    drifted = [name for name, value in scores if value > PSI_WARN]
    if drifted:
        warnings.append(
            "The market looks different from the training data ("
            + ", ".join(drifted[:3])
            + "): train the model again",
        )
    return DriftReport(len(last), mean_predicted, win_rate, tuple(scores), tuple(warnings))
