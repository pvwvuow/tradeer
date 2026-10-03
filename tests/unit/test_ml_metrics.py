"""Forecast scores (spec C10) against hand-calculated numbers."""

import math

import numpy as np
import pytest

from app.ml import metrics


def test_roc_auc_matches_the_pair_count() -> None:
    y = np.asarray([0.0, 0.0, 1.0, 1.0])
    s = np.asarray([0.1, 0.4, 0.35, 0.8])
    assert metrics.roc_auc(y, s) == pytest.approx(0.75)  # 3 of 4 win-loss pairs ordered
    assert metrics.roc_auc(y, np.asarray([0.5, 0.5, 0.5, 0.5])) == pytest.approx(0.5)
    assert metrics.roc_auc(np.ones(3), np.asarray([0.1, 0.2, 0.3])) is None


def test_log_loss_and_brier_by_hand() -> None:
    y = np.asarray([1.0, 0.0])
    p = np.asarray([0.8, 0.4])
    expected = -(math.log(0.8) + math.log(0.6)) / 2
    assert metrics.log_loss(y, p) == pytest.approx(expected)
    assert metrics.brier(y, p) == pytest.approx((0.04 + 0.16) / 2)


def test_buckets_report_expectancy_and_profit_factor() -> None:
    y = np.asarray([1.0, 0.0, 1.0, 0.0, 1.0])
    p = np.asarray([0.62, 0.65, 0.66, 0.2, 0.95])
    r = np.asarray([2.0, -1.0, 2.0, -1.0, 1.5])
    table = {bucket.label(): bucket for bucket in metrics.bucket_table(y, p, r)}
    middle = table["60-70%"]
    assert middle.count == 3 and middle.win_rate == pytest.approx(2 / 3)
    assert middle.expectancy_r == pytest.approx(1.0)
    assert middle.profit_factor == pytest.approx(4.0)
    assert middle.win_low is not None and middle.win_low < 2 / 3 < (middle.win_high or 0)
    assert table["70-100%"].count == 1 and table["70-100%"].profit_factor is None
    assert table["0-30%"].expectancy_r == pytest.approx(-1.0)
    assert table["40-50%"].count == 0 and table["40-50%"].win_rate is None


def test_the_calibration_curve_bins_predictions() -> None:
    y = np.asarray([0.0, 1.0, 1.0, 1.0])
    p = np.asarray([0.05, 0.15, 0.85, 0.95])
    points = metrics.calibration_curve(y, p, bins=2)
    assert [(round(pt.predicted, 2), pt.actual, pt.count) for pt in points] == [
        (0.1, 0.5, 2),
        (0.9, 1.0, 2),
    ]


def test_clean_makes_json_safe_values() -> None:
    assert metrics.clean({"a": math.nan, "b": (1.0, math.inf)}) == {"a": None, "b": [1.0, None]}
