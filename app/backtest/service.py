"""One backtest as the Backtest page and `--backtest` run it (spec C8, F3 page 7): the
replay, its metrics and Monte-Carlo, plus the optional walk-forward and sensitivity tests,
and the row saved in `backtest_runs`."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.backtest.costs import BacktestCosts
from app.backtest.engine import BacktestResult, BacktestSetup, History, run_backtest
from app.backtest.metrics import Metrics, result_metrics
from app.backtest.monte_carlo import MonteCarloResult, monte_carlo
from app.backtest.robustness import OBJECTIVES, history_runner
from app.backtest.sensitivity import SensitivityResult, sensitivity
from app.backtest.walk_forward import WalkForwardResult, walk_forward
from app.calendar.models import CalendarEvent
from app.core.execution_settings import ExecutionConfig
from app.core.strategy_settings import StrategySettings
from app.engine.filters import FilterSettings
from app.risk.settings import RiskConfig
from app.storage.signal_store import config_id
from app.strategies.base import Strategy
from app.strategies.registry import STRATEGIES, create_strategy

Progress = Callable[[str, int, int], None]  # stage, done, total
Cancelled = Callable[[], bool]
DAY = 86_400


class WalkForwardOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    strategy: str = "trend_pullback"
    grid: dict[str, list[float]] = Field(default_factory=dict)
    in_days: int = Field(default=90, ge=1, le=3650)
    out_days: int = Field(default=30, ge=1, le=3650)
    objective: str = "expectancy_r"
    minimum_in_trades: int = Field(default=10, ge=1, le=10_000)

    def candidates(self, base: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Every combination of the grid values on top of the saved params."""
        combos: list[dict[str, Any]] = [dict(base)]
        for name, values in self.grid.items():
            if values:
                combos = [{**combo, name: value} for combo in combos for value in values]
        return combos


class SensitivityOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    strategy: str = "trend_pullback"
    x_name: str = "reward_r"
    x_values: list[float] = Field(default_factory=lambda: [1.5, 2.0, 2.5])
    y_name: str = "sl_atr"
    y_values: list[float] = Field(default_factory=lambda: [1.0, 1.5, 2.0])
    metric: str = "expectancy_r"
    minimum_trades: int = Field(default=10, ge=1, le=10_000)


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbol: str = Field(default="EURUSD", min_length=1)
    start: date
    end: date  # the last day tested (inclusive)
    strategies: list[str] = Field(default_factory=lambda: list(STRATEGIES))
    costs: BacktestCosts = Field(default_factory=BacktestCosts)
    monte_carlo_runs: int = Field(default=1000, ge=0, le=20_000)
    ruin_percent: float = Field(default=50.0, gt=0, le=100)
    walk_forward: WalkForwardOptions | None = None
    sensitivity: SensitivityOptions | None = None

    @model_validator(mode="after")
    def _valid(self) -> BacktestRequest:
        if self.end < self.start:
            raise ValueError("the end date is before the start date")
        unknown = [name for name in self.strategies if name not in STRATEGIES]
        if unknown or not self.strategies:
            raise ValueError(f"unknown or no strategies: {', '.join(unknown) or 'none'}")
        for options in (self.walk_forward, self.sensitivity):
            if options is not None and options.strategy not in self.strategies:
                raise ValueError(f"{options.strategy} is not among the tested strategies")
        if self.walk_forward is not None and self.walk_forward.objective not in OBJECTIVES:
            raise ValueError(f"unknown objective {self.walk_forward.objective}")
        return self

    @property
    def utc_start(self) -> float:
        return datetime(self.start.year, self.start.month, self.start.day, tzinfo=UTC).timestamp()

    @property
    def utc_end(self) -> float:
        last = self.end + timedelta(days=1)
        return datetime(last.year, last.month, last.day, tzinfo=UTC).timestamp()

    @property
    def period(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


def default_request(today: date | None = None) -> BacktestRequest:
    """The last six months up to yesterday, both example strategies."""
    day = today or datetime.now(UTC).date()
    return BacktestRequest(start=day - timedelta(days=183), end=day - timedelta(days=1))


def strategy_params(settings: StrategySettings, name: str) -> dict[str, Any]:
    """The saved params of `name` (on or off), or its defaults when they are invalid."""
    kind = STRATEGIES[name]
    try:
        params = kind.params_model.model_validate(settings.entry(name).params)
    except ValidationError:
        params = kind.params_model()
    return dict(params.model_dump(mode="json"))


@dataclass(frozen=True)
class BacktestReport:
    request: BacktestRequest
    params: Mapping[str, Mapping[str, Any]]
    result: BacktestResult
    metrics: Metrics
    monte_carlo: MonteCarloResult | None
    walk_forward: WalkForwardResult | None = None
    sensitivity: SensitivityResult | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def storage_row(self) -> dict[str, Any]:
        """Keyword arguments for `BacktestRepository.save`."""
        first = self.request.strategies[0]
        version = STRATEGIES[first].version
        hash_ = create_strategy(first, self.params[first]).params_hash
        metrics = self.metrics.to_json()
        metrics.update(
            {
                "symbol": self.request.symbol,
                "strategies": list(self.request.strategies),
                "params": {name: dict(values) for name, values in self.params.items()},
                "signals": self.result.counts(),
                "bars": self.result.bars,
                "seconds": round(self.result.seconds, 1),
                "notes": [*self.notes, *self.result.notes],
            },
        )
        if self.sensitivity is not None:
            metrics["sensitivity"] = self.sensitivity.to_json()
        return {
            "config_id": config_id(first, version, hash_),
            "period": self.request.period,
            "costs": self.request.costs.model_dump(mode="json"),
            "metrics": metrics,
            "walk_forward": self.walk_forward.to_json() if self.walk_forward else None,
            "monte_carlo": self.monte_carlo.to_json() if self.monte_carlo else None,
        }


def run_report(
    history: History,
    request: BacktestRequest,
    *,
    settings: StrategySettings,
    risk: RiskConfig,
    execution: ExecutionConfig,
    events: Sequence[CalendarEvent] = (),
    filters: FilterSettings | None = None,
    progress: Progress | None = None,
    cancelled: Cancelled | None = None,
    log: Callable[[str, str], None] | None = None,
    notes: Sequence[str] = (),
) -> BacktestReport:
    params = {name: strategy_params(settings, name) for name in request.strategies}
    strategies: tuple[Strategy, ...] = tuple(
        create_strategy(name, params[name]) for name in request.strategies
    )
    setup = BacktestSetup(
        strategies=strategies,
        start=request.utc_start,
        end=request.utc_end,
        filters=filters or settings.filters,
        risk=risk,
        costs=request.costs,
        execution=execution,
        events=tuple(events),
    )

    def stage(name: str) -> Callable[[int, int], None] | None:
        if progress is None:
            return None
        report = progress
        return lambda done, total: report(name, done, total)

    result = run_backtest(
        history,
        setup,
        progress=stage("Backtest"),
        cancelled=cancelled,
        log=log or (lambda level, message: None),
    )
    metrics = result_metrics(result)
    carlo = None
    if request.monte_carlo_runs > 0:
        carlo = monte_carlo(
            result.trades,
            request.costs.start_balance,
            runs=request.monte_carlo_runs,
            ruin_percent=request.ruin_percent,
        )
    stopped = cancelled is not None and cancelled()
    runner = history_runner(history, setup, cancelled)
    wf: WalkForwardResult | None = None
    options = request.walk_forward
    if options is not None and not stopped:
        others = [s for s in strategies if s.name != options.strategy]
        wf = walk_forward(
            options.strategy,
            options.candidates(params[options.strategy]),
            runner,
            request.utc_start,
            request.utc_end,
            in_days=options.in_days,
            out_days=options.out_days,
            objective=options.objective,
            minimum_in_trades=options.minimum_in_trades,
            start_balance=request.costs.start_balance,
            others=others,
            progress=stage("Walk-forward"),
            cancelled=cancelled,
        )
    grid: SensitivityResult | None = None
    sens = request.sensitivity
    stopped = cancelled is not None and cancelled()
    if sens is not None and not stopped:
        others = [s for s in strategies if s.name != sens.strategy]
        grid = sensitivity(
            sens.strategy,
            params[sens.strategy],
            sens.x_name,
            sens.x_values,
            sens.y_name,
            sens.y_values,
            runner,
            request.utc_start,
            request.utc_end,
            metric=sens.metric,
            minimum_trades=sens.minimum_trades,
            others=others,
            progress=stage("Sensitivity"),
            cancelled=cancelled,
        )
    return BacktestReport(
        request=request,
        params=params,
        result=result,
        metrics=metrics,
        monte_carlo=carlo,
        walk_forward=wf,
        sensitivity=grid,
        notes=tuple(notes),
    )


def with_dates(request: BacktestRequest, start: date, end: date) -> BacktestRequest:
    return request.model_copy(update={"start": start, "end": end})


def parse_values(text: str) -> list[float]:
    """Parse a list such as 1.5, 2, 2.5 (commas or semicolons). Raises ValueError."""
    found = [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]
    return [float(part) for part in found]
