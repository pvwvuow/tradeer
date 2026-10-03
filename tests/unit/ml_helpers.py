"""A small logistic model and synthetic datasets for the ML tests (test code only).

`LogisticFactory` implements the same `Factory` protocol as LightGBM, so the trainer, the
registry and the predictor are tested without the ML library; `test_ml_lightgbm.py` runs the
real LightGBM in CI.
"""

from __future__ import annotations

import json

import numpy as np

from app.ml import features as feature_set
from app.ml.dataset import Dataset

WIDTH = len(feature_set.FEATURE_NAMES)
HOUR = 3600.0


class LogisticModel:
    def __init__(self, mean: np.ndarray, scale: np.ndarray, coef: np.ndarray, bias: float) -> None:
        self.mean, self.scale, self.coef, self.bias = mean, scale, coef, bias

    def _z(self, x: np.ndarray) -> np.ndarray:
        z = (np.atleast_2d(x) - self.mean) / self.scale
        return np.nan_to_num(z, nan=0.0)

    def predict(self, x: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-(self._z(x) @ self.coef + self.bias)))

    def contributions(self, x: np.ndarray) -> np.ndarray:
        parts = self._z(x) * self.coef
        return np.hstack([parts, np.full((len(parts), 1), self.bias)])

    def dump(self) -> str:
        return json.dumps(
            {
                "mean": self.mean.tolist(),
                "scale": self.scale.tolist(),
                "coef": self.coef.tolist(),
                "bias": self.bias,
            },
        )


class LogisticFactory:
    name = "logistic-test"

    def __init__(self) -> None:
        self.fits = 0

    def fit(self, x: np.ndarray, y: np.ndarray) -> LogisticModel:
        self.fits += 1
        mean = np.nan_to_num(np.nanmean(x, axis=0), nan=0.0)
        scale = np.nan_to_num(np.nanstd(x, axis=0), nan=1.0)
        scale[scale == 0] = 1.0
        model = LogisticModel(mean, scale, np.zeros(x.shape[1]), 0.0)
        z = model._z(x)
        coef, bias = np.zeros(x.shape[1]), 0.0
        for _ in range(300):
            p = 1.0 / (1.0 + np.exp(-(z @ coef + bias)))
            coef -= 0.5 * (z.T @ (p - y) / len(y) + 0.01 * coef)
            bias -= 0.5 * float(np.mean(p - y))
        return LogisticModel(mean, scale, coef, bias)

    def load(self, text: str) -> LogisticModel:
        data = json.loads(text)
        return LogisticModel(
            np.asarray(data["mean"]),
            np.asarray(data["scale"]),
            np.asarray(data["coef"]),
            float(data["bias"]),
        )


def synthetic(count: int = 1200, *, signal: float = 1.5, seed: int = 3) -> Dataset:
    """Samples one hour apart; the win chance depends on the `adx` column when `signal` > 0."""
    rng = np.random.default_rng(seed)
    x = rng.normal(0.0, 1.0, (count, WIDTH))
    column = feature_set.FEATURE_NAMES.index("adx")
    chance = 1.0 / (1.0 + np.exp(-(signal * x[:, column] - 0.2)))
    y = (rng.random(count) < chance).astype(np.float64)
    start = 1_790_000_000.0 + HOUR * np.arange(count)
    names = ("trend_pullback", "london_breakout")
    return Dataset(
        x=x,
        y=y,
        r=np.where(y > 0.5, 2.0, -1.0),
        start=start,
        end=start + 4 * HOUR,
        strategies=tuple(names[i % 2] for i in range(count)),
        symbols=tuple("EURUSD" for _ in range(count)),
        ids=tuple(f"s{i}" for i in range(count)),
    )
