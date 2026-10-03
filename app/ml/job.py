"""Training in a separate process (spec C10, D3.2): the window stays smooth.

The parent loads the history through the MT5 gateway (MT5 lives in the app), then starts a
`spawn` child with the bars and the settings. The child replays the strategies on the history
(the backtest engine keeps every signal, filtered ones too), labels each signal with the
backtest's cost model, and trains and validates the model. It reports progress through a
queue and sends back the report and the model as text (LightGBM's model format, no pickle
of a model object). The parent saves it in the registry.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import queue
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from multiprocessing.process import BaseProcess
from typing import Any

from app.backtest.costs import BacktestCosts
from app.backtest.engine import BacktestSetup, History, run_backtest
from app.calendar.models import CalendarEvent
from app.engine.filters import FilterSettings
from app.ml.dataset import Dataset, build_dataset, merge_datasets
from app.ml.labeler import LabelSettings
from app.ml.model import Factory, LightGBMFactory
from app.ml.trainer import TrainedModel, TrainingOutcome, TrainSettings, train
from app.risk.settings import RiskConfig
from app.strategies.registry import create_strategy

Progress = Callable[[str, float], None]
Cancelled = Callable[[], bool]


@dataclass(frozen=True)
class TrainingRequest:
    histories: tuple[History, ...]
    strategies: Mapping[str, Mapping[str, Any]]  # name -> params
    start: float  # UTC seconds
    end: float
    costs: BacktestCosts = field(default_factory=BacktestCosts)
    filters: FilterSettings = field(default_factory=FilterSettings)
    risk: RiskConfig = field(default_factory=RiskConfig)
    label: LabelSettings = field(default_factory=LabelSettings)
    train: TrainSettings = field(default_factory=TrainSettings)
    events: tuple[CalendarEvent, ...] = ()


class SavedText:
    """A trained model as text: what crosses the process boundary."""

    def __init__(self, text: str) -> None:
        self._text = text

    def predict(self, x: Any) -> Any:
        raise RuntimeError("load the model through the registry to predict")

    def contributions(self, x: Any) -> Any:
        raise RuntimeError("load the model through the registry to explain")

    def dump(self) -> str:
        return self._text


def _quiet(message: str, share: float) -> None:
    return None


def build_training_set(
    request: TrainingRequest,
    progress: Progress = _quiet,
    cancelled: Cancelled | None = None,
) -> Dataset:
    strategies = tuple(create_strategy(name, params) for name, params in request.strategies.items())
    setup = BacktestSetup(
        strategies=strategies,
        start=request.start,
        end=request.end,
        filters=request.filters,
        risk=request.risk,
        costs=request.costs,
        events=request.events,
    )
    parts: list[Dataset] = []
    count = len(request.histories)
    for number, history in enumerate(request.histories):

        def step(done: int, total: int, number: int = number, symbol: str = history.symbol) -> None:
            share = (number + done / max(total, 1)) / max(count, 1) * 0.8
            progress(f"Replaying {symbol}: {done * 100 // max(total, 1)}%", share)

        result = run_backtest(history, setup, progress=step, cancelled=cancelled)
        m5 = history.bars.get("M5")
        if m5 is None:
            continue
        share = (number + 1) / count * 0.8
        progress(f"Labelling {len(result.signals)} {history.symbol} signals", share)
        parts.append(
            build_dataset(
                result.signals,
                {history.symbol: m5},
                {history.symbol: history.spec},
                request.costs,
                request.label,
            ),
        )
    return merge_datasets(parts)


def run_training(
    request: TrainingRequest,
    factory: Factory | None = None,
    progress: Progress = _quiet,
    cancelled: Cancelled | None = None,
) -> TrainingOutcome:
    dataset = build_training_set(request, progress, cancelled)

    def later(message: str, share: float) -> None:
        progress(message, 0.8 + 0.2 * share)

    return train(dataset, request.train, factory or LightGBMFactory(), later)


def portable(outcome: TrainingOutcome) -> TrainingOutcome:
    """The outcome with the model as text, so it can be sent to the parent."""
    model = outcome.model
    if model is None:
        return outcome
    return replace(outcome, model=replace(model, fitted=SavedText(model.fitted.dump())))


def _child(request: TrainingRequest, messages: Any) -> None:
    def progress(message: str, share: float) -> None:
        messages.put(("progress", message, share))

    try:
        outcome = run_training(request, progress=progress)
        messages.put(("done", portable(outcome), 1.0))
    except Exception as error:
        detail = "".join(traceback.format_exception_only(type(error), error)).strip()
        messages.put(("error", detail, 1.0))


@dataclass(frozen=True)
class JobUpdate:
    kind: str  # "progress", "done" or "error"
    message: str
    share: float
    outcome: TrainingOutcome | None = None


class TrainingJob:
    """One training run in a child process. Poll it from the UI thread with a timer."""

    def __init__(self) -> None:
        self._process: BaseProcess | None = None
        self._messages: Any = None
        self.finished = False

    @property
    def running(self) -> bool:
        return self._process is not None and not self.finished

    def start(self, request: TrainingRequest) -> None:
        context = multiprocessing.get_context("spawn")
        self._messages = context.Queue()
        process = context.Process(
            target=_child,
            args=(request, self._messages),
            name="ml-training",
            daemon=True,
        )
        process.start()
        self._process = process
        self.finished = False

    def poll(self) -> list[JobUpdate]:
        found: list[JobUpdate] = []
        if self._messages is None:
            return found
        while True:
            try:
                kind, payload, share = self._messages.get_nowait()
            except queue.Empty:
                break
            if kind == "done":
                self.finished = True
                found.append(JobUpdate("done", "Training finished", 1.0, payload))
            elif kind == "error":
                self.finished = True
                found.append(JobUpdate("error", str(payload), 1.0))
            else:
                found.append(JobUpdate("progress", str(payload), float(share)))
        process = self._process
        if not self.finished and process is not None and not process.is_alive():
            self.finished = True
            code = process.exitcode
            text = f"the training process stopped (exit code {code})"
            found.append(JobUpdate("error", text, 1.0))
        return found

    def cancel(self) -> None:
        process = self._process
        if process is not None and process.is_alive():
            process.terminate()
            with contextlib.suppress(Exception):
                process.join(timeout=5)
        self.finished = True


def restore(model: TrainedModel, factory: Factory) -> TrainedModel:
    """A model received as text, made usable again (tests and the CLI)."""
    return replace(model, fitted=factory.load(model.fitted.dump()))
