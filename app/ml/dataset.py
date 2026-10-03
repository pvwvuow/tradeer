"""The training set (spec C10): labelled signals as a feature matrix.

Every signal the strategies made on history (the backtest replay keeps them all, also the
filtered-out and risk-rejected ones) is labelled by `labeler.label_signal` with the run's
cost model. Signals without model features (made before Phase 11) or without an outcome
(never filled, history too short) are left out and counted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

import numpy as np
import numpy.typing as npt

from app.analysis.bars import Bars
from app.backtest.costs import BacktestCosts
from app.domain.signals import SignalRecord
from app.ml import features as feature_set
from app.ml.labeler import Label, LabelSettings, label_signal
from app.mt5.models import SymbolSpec

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]


@dataclass(frozen=True)
class Dataset:
    x: FloatArray  # samples x features
    y: FloatArray  # 1 win, 0 loss
    r: FloatArray  # net R
    start: FloatArray  # signal time (UTC seconds)
    end: FloatArray  # outcome known (UTC seconds)
    strategies: tuple[str, ...]
    symbols: tuple[str, ...]
    ids: tuple[str, ...]
    names: tuple[str, ...] = feature_set.FEATURE_NAMES
    skipped: Mapping[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.y)

    def subset(self, index: IntArray) -> Dataset:
        picks = [int(i) for i in index]
        return Dataset(
            x=self.x[index],
            y=self.y[index],
            r=self.r[index],
            start=self.start[index],
            end=self.end[index],
            strategies=tuple(self.strategies[i] for i in picks),
            symbols=tuple(self.symbols[i] for i in picks),
            ids=tuple(self.ids[i] for i in picks),
            names=self.names,
        )

    @property
    def wins(self) -> int:
        return int(np.count_nonzero(self.y > 0.5))


def empty_dataset() -> Dataset:
    width = len(feature_set.FEATURE_NAMES)
    zero = np.zeros(0, dtype=np.float64)
    return Dataset(np.zeros((0, width)), zero, zero, zero, zero, (), (), ())


def build_dataset(
    records: Sequence[SignalRecord],
    m5: Mapping[str, Bars],
    specs: Mapping[str, SymbolSpec],
    costs: BacktestCosts,
    settings: LabelSettings | None = None,
) -> Dataset:
    """`m5` and `specs` by the signals' symbol names (the watchlist names)."""
    rows: list[FloatArray] = []
    labels: list[Label] = []
    kept: list[SignalRecord] = []
    skipped = {"no features": 0, "no bars": 0, "no outcome": 0}
    seen: set[str] = set()
    for record in sorted(records, key=lambda item: (item.signal.created_at, item.id)):
        signal = record.signal
        if signal.id in seen:
            continue
        seen.add(signal.id)
        if not feature_set.has_features(signal.features):
            skipped["no features"] += 1
            continue
        bars = m5.get(signal.symbol)
        spec = specs.get(signal.symbol)
        if bars is None or spec is None or not len(bars):
            skipped["no bars"] += 1
            continue
        label = label_signal(signal, bars, spec, costs, settings)
        if label is None:
            skipped["no outcome"] += 1
            continue
        rows.append(feature_set.vector(feature_set.from_signal(signal.features)))
        labels.append(label)
        kept.append(record)
    if not rows:
        return replace(empty_dataset(), skipped=skipped)
    return Dataset(
        x=np.vstack(rows),
        y=np.asarray([1.0 if label.win else 0.0 for label in labels]),
        r=np.asarray([label.r for label in labels]),
        start=np.asarray([record.signal.created_at for record in kept]),
        end=np.asarray([label.exit_time for label in labels]),
        strategies=tuple(record.signal.strategy for record in kept),
        symbols=tuple(record.signal.symbol for record in kept),
        ids=tuple(record.id for record in kept),
        skipped=skipped,
    )


def merge_datasets(parts: Sequence[Dataset]) -> Dataset:
    found = [part for part in parts if len(part)]
    skipped: dict[str, int] = {}
    for part in parts:
        for key, value in part.skipped.items():
            skipped[key] = skipped.get(key, 0) + value
    if not found:
        return replace(empty_dataset(), skipped=skipped)
    joined = Dataset(
        x=np.vstack([part.x for part in found]),
        y=np.concatenate([part.y for part in found]),
        r=np.concatenate([part.r for part in found]),
        start=np.concatenate([part.start for part in found]),
        end=np.concatenate([part.end for part in found]),
        strategies=tuple(s for part in found for s in part.strategies),
        symbols=tuple(s for part in found for s in part.symbols),
        ids=tuple(s for part in found for s in part.ids),
        skipped=skipped,
    )
    order = np.argsort(joined.start, kind="mergesort").astype(np.int64)
    return replace(joined.subset(order), skipped=skipped)
