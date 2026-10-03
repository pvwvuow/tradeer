"""Purged walk-forward cross-validation with an embargo (spec C10).

Samples are ordered by signal time and never shuffled. The test windows are the last
`folds` equal slices of time after an initial training share; each fold trains only on
samples that ended (outcome known) before the test window starts minus the embargo, so no
training label overlaps the test period (purging) and nothing just before it leaks in.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]


@dataclass(frozen=True)
class Fold:
    index: int
    train: IntArray
    test: IntArray
    test_start: float
    test_end: float


def walk_forward_folds(
    start_times: FloatArray,
    end_times: FloatArray,
    *,
    folds: int = 5,
    initial_share: float = 0.4,
    embargo_seconds: float = 86_400.0,
    min_train: int = 50,
) -> list[Fold]:
    """Folds over samples sorted or not; the indices refer to the given arrays."""
    starts = np.asarray(start_times, dtype=np.float64)
    ends = np.asarray(end_times, dtype=np.float64)
    count = len(starts)
    if count == 0 or folds < 1:
        return []
    order = np.argsort(starts, kind="mergesort")
    first = int(count * initial_share)
    edges = np.linspace(first, count, folds + 1).astype(int)
    found: list[Fold] = []
    for number, (low, high) in enumerate(zip(edges[:-1], edges[1:], strict=True)):
        if high <= low:
            continue
        test = order[low:high]
        test_start = float(starts[test].min())
        test_end = float(starts[test].max())
        cutoff = test_start - embargo_seconds
        train = order[(starts[order] < cutoff) & (ends[order] <= cutoff)]
        if len(train) < min_train:
            continue
        found.append(
            Fold(number, train.astype(np.int64), test.astype(np.int64), test_start, test_end),
        )
    return found
