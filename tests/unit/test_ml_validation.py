"""Purged walk-forward folds (spec C10): the past only, no label overlapping the test."""

import numpy as np

from app.ml.validation import walk_forward_folds

HOUR = 3600.0


def test_folds_train_on_the_past_and_purge_overlapping_labels() -> None:
    start = HOUR * np.arange(500)
    end = start + 10 * HOUR  # each outcome is known 10 hours later
    folds = walk_forward_folds(start, end, folds=4, embargo_seconds=24 * HOUR)
    assert len(folds) == 4
    previous_end = -1.0
    for fold in folds:
        cutoff = fold.test_start - 24 * HOUR
        assert np.all(end[fold.train] <= cutoff)  # purged and embargoed
        assert np.all(start[fold.test] >= fold.test_start)
        assert not set(fold.train.tolist()) & set(fold.test.tolist())
        assert fold.test_start > previous_end
        previous_end = fold.test_end
    assert len(folds[-1].train) > len(folds[0].train)


def test_shuffled_input_gives_the_same_time_order() -> None:
    rng = np.random.default_rng(4)
    order = rng.permutation(300)
    start = (HOUR * np.arange(300))[order]
    folds = walk_forward_folds(start, start + HOUR, folds=3, embargo_seconds=0.0)
    for fold in folds:
        assert start[fold.train].max() < start[fold.test].min()


def test_too_little_training_data_drops_the_fold() -> None:
    start = HOUR * np.arange(60)
    assert walk_forward_folds(start, start + HOUR, folds=3, min_train=50) == []
