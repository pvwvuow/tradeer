"""Calibration (spec C10): isotonic and sigmoid, saved as plain numbers."""

import numpy as np
import pytest

from app.ml.calibration import Calibrator, fit_calibrator, fit_sigmoid, pava


def test_pava_gives_a_non_decreasing_fit() -> None:
    xs, ys = pava(np.asarray([0.1, 0.2, 0.3, 0.4]), np.asarray([0.0, 1.0, 0.0, 1.0]))
    assert list(ys) == sorted(ys)
    assert list(ys) == pytest.approx([0.0, 0.5, 1.0])
    assert list(xs) == pytest.approx([0.1, 0.25, 0.4])


def test_the_sigmoid_corrects_overconfident_scores() -> None:
    rng = np.random.default_rng(1)
    true = rng.uniform(0.2, 0.8, 4000)
    y = (rng.random(4000) < true).astype(float)
    logit = np.log(true / (1 - true))
    raw = 1 / (1 + np.exp(-3 * logit))  # three times too confident
    calibrator = fit_sigmoid(raw, y)
    assert calibrator.method == "sigmoid"
    assert calibrator.a == pytest.approx(1 / 3, abs=0.06)
    fixed = calibrator.apply(raw)
    assert np.mean(np.abs(fixed - true)) < np.mean(np.abs(raw - true)) / 2


def test_auto_picks_isotonic_only_with_many_samples() -> None:
    rng = np.random.default_rng(2)
    raw = rng.random(1500)
    y = (rng.random(1500) < raw).astype(float)
    assert fit_calibrator(raw, y).method == "isotonic"
    assert fit_calibrator(raw[:500], y[:500]).method == "sigmoid"
    assert fit_calibrator(raw[:10], np.ones(10)).method == "none"


def test_a_calibrator_survives_json() -> None:
    rng = np.random.default_rng(3)
    raw = rng.random(1200)
    y = (rng.random(1200) < raw).astype(float)
    for method in ("isotonic", "sigmoid"):
        calibrator = fit_calibrator(raw, y, method)
        again = Calibrator.from_json(calibrator.to_json())
        probe = np.asarray([0.0, 0.3, 0.7, 1.0])
        assert again.apply(probe) == pytest.approx(calibrator.apply(probe))
        assert np.all((again.apply(probe) > 0) & (again.apply(probe) < 1))
