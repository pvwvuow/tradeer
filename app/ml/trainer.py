"""Training and honest validation (spec C10).

1. Fewer than `min_samples` labelled signals (or only wins or only losses): no model. The
   probability stays the **baseline**, the strategy's win rate with a Wilson interval.
2. Purged walk-forward folds (`validation.py`): each fold trains on the past only and predicts
   the next slice of time. The **baseline** forecast of a fold is the training slice's win
   rate per strategy.
3. Calibration is checked out of sample too: fold k is calibrated with a calibrator fitted on
   the predictions of folds 0..k-1 only. Fold 0 has no calibrator and is only used for that.
4. The model **beats the baseline** only when its calibrated out-of-sample log-loss and Brier
   score are both lower than the baseline's on the same signals. Otherwise the report says
   so and the registry refuses to activate it.
5. The final model is trained on every sample; its calibrator on every out-of-fold
   prediction. The report keeps ROC-AUC, log-loss, Brier, the calibration curve, and
   expectancy and profit factor per probability bucket, all out of sample.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field

from app.ml import metrics
from app.ml.calibration import Calibrator, fit_calibrator
from app.ml.dataset import Dataset
from app.ml.model import Factory, Fitted
from app.ml.validation import walk_forward_folds

FloatArray = npt.NDArray[np.float64]
Progress = Callable[[str, float], None]  # message, share done 0..1
MIN_EVALUATED = 50
PSI_BINS = 10


class TrainSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    min_samples: int = Field(
        default=300,
        ge=50,
        le=1_000_000,
        description="Labelled signals needed before a model is trained (else: baseline)",
    )
    folds: int = Field(default=5, ge=2, le=20, description="Walk-forward test folds")
    initial_share: float = Field(
        default=0.4,
        ge=0.1,
        le=0.8,
        description="Share of the oldest samples only used for training",
    )
    embargo_hours: float = Field(
        default=24.0,
        ge=0,
        le=24 * 30,
        description="Gap between training data and each test fold (hours)",
    )
    calibration: Literal["auto", "isotonic", "sigmoid"] = Field(
        default="auto",
        description="Calibration method (auto: isotonic from 1000 samples, else sigmoid)",
    )


def _quiet(message: str, share: float) -> None:
    return None


@dataclass(frozen=True)
class FoldReport:
    index: int
    train: int
    test: int
    test_start: float
    test_end: float
    raw_auc: float | None


@dataclass(frozen=True)
class TrainingReport:
    status: str  # "trained" or "baseline"
    reason: str
    samples: int
    wins: int
    period_start: float
    period_end: float
    symbols: tuple[str, ...]
    strategies: tuple[str, ...]
    beats_baseline: bool = False
    model: metrics.Scores | None = None  # calibrated, out of sample
    raw: metrics.Scores | None = None  # uncalibrated, out of sample (all folds)
    baseline: metrics.Scores | None = None  # same samples as `model`
    calibration_method: str = "none"
    calibration_curve: tuple[metrics.CalibrationPoint, ...] = ()
    buckets: tuple[metrics.Bucket, ...] = ()
    importance: tuple[tuple[str, float], ...] = ()  # mean |SHAP| (log-odds), largest first
    folds: tuple[FoldReport, ...] = ()
    skipped: Mapping[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        found: dict[str, Any] = metrics.clean(asdict(self))
        return found

    def verdict(self) -> str:
        if self.status != "trained":
            return f"Baseline only: {self.reason}"
        if not self.beats_baseline:
            return f"Not better than the baseline out of sample: {self.reason}"
        return "Beats the baseline out of sample"


@dataclass(frozen=True)
class TrainedModel:
    """Everything the registry saves and the predictor needs."""

    fitted: Fitted
    calibrator: Calibrator
    names: tuple[str, ...]
    medians: tuple[float, ...]
    reference: tuple[tuple[tuple[float, ...], tuple[float, ...]], ...]  # PSI edges, shares
    baseline_rates: Mapping[str, float]
    report: TrainingReport


@dataclass(frozen=True)
class TrainingOutcome:
    report: TrainingReport
    model: TrainedModel | None = None


def baseline_rates(dataset: Dataset, index: npt.NDArray[np.int64]) -> dict[str, float]:
    """Win rate per strategy (and overall under "") in the given samples."""
    y = dataset.y[index]
    found: dict[str, float] = {"": float(y.mean()) if len(y) else 0.5}
    names = [dataset.strategies[int(i)] for i in index]
    for name in sorted(set(names)):
        mask = np.asarray([n == name for n in names])
        found[name] = float(y[mask].mean())
    return found


def _baseline_for(rates: Mapping[str, float], strategies: list[str]) -> FloatArray:
    overall = rates.get("", 0.5)
    return np.asarray([rates.get(name, overall) for name in strategies], dtype=np.float64)


def psi_reference(
    x: FloatArray,
) -> tuple[tuple[tuple[float, ...], tuple[float, ...]], ...]:
    """Per feature: decile edges of the training values and the share in each bin."""
    found: list[tuple[tuple[float, ...], tuple[float, ...]]] = []
    for column in x.T:
        values = column[np.isfinite(column)]
        if len(values) < PSI_BINS:
            found.append(((), ()))
            continue
        edges = np.unique(np.quantile(values, np.linspace(0, 1, PSI_BINS + 1)[1:-1]))
        counts = np.bincount(np.searchsorted(edges, values, side="right"), minlength=len(edges) + 1)
        shares = tuple(float(c) / len(values) for c in counts)
        found.append((tuple(float(e) for e in edges), shares))
    return tuple(found)


def _medians(x: FloatArray) -> tuple[float, ...]:
    found: list[float] = []
    for column in x.T:
        values = column[np.isfinite(column)]
        found.append(float(np.median(values)) if len(values) else math.nan)
    return tuple(found)


def _report(dataset: Dataset, status: str, reason: str, **values: Any) -> TrainingReport:
    return TrainingReport(
        status=status,
        reason=reason,
        samples=len(dataset),
        wins=dataset.wins,
        period_start=float(dataset.start.min()) if len(dataset) else 0.0,
        period_end=float(dataset.start.max()) if len(dataset) else 0.0,
        symbols=tuple(sorted(set(dataset.symbols))),
        strategies=tuple(sorted(set(dataset.strategies))),
        skipped=dict(dataset.skipped),
        **values,
    )


def train(
    dataset: Dataset,
    settings: TrainSettings,
    factory: Factory,
    progress: Progress = _quiet,
) -> TrainingOutcome:
    count = len(dataset)
    if count < settings.min_samples:
        reason = f"{count} labelled signals, {settings.min_samples} needed for a model"
        return TrainingOutcome(_report(dataset, "baseline", reason))
    if dataset.wins in (0, count):
        return TrainingOutcome(_report(dataset, "baseline", "every signal had the same outcome"))
    folds = walk_forward_folds(
        dataset.start,
        dataset.end,
        folds=settings.folds,
        initial_share=settings.initial_share,
        embargo_seconds=settings.embargo_hours * 3600.0,
    )
    if len(folds) < 2:
        reason = "too little history for two walk-forward folds after purging"
        return TrainingOutcome(_report(dataset, "baseline", reason))
    raw = np.full(count, np.nan)
    base = np.full(count, np.nan)
    calibrated = np.full(count, np.nan)
    fold_reports: list[FoldReport] = []
    seen: list[npt.NDArray[np.int64]] = []
    for number, fold in enumerate(folds):
        progress(f"Walk-forward fold {number + 1} of {len(folds)}", number / (len(folds) + 1))
        train_y = dataset.y[fold.train]
        if len(np.unique(train_y)) < 2:
            continue
        fitted = factory.fit(dataset.x[fold.train], train_y)
        predictions = fitted.predict(dataset.x[fold.test])
        raw[fold.test] = predictions
        rates = baseline_rates(dataset, fold.train)
        base[fold.test] = _baseline_for(rates, [dataset.strategies[int(i)] for i in fold.test])
        if seen:
            before = np.concatenate(seen)
            calibrator = fit_calibrator(raw[before], dataset.y[before], settings.calibration)
            calibrated[fold.test] = calibrator.apply(predictions)
        seen.append(fold.test)
        fold_reports.append(
            FoldReport(
                index=number,
                train=len(fold.train),
                test=len(fold.test),
                test_start=fold.test_start,
                test_end=fold.test_end,
                raw_auc=metrics.roc_auc(dataset.y[fold.test], predictions),
            ),
        )
    tested = np.flatnonzero(np.isfinite(raw))
    evaluated = np.flatnonzero(np.isfinite(calibrated))
    if len(evaluated) < MIN_EVALUATED:
        reason = f"only {len(evaluated)} calibrated out-of-sample signals ({MIN_EVALUATED} needed)"
        return TrainingOutcome(_report(dataset, "baseline", reason, folds=tuple(fold_reports)))
    y_eval = dataset.y[evaluated]
    model_scores = metrics.scores(y_eval, calibrated[evaluated])
    base_scores = metrics.scores(y_eval, base[evaluated])
    raw_scores = metrics.scores(dataset.y[tested], raw[tested])
    better_loss = model_scores.log_loss < base_scores.log_loss
    better_brier = model_scores.brier < base_scores.brier
    beats = better_loss and better_brier
    if beats:
        reason = (
            f"log-loss {model_scores.log_loss:.4f} vs {base_scores.log_loss:.4f}, "
            f"Brier {model_scores.brier:.4f} vs {base_scores.brier:.4f}"
        )
    else:
        worse = [
            name
            for name, ok in (("log-loss", better_loss), ("Brier score", better_brier))
            if not ok
        ]
        reason = (
            f"{' and '.join(worse)} not lower than the baseline's (log-loss "
            f"{model_scores.log_loss:.4f} vs {base_scores.log_loss:.4f}, Brier "
            f"{model_scores.brier:.4f} vs {base_scores.brier:.4f})"
        )
    progress("Training the final model on every signal", len(folds) / (len(folds) + 1))
    final = factory.fit(dataset.x, dataset.y)
    calibrator = fit_calibrator(raw[tested], dataset.y[tested], settings.calibration)
    contributions = final.contributions(dataset.x)[:, : len(dataset.names)]
    mean_abs = np.mean(np.abs(contributions), axis=0)
    importance = tuple(
        sorted(
            ((name, round(float(v), 5)) for name, v in zip(dataset.names, mean_abs, strict=True)),
            key=lambda item: -item[1],
        ),
    )
    report = _report(
        dataset,
        "trained",
        reason,
        beats_baseline=beats,
        model=model_scores,
        raw=raw_scores,
        baseline=base_scores,
        calibration_method=calibrator.method,
        calibration_curve=metrics.calibration_curve(y_eval, calibrated[evaluated]),
        buckets=metrics.bucket_table(y_eval, calibrated[evaluated], dataset.r[evaluated]),
        importance=importance,
        folds=tuple(fold_reports),
    )
    every = np.arange(count, dtype=np.int64)
    model = TrainedModel(
        fitted=final,
        calibrator=calibrator,
        names=dataset.names,
        medians=_medians(dataset.x),
        reference=psi_reference(dataset.x),
        baseline_rates=baseline_rates(dataset, every),
        report=report,
    )
    progress("Done", 1.0)
    return TrainingOutcome(report, model)
