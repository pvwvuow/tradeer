"""Training (spec C10): baseline below 300 signals, purged walk-forward, calibration, and
activation only when the model beats the baseline out of sample."""

import numpy as np

from app.ml.trainer import TrainSettings, train
from tests.unit.ml_helpers import LogisticFactory, synthetic


def test_too_few_signals_keep_the_baseline() -> None:
    outcome = train(synthetic(200), TrainSettings(), LogisticFactory())
    assert outcome.model is None
    assert outcome.report.status == "baseline"
    assert "300 needed" in outcome.report.reason
    assert outcome.report.verdict().startswith("Baseline only")


def test_a_real_pattern_beats_the_baseline_out_of_sample() -> None:
    factory = LogisticFactory()
    outcome = train(synthetic(1200, signal=1.5), TrainSettings(), factory)
    report = outcome.report
    assert report.status == "trained" and report.beats_baseline, report.reason
    assert report.model is not None and report.baseline is not None and report.raw is not None
    assert report.model.log_loss < report.baseline.log_loss
    assert report.model.brier < report.baseline.brier
    assert report.model.auc is not None and report.model.auc > 0.7
    assert report.importance[0][0] == "adx"
    assert len(report.folds) == 5 and factory.fits == 6  # five folds and the final model
    assert sum(bucket.count for bucket in report.buckets) == report.model.count
    assert report.calibration_curve and report.calibration_method == "sigmoid"  # 720 < 1000
    model = outcome.model
    assert model is not None and len(model.medians) == len(model.names)
    assert len(model.reference) == len(model.names)
    assert set(model.baseline_rates) == {"", "trend_pullback", "london_breakout"}


def test_noise_does_not_beat_the_baseline_and_says_why() -> None:
    outcome = train(synthetic(1200, signal=0.0, seed=8), TrainSettings(), LogisticFactory())
    report = outcome.report
    assert report.status == "trained" and not report.beats_baseline
    assert "not lower than the baseline" in report.reason
    assert report.verdict().startswith("Not better than the baseline")


def test_the_report_is_json_safe() -> None:
    import json

    outcome = train(synthetic(600, signal=1.0), TrainSettings(), LogisticFactory())
    text = json.dumps(outcome.report.to_json())
    assert "NaN" not in text and "Infinity" not in text


def test_folds_never_use_future_samples() -> None:
    seen: list[float] = []

    class Recording(LogisticFactory):
        def fit(self, x: np.ndarray, y: np.ndarray):  # type: ignore[no-untyped-def]
            seen.append(float(len(y)))
            return super().fit(x, y)

    train(synthetic(800), TrainSettings(), Recording())
    folds = seen[:-1]
    assert folds == sorted(folds) and seen[-1] == 800
