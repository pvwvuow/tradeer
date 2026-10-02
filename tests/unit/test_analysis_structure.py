import numpy as np

from app.analysis.structure import SwingKind, Trend, analyze_structure, find_swings
from tests.unit.analysis_helpers import bars_from_closes, trending

ZIGZAG = [1, 2, 3, 4, 3, 2, 3, 4, 5, 6, 5, 4, 5, 6, 7, 8, 7, 6, 7, 8, 9, 10, 9, 8, 7]


def test_swings_are_found_and_labelled() -> None:
    bars = bars_from_closes([float(value) for value in ZIGZAG], wick=0.1)
    swings = find_swings(bars, strength=2)
    highs = [swing for swing in swings if swing.kind is SwingKind.HIGH]
    lows = [swing for swing in swings if swing.kind is SwingKind.LOW]
    assert [swing.index for swing in highs] == [3, 9, 15, 21]
    assert [swing.label for swing in highs] == ["", "HH", "HH", "HH"]
    assert [swing.label for swing in lows] == ["", "HL", "HL"]
    assert all(swing.confirmed_index == swing.index + 2 for swing in swings)


def test_breaks_of_structure_and_the_trend() -> None:
    bars = bars_from_closes([float(value) for value in ZIGZAG], wick=0.1)
    structure = analyze_structure(bars, strength=2)
    assert structure.trend is Trend.UP
    assert [event.kind for event in structure.events] == ["BOS", "BOS", "BOS"]
    assert all(event.direction is Trend.UP for event in structure.events)
    down = bars_from_closes([float(value) for value in [*ZIGZAG, 6, 5, 4, 3, 2, 1]], wick=0.1)
    turned = analyze_structure(down, strength=2)
    assert turned.last_event is not None and turned.last_event.kind == "CHoCH"
    assert turned.bias is Trend.DOWN


def test_structure_never_looks_ahead() -> None:
    """The result for the first k bars equals the full result cut at bar k."""
    rng = np.random.default_rng(11)
    closes = 100 + np.cumsum(rng.normal(0, 0.5, 300))
    bars = bars_from_closes(closes, wick=0.3)
    full = analyze_structure(bars)
    for size in range(20, 300, 13):
        part = analyze_structure(bars.slice(0, size))
        assert list(part.swings) == [s for s in full.swings if s.confirmed_index < size]
        assert list(part.events) == [e for e in full.events if e.index < size]


def test_a_random_walk_trend_label_is_consistent() -> None:
    bars = bars_from_closes(trending(300, -0.0003, seed=4))
    structure = analyze_structure(bars)
    assert structure.trend in (Trend.DOWN, Trend.RANGE)
    assert structure.labels()
