"""The model in the signal pipeline (spec C10): its estimate and factors replace the baseline
for the strategies it covers; a failing model falls back to the baseline."""

from collections.abc import Mapping

from app.core.clock import BrokerClock
from app.domain.probability import ProbabilityEstimate
from app.engine.filters import FilterSettings
from app.engine.signal_pipeline import SignalPipeline
from app.ml import features as feature_set
from app.ml.predictor import Prediction
from app.strategies.registry import create_strategy
from tests.unit.signal_helpers import MORNING
from tests.unit.strategy_helpers import analysis_at, london_days

CLOCK = BrokerClock.assumed()


class StubModel:
    source = "model v9"

    def __init__(self, *, covers: str = "london_breakout", fail: bool = False) -> None:
        self.covered = covers
        self.fail = fail
        self.seen: list[Mapping[str, float]] = []

    def covers(self, strategy: str) -> bool:
        return strategy == self.covered

    def predict(self, values: Mapping[str, float]) -> Prediction:
        if self.fail:
            raise RuntimeError("broken model")
        self.seen.append(values)
        estimate = ProbabilityEstimate(0.62, 0.55, 0.69, 140, self.source)
        factors = ("a strong trend +8%", "a wide spread -5%", "news soon -2%")
        return Prediction(estimate, factors, 0.6)


def run(model: StubModel) -> tuple[list, list[tuple[str, str]]]:  # type: ignore[type-arg]
    logs: list[tuple[str, str]] = []
    strategies = [create_strategy("london_breakout")]
    signals = SignalPipeline(
        lambda: strategies,
        FilterSettings,
        model=lambda: model,
        log=lambda level, message: logs.append((level, message)),
        utc_now=lambda: float(MORNING),
    )
    m15 = london_days()
    analysis, now = analysis_at(m15, len(m15))
    created = signals.on_analysis(analysis, clock=CLOCK, spec=None, spread=2e-5, now=now)
    return created, logs


def test_the_active_model_gives_the_probability_and_its_factors() -> None:
    model = StubModel()
    created, _ = run(model)
    assert len(created) == 2 and len(model.seen) == 2
    assert set(model.seen[0]) == set(feature_set.FEATURE_NAMES)
    for record in created:
        assert record.probability.source == "model v9"
        assert record.probability.value == 0.62
        assert record.expected_value is not None
        step = next(s for s in record.trace.steps if s.stage == "probability")
        assert "62% \u00b1 7 (n = 140, model v9)" in step.detail
        assert "main factors: a strong trend +8%" in step.detail
        assert record.signal.features["probability_factors"].startswith("a strong trend")


def test_a_model_for_another_strategy_or_a_failing_one_leaves_the_baseline() -> None:
    created, _ = run(StubModel(covers="trend_pullback"))
    assert all(record.probability.source == "none" for record in created)
    created, logs = run(StubModel(fail=True))
    assert all(record.probability.source == "none" for record in created)
    assert any(level == "WARNING" and "broken model" in text for level, text in logs)
