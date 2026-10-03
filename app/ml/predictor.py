"""The active model at signal time (spec C10): a calibrated win probability with its
uncertainty, and the three factors that moved it most, in plain words.

The interval is the Wilson 95% interval of the out-of-sample win rate in the probability
bucket the estimate falls into, with that bucket's size as n ("62% +/- 8 (n = 140, model
v3)"), so a number is never shown without how much evidence stands behind it. The model
estimates the probability at entry only; it is never re-run on open trades.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from app.domain.probability import ProbabilityEstimate, wilson_interval
from app.ml import features as feature_set
from app.ml.calibration import Calibrator
from app.ml.metrics import Bucket
from app.ml.model import Fitted

FloatArray = npt.NDArray[np.float64]
FACTORS = 3
SILENT = ("hour_sin", "hour_cos", "weekday_sin", "weekday_cos")


@dataclass(frozen=True)
class Prediction:
    estimate: ProbabilityEstimate
    factors: tuple[str, ...]  # "strong trend +8%", largest effect first
    raw: float


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def factor_text(name: str, value: float, median: float, effect: float) -> str:
    feature = feature_set.BY_NAME.get(name)
    if name in SILENT:
        words = "the time of day" if name.startswith("hour") else "the day of the week"
    elif feature is None:
        words = name
    elif not math.isfinite(value):
        words = f"{feature.meaning} unknown"
    else:
        high = value >= median if math.isfinite(median) else value > 0
        words = feature.high if high else feature.low
    return f"{words} {effect * 100:+.0f}%"


class Predictor:
    def __init__(
        self,
        *,
        version: int,
        fitted: Fitted,
        calibrator: Calibrator,
        names: Sequence[str],
        medians: Sequence[float],
        buckets: Sequence[Bucket],
        strategies: Sequence[str],
        evaluated: int,
    ) -> None:
        self.version = version
        self.fitted = fitted
        self.calibrator = calibrator
        self.names = tuple(names)
        self.medians = tuple(medians)
        self.buckets = tuple(buckets)
        self.strategies = frozenset(strategies)
        self.evaluated = evaluated

    @property
    def source(self) -> str:
        return f"model v{self.version}"

    def covers(self, strategy: str) -> bool:
        return strategy in self.strategies

    def _samples(self, probability: float) -> int:
        for bucket in self.buckets:
            top = bucket.high if bucket.high < 1.0 else 1.0 + 1e-9
            if bucket.low <= probability < top and bucket.count:
                return bucket.count
        return max(self.evaluated, 1)

    def predict(self, values: Mapping[str, float]) -> Prediction:
        row = feature_set.vector(values, self.names)[None, :]
        raw = float(self.fitted.predict(row)[0])
        probability = float(self.calibrator.apply(np.asarray([raw]))[0])
        samples = self._samples(probability)
        low, high = wilson_interval(round(probability * samples), samples)
        low, high = min(low, probability), max(high, probability)
        estimate = ProbabilityEstimate(probability, low, high, samples, self.source)
        return Prediction(estimate, self.explain(row[0], raw), raw)

    def explain(self, row: FloatArray, raw: float) -> tuple[str, ...]:
        try:
            contributions = self.fitted.contributions(row[None, :])[0]
        except Exception:
            return ()
        total = float(np.sum(contributions))
        effects: list[tuple[float, str]] = []
        for index, name in enumerate(self.names):
            value = float(contributions[index])
            if value == 0.0:
                continue
            effect = _sigmoid(total) - _sigmoid(total - value)
            median = self.medians[index] if index < len(self.medians) else math.nan
            effects.append((effect, factor_text(name, float(row[index]), median, effect)))
        effects.sort(key=lambda item: -abs(item[0]))
        return tuple(text for _, text in effects[:FACTORS])
