from app.analysis.structure import Trend
from app.analysis.trend import MIN_BARS, WEIGHTS, timeframe_trend, trend_matrix
from tests.unit.analysis_helpers import bars_from_closes, trending


def test_a_clean_uptrend_and_downtrend_score_strongly() -> None:
    up = timeframe_trend(bars_from_closes(trending(300, 0.0004)))
    down = timeframe_trend(bars_from_closes(trending(300, -0.0004)))
    assert up.direction is Trend.UP and up.score > 60
    assert down.direction is Trend.DOWN and down.score < -60
    assert any("stacked upward" in reason for reason in up.reasons)
    assert any(reason.startswith("ADX") for reason in up.reasons)


def test_short_history_is_reported_not_guessed() -> None:
    row = timeframe_trend(bars_from_closes(trending(MIN_BARS - 1, 0.0004)))
    assert not row.enough_data and row.score == 0
    assert "not enough history" in row.text()


def test_the_bias_is_the_weighted_average() -> None:
    bars = {
        "H1": bars_from_closes(trending(300, -0.0004), "H1"),
        "H4": bars_from_closes(trending(300, 0.0004), "H4"),
        "D1": bars_from_closes(trending(100, 0.0004), "D1"),
    }
    matrix = trend_matrix(bars)
    h1, h4 = matrix.get("H1"), matrix.get("H4")
    assert h1 is not None and h4 is not None
    weighted = h1.score * WEIGHTS["H1"] + h4.score * WEIGHTS["H4"]
    expected = weighted / (WEIGHTS["H1"] + WEIGHTS["H4"])
    assert abs(matrix.bias - expected) < 1e-9
    assert any("not enough history on D1" in reason for reason in matrix.reasons)
