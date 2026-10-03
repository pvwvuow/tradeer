"""The classifier (spec C10, D1): LightGBM gradient-boosted trees.

`lightgbm` is imported only when a model is trained or loaded, so the app starts (and falls
back to the baseline) even if the library cannot load on a PC. A model is saved as LightGBM's
own text format, never as a pickle, so loading a model file cannot run code. Explanations use
LightGBM's built-in TreeSHAP (`pred_contrib=True`): the same exact SHAP values the `shap`
package computes for tree models, without its heavy dependencies (ADR 97).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]


class Fitted(Protocol):
    def predict(self, x: FloatArray) -> FloatArray:
        """Raw (uncalibrated) win probabilities."""
        ...

    def contributions(self, x: FloatArray) -> FloatArray:
        """SHAP values in log-odds, one column per feature plus the expected value last."""
        ...

    def dump(self) -> str: ...


class Factory(Protocol):
    name: str

    def fit(self, x: FloatArray, y: FloatArray) -> Fitted: ...

    def load(self, text: str) -> Fitted: ...


@dataclass(frozen=True)
class LightGBMParams:
    num_boost_round: int = 200
    learning_rate: float = 0.03
    num_leaves: int = 8
    min_data_in_leaf: int = 20
    feature_fraction: float = 0.8
    bagging_fraction: float = 0.8
    bagging_freq: int = 1
    lambda_l2: float = 1.0
    seed: int = 7
    extra: Mapping[str, Any] = field(default_factory=dict)

    def booster_params(self) -> dict[str, Any]:
        found: dict[str, Any] = {
            "objective": "binary",
            "learning_rate": self.learning_rate,
            "num_leaves": self.num_leaves,
            "min_data_in_leaf": self.min_data_in_leaf,
            "feature_fraction": self.feature_fraction,
            "bagging_fraction": self.bagging_fraction,
            "bagging_freq": self.bagging_freq,
            "lambda_l2": self.lambda_l2,
            "seed": self.seed,
            "deterministic": True,
            "num_threads": 1,
            "verbose": -1,
        }
        found.update(self.extra)
        return found


def load_lightgbm() -> Any:
    """The lightgbm module; raises RuntimeError with a plain reason if it cannot load."""
    try:
        import lightgbm
    except Exception as error:  # ImportError, OSError (a missing VC++ runtime DLL)
        raise RuntimeError(
            f"the ML library could not load: {type(error).__name__}: {error}",
        ) from error
    return lightgbm


class LightGBMModel:
    def __init__(self, booster: Any) -> None:
        self._booster = booster

    def predict(self, x: FloatArray) -> FloatArray:
        return np.asarray(self._booster.predict(np.atleast_2d(x)), dtype=np.float64)

    def contributions(self, x: FloatArray) -> FloatArray:
        found = self._booster.predict(np.atleast_2d(x), pred_contrib=True)
        return np.asarray(found, dtype=np.float64)

    def dump(self) -> str:
        return str(self._booster.model_to_string())


class LightGBMFactory:
    name = "lightgbm"

    def __init__(self, params: LightGBMParams | None = None) -> None:
        self.params = params or LightGBMParams()

    def fit(self, x: FloatArray, y: FloatArray) -> LightGBMModel:
        lightgbm = load_lightgbm()
        data = lightgbm.Dataset(np.asarray(x), label=np.asarray(y), free_raw_data=True)
        booster = lightgbm.train(
            self.params.booster_params(),
            data,
            num_boost_round=self.params.num_boost_round,
        )
        return LightGBMModel(booster)

    def load(self, text: str) -> LightGBMModel:
        lightgbm = load_lightgbm()
        return LightGBMModel(lightgbm.Booster(model_str=text))


def lightgbm_version() -> str:
    try:
        return str(load_lightgbm().__version__)
    except RuntimeError:
        return ""
