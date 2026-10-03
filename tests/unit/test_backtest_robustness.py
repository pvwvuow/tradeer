"""Walk-forward and parameter sensitivity (spec C8) with a scripted runner: the choices are
made on the in-sample window only, and a sharp peak is reported as overfitting."""

from collections.abc import Sequence

import numpy as np
import pytest

from app.backtest.engine import BacktestResult
from app.backtest.sensitivity import plateau, sensitivity
from app.backtest.walk_forward import DAY, summary_lines, walk_forward, windows
from app.strategies.base import Strategy
from tests.unit.strategy_helpers import WEDNESDAY
from tests.unit.test_backtest_metrics import trade

START = float(WEDNESDAY - 100 * DAY)


class Scripted:
    """A runner whose trades depend on the strategy's params and the period (test only)."""

    def __init__(self, edge) -> None:  # type: ignore[no-untyped-def]
        self.edge = edge
        self.calls: list[tuple[dict, float, float]] = []

    def __call__(self, strategies: Sequence[Strategy], start: float, end: float) -> BacktestResult:
        params = strategies[0].params.model_dump()
        self.calls.append((params, start, end))
        r = self.edge(params, start)
        days = int((end - start) // DAY)
        trades = []
        for day in range(days):
            index = int((start - WEDNESDAY) // DAY) + day
            won = r is not None and day % 2 == 0
            value = r if won else -1.0
            if r is None:
                continue
            base = trade(index, 50.0 * value, value, strategy="trend_pullback")
            trades.append(base)
        times = np.asarray([t.close_time for t in trades], dtype=np.float64)
        equity = 10_000 + np.cumsum([t.net_profit for t in trades], dtype=np.float64)
        return BacktestResult(
            "EURUSD",
            start,
            end,
            10_000.0,
            tuple(trades),
            (),
            times,
            equity,
            equity,
            len(trades),
            0,
            0.0,
        )


def test_windows_roll_by_the_out_of_sample_length() -> None:
    found = windows(0.0, 100 * DAY, 30, 10)
    assert len(found) == 7
    assert (found[0].in_start, found[0].in_end, found[0].out_end) == (0.0, 30 * DAY, 40 * DAY)
    assert found[1].in_start == 10 * DAY and found[-1].out_end == 100 * DAY
    with pytest.raises(ValueError):
        windows(0.0, DAY, 0, 1)


def test_walk_forward_picks_on_in_sample_and_counts_out_of_sample_only() -> None:
    # reward_r 3 wins big before day 60, reward_r 2 is steady; the choice must follow the data.
    def edge(params, start):  # type: ignore[no-untyped-def]
        if params["reward_r"] == 3.0:
            return 3.0 if start < WEDNESDAY - 60 * DAY else 0.5
        return 2.0

    runner = Scripted(edge)
    candidates = [{"reward_r": 2.0}, {"reward_r": 3.0}]
    result = walk_forward(
        "trend_pullback",
        candidates,
        runner,
        START,
        START + 100 * DAY,
        in_days=30,
        out_days=10,
        minimum_in_trades=5,
        minimum_trades=20,
    )
    assert len(result.folds) == 7
    assert result.folds[0].params == {"reward_r": 3.0}
    assert result.folds[-1].params == {"reward_r": 2.0}
    assert sum(f.out_trades for f in result.folds) == result.metrics.trades == 70
    for fold in result.folds:  # out-of-sample runs used the chosen params on unseen days
        assert (dict(runner.calls[0][0]) | fold.params)["reward_r"] == fold.params["reward_r"]
        assert fold.window.out_start >= fold.window.in_end
    assert result.passed and result.to_json()["passed"]
    assert summary_lines(result)[0].startswith("Walk-forward trend_pullback: 7 windows")


def test_walk_forward_fails_without_enough_out_of_sample_trades() -> None:
    result = walk_forward(
        "trend_pullback",
        [{"reward_r": 2.0}],
        Scripted(lambda params, start: 2.0),
        START,
        START + 40 * DAY,
        in_days=30,
        out_days=10,
    )
    assert result.metrics.trades == 10 and not result.passed
    with pytest.raises(ValueError):
        walk_forward(
            "trend_pullback",
            [],
            Scripted(lambda p, s: 1.0),
            START,
            START + DAY,
            in_days=1,
            out_days=1,
        )


def test_a_sharp_peak_is_flagged_and_a_plateau_is_not() -> None:
    def peak(params, start):  # type: ignore[no-untyped-def]
        return 4.0 if (params["reward_r"], params["sl_atr"]) == (2.0, 1.5) else 0.6

    grid = {
        "x_name": "reward_r",
        "x_values": (1.5, 2.0, 2.5),
        "y_name": "sl_atr",
        "y_values": (1.0, 1.5, 2.0),
    }
    sharp = sensitivity(
        "trend_pullback",
        {},
        runner=Scripted(peak),
        start=START,
        end=START + 20 * DAY,
        **grid,  # type: ignore[arg-type]
    )
    assert sharp.best == (1, 1) and not sharp.stable
    assert "Sharp peak at reward_r=2.0, sl_atr=1.5" in sharp.verdict()
    flat = sensitivity(
        "trend_pullback",
        {},
        runner=Scripted(lambda params, start: 2.0 + params["reward_r"] / 10),
        start=START,
        end=START + 20 * DAY,
        **grid,  # type: ignore[arg-type]
    )
    assert flat.stable and flat.verdict().startswith("Stable plateau")
    assert flat.to_json()["values"][0][2] == pytest.approx(flat.values[0][2])


def test_invalid_combinations_stay_empty() -> None:
    result = sensitivity(
        "trend_pullback",
        {},
        "fast_ema_h1",
        (50, 500),
        "slow_ema_h1",
        (200,),
        Scripted(lambda params, start: 2.0),
        START,
        START + 20 * DAY,
    )
    assert result.values[0][1] is None and "1 invalid" in result.notes[0]


def test_plateau_counts_missing_neighbours_as_nothing() -> None:
    assert plateau([[None, None], [None, None]]) == (None, None)
    best, share = plateau([[1.0, None], [None, None]])
    assert best == (0, 0) and share == 0.0
    best, share = plateau([[2.0, 1.0]])
    assert best == (0, 0) and share == pytest.approx(0.5)
