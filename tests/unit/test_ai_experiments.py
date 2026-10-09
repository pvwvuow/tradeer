"""The AI Lab's experiments (docs/NOCURVE_V2.md 20e3): numbered per checked suggestion, the
same suggestion reused, the result with the curves, activation and a broken file."""

from __future__ import annotations

from pathlib import Path

from app.ai.experiments import (
    ACTIVE,
    BETTER,
    CURVE_POINTS,
    EXPERIMENT_FILE,
    IGNORED,
    NOT_BETTER,
    UNTESTED,
    ExperimentStore,
    thin,
)

CHANGE = ("trend_pullback", "min_adx_h1", "20", "25")


def test_a_suggestion_becomes_one_numbered_experiment(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path)
    assert store.start([], "nothing", 1.0) is None
    first = store.start([CHANGE], "weak trends lose", 10.0)
    assert first is not None and first.number == 1 and first.status == UNTESTED
    assert store.start([CHANGE], "again", 20.0) == first  # the same suggestion, reused
    other = store.start([("trend_pullback", "sl_atr", "1.5", "1.8")], "wider", 30.0)
    assert other is not None and other.number == 2
    assert [item.number for item in store.experiments()] == [2, 1]
    assert first.title == "min_adx_h1 20 \u2192 25" and first.strategies == ["trend_pullback"]


def test_the_result_activation_and_ignoring(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path)
    item = store.start([CHANGE], "weak trends lose", 10.0)
    assert item is not None
    done = store.record_result(
        item.number,
        better=False,
        symbol="EURUSD",
        period="2026-04-01..2026-09-30",
        current={"trades": 142.0, "expectancy_r": 0.21},
        proposed={"trades": 151.0, "expectancy_r": None},
        current_curve=[float(value) for value in range(500)],
        proposed_curve=[1.0, 2.0, float("nan"), 3.0],
    )
    assert done is not None and done.status == NOT_BETTER
    assert len(done.current_curve) == CURVE_POINTS and done.current_curve[-1] == 499.0
    assert done.proposed_curve == (1.0, 2.0, 3.0)
    loaded = store.get(item.number)
    assert loaded is not None and loaded.proposed == {"trades": 151.0, "expectancy_r": None}
    assert store.update(item.number, activated=50.0) is not None
    assert store.get(item.number) is not None and store.experiments()[0].status == ACTIVE
    again = store.start([CHANGE], "once more", 60.0)
    assert again is not None and again.number == 2  # an activated one is not reused
    assert store.update(2, ignored=True) is not None
    assert store.experiments()[0].status == IGNORED
    assert store.update(99, ignored=True) is None
    assert BETTER != NOT_BETTER


def test_a_broken_file_and_thin(tmp_path: Path) -> None:
    (tmp_path / EXPERIMENT_FILE).write_text("[not json", encoding="utf-8")
    assert ExperimentStore(tmp_path).experiments() == []
    assert thin([1.0, 2.0]) == (1.0, 2.0)
    assert thin([float(value) for value in range(10)], 4) == (0.0, 3.0, 6.0, 9.0)
