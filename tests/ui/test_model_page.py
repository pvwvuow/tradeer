"""The Model page (spec C10): training through a (fake) job, the out-of-sample report, and
versions that can only be used when they beat the baseline."""

from pathlib import Path

from pytestqt.qtbot import QtBot

from app.core.strategy_settings import StrategySettingsSource
from app.ml.job import JobUpdate, TrainingRequest
from app.ml.registry import ModelRegistry
from app.ml.service import ModelService
from app.ml.trainer import TrainSettings, train
from app.risk.settings import RiskSettingsSource
from app.strategies.registry import STRATEGIES
from app.ui.model_page import NOT_CONNECTED, ModelContext, ModelPage
from tests.unit.backtest_helpers import noisy_history
from tests.unit.ml_helpers import LogisticFactory, synthetic
from tests.unit.storage_helpers import temporary_store

HISTORY = noisy_history(26, 1)


class FakeJob:
    """Answers with a model trained on synthetic data (no process, no LightGBM)."""

    requests: list[TrainingRequest] = []

    def __init__(self) -> None:
        self.finished = False
        self.sent = False

    @property
    def running(self) -> bool:
        return not self.finished

    def start(self, request: TrainingRequest) -> None:
        FakeJob.requests.append(request)

    def poll(self) -> list[JobUpdate]:
        if self.sent:
            return []
        self.sent = True
        self.finished = True
        outcome = train(synthetic(1200), TrainSettings(), LogisticFactory())
        return [
            JobUpdate("progress", "Replaying EURUSD: 50%", 0.4),
            JobUpdate("done", "ok", 1.0, outcome),
        ]

    def cancel(self) -> None:
        self.finished = True


def context(tmp_path: Path, service: ModelService, connected: bool = True) -> ModelContext:
    return ModelContext(
        service=service,
        load=lambda symbol, start, end, note: HISTORY,
        strategies=StrategySettingsSource(tmp_path),
        risk=RiskSettingsSource(tmp_path),
        symbols=lambda: ["EURUSD"],
        connected=lambda: connected,
        job_factory=FakeJob,  # type: ignore[arg-type]
    )


def test_without_a_context_the_page_cannot_train(qtbot: QtBot) -> None:
    page = ModelPage(None)
    qtbot.addWidget(page)
    assert not page.train_button.isEnabled()
    assert page.versions.rowCount() == 0


def test_not_connected_says_so(qtbot: QtBot, tmp_path: Path) -> None:
    with temporary_store() as store:
        service = ModelService(ModelRegistry(store, tmp_path / "models", factory=LogisticFactory()))
        page = ModelPage(context(tmp_path, service, connected=False))
        qtbot.addWidget(page)
        page.start_training()
        assert page.status.text() == NOT_CONNECTED and not page.running


def test_training_shows_the_report_saves_and_activates(qtbot: QtBot, tmp_path: Path) -> None:
    with temporary_store() as store:
        registry = ModelRegistry(store, tmp_path / "models", factory=LogisticFactory())
        service = ModelService(registry)
        page = ModelPage(context(tmp_path, service))
        qtbot.addWidget(page)
        page.start_training()
        qtbot.waitUntil(lambda: page.last_outcome is not None, timeout=20_000)
        request = FakeJob.requests[-1]
        assert request.histories[0].symbol == "EURUSD"
        assert set(request.strategies) == set(STRATEGIES)  # the model learns from every one
        assert "Beats the baseline" in page.verdict.text()
        assert page.scores.rowCount() == 3 and page.buckets.rowCount() > 0
        assert page.importance.item(0, 0).text() == "adx"
        assert page.versions.rowCount() == 1 and "Saved as v1" in page.status.text()
        page.versions.selectRow(0)
        page.activate_selected()
        assert page.version_status.text() == "Now using model v1."
        assert service.current() is not None
        assert "Using model v1" in page.active_label.text()
        page.use_baseline()
        assert service.current() is None
        assert "baseline" in page.active_label.text()
