"""Purged walk-forward split generator.

Produces rolling train/test index pairs with two leakage controls:

  Purging  — removes any training sample whose label window overlaps the test
             period. A sample at position i with label_horizon k covers bars
             [i+1 .. i+k]; it is purged when i + k >= test_start.

  Embargo  — after purging, drops the `embargo` samples closest to the test
             boundary to defeat residual serial-correlation leakage.
"""
from __future__ import annotations

import warnings
from collections.abc import Iterator

import numpy as np


def walkforward_splits(
    n_samples: int,
    train_window: int,
    test_window: int,
    step: int = 1,
    label_horizon: int = 0,
    embargo: int = 0,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Yield (train_positions, test_positions) pairs for purged walk-forward CV.

    Parameters
    ----------
    n_samples:      Total number of samples in the dataset.
    train_window:   Number of samples in each rolling train set (before purge).
    test_window:    Number of samples in each test set.
    step:           How many positions to advance the test window each fold.
    label_horizon:  Forward-look of each label: sample i is purged when
                    i + label_horizon >= test_start.
    embargo:        Extra samples to drop from the top of the purged train set.
    """
    if train_window + test_window > n_samples:
        warnings.warn(
            f"train_window ({train_window}) + test_window ({test_window}) "
            f"exceeds n_samples ({n_samples}): no splits will be generated",
            stacklevel=2,
        )

    test_start = train_window
    while test_start + test_window <= n_samples:
        test_end = test_start + test_window          # exclusive
        test_positions = np.arange(test_start, test_end)

        # Rolling window: fixed-length, ends immediately before the test set.
        raw_train_start = test_start - train_window
        raw_train = np.arange(raw_train_start, test_start)

        # Purge: remove samples whose label window overlaps the test period.
        if label_horizon > 0:
            raw_train = raw_train[raw_train + label_horizon < test_start]

        # Embargo: drop the `embargo` samples closest to the test boundary.
        if embargo > 0 and len(raw_train) > embargo:
            raw_train = raw_train[: len(raw_train) - embargo]
        elif embargo > 0:
            raw_train = raw_train[:0]  # embargo consumed the entire train

        yield raw_train, test_positions
        test_start += step
