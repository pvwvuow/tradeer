"""The model service (spec C10): the pipeline's cached model, activation with an audit line,
rollback, the baseline switch and the drift report."""

from pathlib import Path

from app.ml.registry import ModelRegistry
from app.ml.service import ModelService
from app.ml.trainer import TrainSettings, train
from app.storage.signal_store import SignalRepository
from tests.unit.ml_helpers import LogisticFactory, synthetic
from tests.unit.storage_helpers import temporary_store


def test_activation_switches_the_model_the_pipeline_sees(tmp_path: Path) -> None:
    with temporary_store() as store:
        logs: list[tuple[str, str]] = []
        audits: list[tuple[str, str, str]] = []
        registry = ModelRegistry(store, tmp_path, factory=LogisticFactory())
        service = ModelService(
            registry,
            SignalRepository(store),
            log=lambda level, text: logs.append((level, text)),
            audit=lambda action, before, after: audits.append((action, before, after)),
        )
        assert service.current() is None
        good = train(synthetic(1200), TrainSettings(), LogisticFactory()).model
        weak = train(synthetic(1200, signal=0.0, seed=8), TrainSettings(), LogisticFactory()).model
        assert good is not None and weak is not None
        first = service.save(good)
        refused = service.save(weak)
        assert "did not beat" in service.activate(refused.id)
        assert any(level == "WARNING" for level, _ in logs)
        assert service.activate(first.id) == ""
        current = service.current()
        assert current is not None and current.source == "model v1"
        assert audits[-1] == ("probability model changed", "baseline", "v1")
        report = service.drift()
        assert report is not None and report.trades == 0 and not report.warnings
        service.use_baseline()
        assert service.current() is None
        assert audits[-1] == ("probability model changed", "v1", "baseline")
        assert service.rollback() == "no model is active"


def test_a_missing_model_file_falls_back_to_the_baseline(tmp_path: Path) -> None:
    with temporary_store() as store:
        logs: list[tuple[str, str]] = []
        registry = ModelRegistry(store, tmp_path, factory=LogisticFactory())
        model = train(synthetic(1200), TrainSettings(), LogisticFactory()).model
        assert model is not None
        info = registry.save(model)
        assert registry.activate(info.id) == ""
        (tmp_path / info.file).unlink()
        service = ModelService(registry, log=lambda level, text: logs.append((level, text)))
        assert service.current() is None
        assert any(level == "ERROR" and "baseline" in text for level, text in logs)
