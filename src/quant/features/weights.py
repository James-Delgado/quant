"""
Sample uniqueness weighting for overlapping forward-return labels.

For a label at time t with horizon h, the label window covers bars [t+1, t+h].
When labels overlap (as they do for h > 1), adjacent samples share future bars,
so they are not independent. This module computes a weight per sample equal to
the mean uniqueness of its label window — the fraction of bars in the window
that are not shared with neighboring labels. Weights are normalized to mean=1.

Reference: López de Prado (2018), Advances in Financial Machine Learning, Ch. 4.
"""

import numpy as np


def compute_sample_weights(n_samples: int, horizon: int) -> np.ndarray:
    """Return per-sample uniqueness weights for overlapping forward-return labels.

    Parameters
    ----------
    n_samples : int
        Number of training samples (rows in the feature matrix).
    horizon : int
        Number of forward bars in the label (label_horizon from LabelResult).

    Returns
    -------
    np.ndarray
        Shape (n_samples,), dtype float64. Mean is 1.0 after normalization.
        Edge samples (start/end of window) receive higher weights; heavily
        overlapping middle samples receive lower weights.

    Raises
    ------
    ValueError
        If n_samples < 1 or horizon < 1.
    """
    if n_samples < 1:
        raise ValueError(f"n_samples must be >= 1, got {n_samples}")
    if horizon < 1:
        raise ValueError(f"horizon must be >= 1, got {horizon}")

    weights = np.zeros(n_samples, dtype=float)
    for t in range(n_samples):
        uniqueness_sum = 0.0
        for j in range(1, horizon + 1):
            b = t + j
            # t_lo/t_hi: range of sample indices whose label windows include bar b
            t_lo = max(0, b - horizon)
            t_hi = min(b - 1, n_samples - 1)
            count = max(1, t_hi - t_lo + 1)
            uniqueness_sum += 1.0 / count
        weights[t] = uniqueness_sum / horizon

    mean_w = weights.mean()
    if mean_w > 0:
        weights /= mean_w
    return weights
