"""The real LightGBM model (spec C10, D1): training, exact TreeSHAP contributions, the text
format round trip, the registry, and training in a separate process. Runs in CI, where the
ML library is installed."""

import math
import time
from pathlib import Path

import numpy as np
import pytest

from app.backtest.costs import BacktestCosts
from app.ml import features as feature_set
from app.ml.job import TrainingJob, TrainingRequest, run_training
from app.ml.model import LightGBMFactory, LightGBMParams, lightgbm_version
from app.ml.registry import ModelRegistry
from app.ml.trainer import TrainSettings, train
from tests.unit.backtest_helpers import DAY, noisy_history
from tests.unit.ml_helpers import synthetic
from tests.unit.storage_helpers import temporary_store
from tests.unit.strategy_helpers import WEDNESDAY

pytest.importorskip("lightgbm")

FAST = LightGBMParams(num_boost_round=60)


def test_lightgbm_predicts_explains_and_reloads_exactly() -> None:
    data = synthetic(800)
    factory = LightGBMFactory(FAST)
    model = factory.fit(data.x, data.y)
    predictions = model.predict(data.x[:20])
    assert np.all((predictions > 0) & (predictions < 1))
    contributions = model.contributions(data.x[:20])
    assert contributions.shape == (20, len(feature_set.FEATURE_NAMES) + 1)
    logits = np.log(predictions / (1 - predictions))
    assert contributions.sum(axis=1) == pytest.approx(logits, abs=1e-6)
    again = factory.load(model.dump())
    assert again.predict(data.x[:20]) == pytest.approx(predictions)
    assert lightgbm_version()


def test_lightgbm_handles_missing_features() -> None:
    data = synthetic(600)
    x = data.x.copy()
    x[::3, 0] = math.nan
    model = LightGBMFactory(FAST).fit(x, data.y)
    assert np.all(np.isfinite(model.predict(x[:10])))


def test_a_lightgbm_model_goes_through_the_registry(tmp_path: Path) -> None:
    outcome = train(synthetic(1200, signal=1.5), TrainSettings(), LightGBMFactory(FAST))
    assert outcome.model is not None and outcome.report.beats_baseline, outcome.report.reason
    with temporary_store() as store:
        registry = ModelRegistry(store, tmp_path, factory=LightGBMFactory())
        info = registry.save(outcome.model)
        assert registry.activate(info.id) == ""
        predictor = registry.load_active()
        assert predictor is not None
        values = dict.fromkeys(feature_set.FEATURE_NAMES, 0.0)
        prediction = predictor.predict(values)
        assert prediction.estimate.value is not None and len(prediction.factors) == 3


def short_request() -> TrainingRequest:
    history = noisy_history(26, 1)
    return TrainingRequest(
        histories=(history,),
        strategies={"trend_pullback": {}, "london_breakout": {}},
        start=WEDNESDAY - 6 * DAY,
        end=WEDNESDAY,
        costs=BacktestCosts(),
        train=TrainSettings(min_samples=50),
    )


def test_training_on_a_replay_labels_every_signal() -> None:
    outcome = run_training(short_request(), LightGBMFactory(FAST))
    report = outcome.report
    assert report.samples > 0 and report.symbols == ("EURUSD",)
    assert report.status == "baseline"  # a few days give far too few signals
    assert sum(report.skipped.values()) >= 0


def test_training_runs_in_a_separate_process() -> None:
    job = TrainingJob()
    job.start(short_request())
    deadline = time.monotonic() + 240
    updates = []
    while not job.finished and time.monotonic() < deadline:
        updates.extend(job.poll())
        time.sleep(0.2)
    assert job.finished, "the training process did not finish"
    final = updates[-1]
    assert final.kind == "done", final.message
    assert final.outcome is not None and final.outcome.report.samples > 0
    assert any(update.kind == "progress" for update in updates)


def test_the_self_check_trains_a_tiny_model() -> None:
    from app.cli import check_ml_library

    result = check_ml_library()
    assert result.ok, result.detail
    assert "LightGBM" in result.detail
