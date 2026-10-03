"""The model registry (spec C10): versions, file hashes, schema checks, activation only for a
model that beat the baseline, rollback, and the calibrated estimate with its factors."""

import json
import math
from pathlib import Path

import pytest

from app.ml import features as feature_set
from app.ml.registry import ModelError, ModelRegistry
from app.ml.trainer import TrainedModel, TrainSettings, train
from tests.unit.ml_helpers import LogisticFactory, synthetic
from tests.unit.storage_helpers import temporary_store


def trained(signal: float = 1.5, seed: int = 3) -> TrainedModel:
    outcome = train(synthetic(1200, signal=signal, seed=seed), TrainSettings(), LogisticFactory())
    assert outcome.model is not None
    return outcome.model


def test_a_saved_model_has_a_version_a_file_and_a_synced_row(tmp_path: Path) -> None:
    with temporary_store() as store:
        registry = ModelRegistry(store, tmp_path, factory=LogisticFactory())
        first = registry.save(trained())
        second = registry.save(trained(seed=4))
        assert (first.version, second.version) == (1, 2)
        assert (tmp_path / first.file).exists() and len(first.file_hash) == 64
        assert first.schema_hash == feature_set.schema_hash()
        assert first.strategies == ("london_breakout", "trend_pullback")
        assert first.symbols == ("EURUSD",) and " to " in first.period
        assert not first.active and first.beats_baseline
        assert store.count("model_versions") == 2
        rows = store.db.query("SELECT table_name FROM outbox WHERE table_name = 'model_versions'")
        assert len(rows) == 2
        payload = json.loads((tmp_path / first.file).read_text())
        assert payload["format"] == 1 and "model" in payload and "pickle" not in json.dumps(payload)


def test_only_a_model_that_beats_the_baseline_can_be_activated(tmp_path: Path) -> None:
    with temporary_store() as store:
        registry = ModelRegistry(store, tmp_path, factory=LogisticFactory())
        weak = registry.save(trained(signal=0.0, seed=8))
        assert not weak.beats_baseline
        refused = registry.activate(weak.id)
        assert "did not beat the baseline" in refused
        assert registry.active() is None and registry.load_active() is None
        good = registry.save(trained())
        assert registry.activate(good.id) == ""
        active = registry.active()
        assert active is not None and active.id == good.id


def test_rollback_returns_to_the_previous_model_or_the_baseline(tmp_path: Path) -> None:
    with temporary_store() as store:
        registry = ModelRegistry(store, tmp_path, factory=LogisticFactory())
        first = registry.save(trained())
        second = registry.save(trained(seed=5))
        assert registry.activate(second.id) == ""
        assert registry.rollback() == ""
        active = registry.active()
        assert active is not None and active.id == first.id
        assert registry.rollback() == ""
        assert registry.active() is None
        assert registry.rollback() == "no model is active"


def test_a_changed_file_or_another_schema_is_refused(tmp_path: Path) -> None:
    with temporary_store() as store:
        registry = ModelRegistry(store, tmp_path, factory=LogisticFactory())
        info = registry.save(trained())
        path = tmp_path / info.file
        path.write_text(path.read_text().replace('"format": 1', '"format": 1 '))
        with pytest.raises(ModelError, match="hash mismatch"):
            registry.load(info)
        assert "hash mismatch" in registry.activate(info.id)
        other = registry.save(trained(seed=6))
        store.upsert("model_versions", {"id": other.id, "schema_hash": "0123456789abcdef"})
        assert "feature schema does not match" in registry.activate(other.id)


def test_the_estimate_has_an_interval_a_sample_size_and_three_factors(tmp_path: Path) -> None:
    with temporary_store() as store:
        registry = ModelRegistry(store, tmp_path, factory=LogisticFactory())
        info = registry.save(trained())
        assert registry.activate(info.id) == ""
        predictor = registry.load_active()
        assert predictor is not None and predictor.covers("trend_pullback")
        values = dict.fromkeys(feature_set.FEATURE_NAMES, 0.0)
        values["adx"] = 2.0
        strong = predictor.predict(values)
        values["adx"] = -2.0
        weak = predictor.predict(values)
        estimate = strong.estimate
        assert estimate.source == "model v1" and estimate.value is not None
        assert estimate.value > (weak.estimate.value or 1.0)
        assert estimate.low is not None and estimate.high is not None
        assert estimate.low <= estimate.value <= estimate.high and estimate.samples > 0
        assert "model v1" in estimate.text() and "n = " in estimate.text()
        assert len(strong.factors) == 3
        assert strong.factors[0].startswith("a strong trend +")
        assert weak.factors[0].startswith("a weak trend -")
        assert not math.isnan(strong.raw)
