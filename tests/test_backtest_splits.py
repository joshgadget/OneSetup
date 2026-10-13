"""Phase 6 tests: time-ordered splits and walk-forward windows never shuffle."""
from __future__ import annotations

import pytest

from engine.backtest.splits import time_split, walk_forward


def test_time_split_is_ordered_contiguous_and_exhaustive():
    splits = time_split(100, train_fraction=0.5, validation_fraction=0.2, test_fraction=0.3)
    train, validation, test = splits["train"], splits["validation"], splits["test"]
    assert (train.start, train.end) == (0, 50)
    assert validation.start == train.end and test.start == validation.end
    assert test.end == 100
    assert train.size + validation.size + test.size == 100


def test_time_split_rejects_bad_fractions_and_short_series():
    with pytest.raises(ValueError):
        time_split(100, train_fraction=0.5, validation_fraction=0.2, test_fraction=0.2)
    with pytest.raises(ValueError):
        time_split(2)


def test_walk_forward_folds_move_forward_in_time():
    folds = walk_forward(400, windows=4, train_fraction=0.5)
    assert len(folds) == 4
    previous_end = 0
    for fold in folds:
        train, test = fold["train"], fold["test"]
        assert train.start == previous_end, "folds tile the series without gaps"
        assert train.end == test.start, "the test window starts where training ends"
        assert train.end > train.start and test.end > test.start
        previous_end = test.end
    assert previous_end == 400
    ends = [fold["test"].end for fold in folds]
    assert ends == sorted(ends), "time never runs backwards"


def test_walk_forward_rejects_impossible_requests():
    with pytest.raises(ValueError):
        walk_forward(10, windows=100)
    with pytest.raises(ValueError):
        walk_forward(100, windows=4, train_fraction=1.5)
