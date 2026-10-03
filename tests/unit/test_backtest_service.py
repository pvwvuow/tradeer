"""The backtest service (spec C8, F3 page 7): a request runs the replay, Monte-Carlo and the
optional walk-forward and sensitivity tests, and gives the row saved in `backtest_runs`."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.backtest.service import (
    BacktestRequest,
    SensitivityOptions,
    WalkForwardOptions,
    default_request,
    parse_values,
    run_report,
    strategy_params,
)
from app.core.execution_settings import ExecutionConfig
from app.core.strategy_settings import StrategyEntry, StrategySettings
from app.risk.settings import RiskConfig
from app.storage.backtest_store import BacktestRepository
from tests.unit.backtest_helpers import noisy_history
from tests.unit.storage_helpers import temporary_store


def test_requests_are_validated_and_give_utc_bounds() -> None:
    request = BacktestRequest(start=date(2026, 9, 20), end=date(2026, 9, 29))
    assert request.utc_end - request.utc_start == 10 * 86_400
    assert request.period == "2026-09-20..2026-09-29"
    with pytest.raises(ValidationError):
        BacktestRequest(start=date(2026, 9, 2), end=date(2026, 9, 1))
    with pytest.raises(ValidationError):
        BacktestRequest(start=date(2026, 9, 1), end=date(2026, 9, 2), strategies=["nope"])
    with pytest.raises(ValidationError):
        BacktestRequest(
            start=date(2026, 9, 1),
            end=date(2026, 9, 2),
            strategies=["london_breakout"],
            walk_forward=WalkForwardOptions(strategy="trend_pullback"),
        )
    found = default_request(date(2026, 10, 3))
    assert found.end == date(2026, 10, 2) and (found.end - found.start).days == 182


def test_saved_params_are_used_even_for_a_disabled_strategy() -> None:
    settings = StrategySettings().with_entry(
        "trend_pullback",
        StrategyEntry(enabled=False, params={"reward_r": 3.0}),
    )
    assert strategy_params(settings, "trend_pullback")["reward_r"] == 3.0
    broken = settings.with_entry("trend_pullback", StrategyEntry(params={"reward_r": -1}))
    assert strategy_params(broken, "trend_pullback")["reward_r"] == 2.0
    assert parse_values("1.5, 2; 2.5") == [1.5, 2.0, 2.5]
    with pytest.raises(ValueError):
        parse_values("1, x")


def test_a_full_report_runs_and_is_saved() -> None:
    history = noisy_history(26, 1)
    request = BacktestRequest(
        start=date(2026, 9, 24),
        end=date(2026, 9, 29),
        monte_carlo_runs=200,
        walk_forward=WalkForwardOptions(
            strategy="london_breakout",
            grid={"buffer_atr": [0.05, 0.1]},
            in_days=3,
            out_days=1,
            minimum_in_trades=1,
        ),
        sensitivity=SensitivityOptions(
            strategy="london_breakout",
            x_name="buffer_atr",
            x_values=[0.05, 0.1],
            y_name="reward_r",
            y_values=[1.5],
            minimum_trades=1,
        ),
    )
    stages: set[str] = set()
    report = run_report(
        history,
        request,
        settings=StrategySettings(),
        risk=RiskConfig(),
        execution=ExecutionConfig(),
        progress=lambda stage, done, total: stages.add(stage),
    )
    assert stages == {"Backtest", "Walk-forward", "Sensitivity"}
    assert report.result.bars == 6 * 288 and report.metrics.trades == len(report.result.trades)
    assert report.monte_carlo is not None and report.monte_carlo.runs == 200
    assert report.walk_forward is not None and len(report.walk_forward.folds) == 3
    assert report.sensitivity is not None and len(report.sensitivity.values[0]) == 2
    row = report.storage_row()
    assert row["period"] == "2026-09-24..2026-09-29"
    assert row["metrics"]["symbol"] == "EURUSD" and "sensitivity" in row["metrics"]
    with temporary_store() as store:
        saved_id = BacktestRepository(store).save("acc", **row)
        saved = BacktestRepository(store).recent()[0]
        assert saved.id == saved_id and saved.walk_forward is not None
        assert saved.monte_carlo is not None and saved.monte_carlo["runs"] == 200
