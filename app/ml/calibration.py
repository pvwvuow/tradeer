"""Probability calibration (spec C10): isotonic regression or a sigmoid (Platt scaling),
fitted on out-of-fold predictions and saved as plain numbers (no pickle).

`fit_calibrator` picks isotonic with at least `ISOTONIC_MIN` samples (it needs many points
to be stable) and the sigmoid below that.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
ISOTONIC_MIN = 1000
EPSILON = 1e-6


def _logit(p: FloatArray) -> FloatArray:
    q = np.clip(p, EPSILON, 1.0 - EPSILON)
    return np.asarray(np.log(q / (1.0 - q)), dtype=np.float64)


def _sigmoid(z: FloatArray) -> FloatArray:
    return np.asarray(1.0 / (1.0 + np.exp(-z)), dtype=np.float64)


def pava(x: FloatArray, y: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Pool adjacent violators: the non-decreasing step function closest to y (by x).
    Returns the block x positions (block means) and block values."""
    order = np.argsort(x, kind="mergesort")
    xs = np.asarray(x, dtype=np.float64)[order]
    ys = np.asarray(y, dtype=np.float64)[order]
    values: list[float] = []
    weights: list[float] = []
    positions: list[float] = []
    for xi, yi in zip(xs, ys, strict=True):
        values.append(float(yi))
        weights.append(1.0)
        positions.append(float(xi))
        while len(values) > 1 and values[-2] > values[-1]:
            w = weights[-2] + weights[-1]
            v = (values[-2] * weights[-2] + values[-1] * weights[-1]) / w
            p = (positions[-2] * weights[-2] + positions[-1] * weights[-1]) / w
            values[-2:] = [v]
            weights[-2:] = [w]
            positions[-2:] = [p]
    return np.asarray(positions, dtype=np.float64), np.asarray(values, dtype=np.float64)


@dataclass(frozen=True)
class Calibrator:
    method: str  # "isotonic", "sigmoid" or "none"
    a: float = 1.0  # sigmoid: p = 1 / (1 + exp(-(a * logit(raw) + b)))
    b: float = 0.0
    xs: tuple[float, ...] = ()  # isotonic knots (raw probability -> calibrated)
    ys: tuple[float, ...] = ()

    def apply(self, raw: FloatArray) -> FloatArray:
        p = np.asarray(raw, dtype=np.float64)
        if self.method == "sigmoid":
            return _sigmoid(self.a * _logit(p) + self.b)
        if self.method == "isotonic" and self.xs:
            found = np.interp(p, np.asarray(self.xs), np.asarray(self.ys))
            return np.asarray(np.clip(found, EPSILON, 1.0 - EPSILON), dtype=np.float64)
        return p

    def to_json(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "a": self.a,
            "b": self.b,
            "xs": list(self.xs),
            "ys": list(self.ys),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Calibrator:
        return cls(
            method=str(data.get("method", "none")),
            a=float(data.get("a", 1.0)),
            b=float(data.get("b", 0.0)),
            xs=tuple(float(v) for v in data.get("xs", ())),
            ys=tuple(float(v) for v in data.get("ys", ())),
        )


def fit_sigmoid(raw: FloatArray, labels: FloatArray, iterations: int = 50) -> Calibrator:
    """Platt scaling on the logit of the raw probability, by Newton's method."""
    z = _logit(np.asarray(raw, dtype=np.float64))
    y = np.asarray(labels, dtype=np.float64)
    a, b = 1.0, 0.0
    for _ in range(iterations):
        p = _sigmoid(a * z + b)
        w = p * (1.0 - p) + 1e-9
        g_a = float(np.sum((p - y) * z))
        g_b = float(np.sum(p - y))
        h_aa = float(np.sum(w * z * z)) + 1e-6
        h_ab = float(np.sum(w * z))
        h_bb = float(np.sum(w)) + 1e-6
        det = h_aa * h_bb - h_ab * h_ab
        if det <= 0:
            break
        step_a = (h_bb * g_a - h_ab * g_b) / det
        step_b = (h_aa * g_b - h_ab * g_a) / det
        a, b = a - step_a, b - step_b
        if abs(step_a) < 1e-9 and abs(step_b) < 1e-9:
            break
    if not (math.isfinite(a) and math.isfinite(b)):
        return Calibrator("none")
    return Calibrator("sigmoid", a=a, b=b)


def fit_isotonic(raw: FloatArray, labels: FloatArray) -> Calibrator:
    xs, ys = pava(np.asarray(raw, dtype=np.float64), np.asarray(labels, dtype=np.float64))
    return Calibrator("isotonic", xs=tuple(float(v) for v in xs), ys=tuple(float(v) for v in ys))


def fit_calibrator(raw: FloatArray, labels: FloatArray, method: str = "auto") -> Calibrator:
    if len(raw) < 2 or len(np.unique(labels)) < 2:
        return Calibrator("none")
    if method == "isotonic" or (method == "auto" and len(raw) >= ISOTONIC_MIN):
        return fit_isotonic(raw, labels)
    return fit_sigmoid(raw, labels)
