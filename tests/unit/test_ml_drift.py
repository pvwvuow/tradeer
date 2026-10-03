"""Drift monitoring (spec C10): live win rate vs predictions, and feature PSI."""

import numpy as np
import pytest

from app.ml import features as feature_set
from app.ml.drift import drift_report, psi
from app.ml.trainer import psi_reference


def test_psi_is_small_for_the_same_distribution_and_large_after_a_shift() -> None:
    rng = np.random.default_rng(5)
    training = rng.normal(0, 1, (2000, 1))
    [(edges, shares)] = psi_reference(training)
    same = psi(edges, shares, list(rng.normal(0, 1, 500)))
    moved = psi(edges, shares, list(rng.normal(1.5, 1, 500)))
    assert same is not None and same < 0.1
    assert moved is not None and moved > 0.25
    assert psi(edges, shares, [0.1] * 5) is None


def test_fewer_wins_than_predicted_and_feature_drift_warn() -> None:
    results = [(0.6, i % 4 == 0) for i in range(50)]  # 25% won, 60% predicted
    rng = np.random.default_rng(6)
    width = len(feature_set.FEATURE_NAMES)
    reference = psi_reference(rng.normal(0, 1, (1000, width)))
    adx = feature_set.FEATURE_NAMES.index("adx")
    recent = []
    for row in rng.normal(0, 1, (100, width)):
        row[adx] += 3.0
        recent.append(dict(zip(feature_set.FEATURE_NAMES, row, strict=True)))
    report = drift_report(results, recent, reference)
    assert report.retrain and len(report.warnings) == 2
    assert report.win_rate == pytest.approx(0.26) and report.mean_predicted == pytest.approx(0.6)
    assert report.feature_psi[0][0] == "adx"
    assert any("won fewer than predicted" in line for line in report.lines())


def test_matching_results_give_no_warning_and_few_trades_no_verdict() -> None:
    results = [(0.5, i % 2 == 0) for i in range(40)]
    assert not drift_report(results, [], []).warnings
    early = drift_report(results[:5], [], [])
    assert early.win_rate is None and "5 closed trades" in early.lines()[0]
