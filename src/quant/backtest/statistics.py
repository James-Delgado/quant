"""Statistical tests for model comparison.

Diebold-Mariano test
---------------------
Tests whether two forecast series have equal predictive accuracy.
Use to compare GBM vs Ridge on out-of-sample forecast errors.

Reference:
  Diebold, F.X., & Mariano, R.S. (1995). Comparing Predictive Accuracy.
  Journal of Business & Economic Statistics, 13(3), 253-263.

  Harvey, D., Leybourne, S., & Newbold, P. (1997). Testing the equality of
  prediction mean squared errors. International Journal of Forecasting,
  13(2), 281-291.

The Harvey et al. (1997) small-sample correction is applied by default.
Without it the test over-rejects in small samples (T ~ 200-300 OOS bars).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass(frozen=True)
class DMResult:
    """Result of a Diebold-Mariano test."""

    statistic: float
    p_value: float
    n_obs: int
    h: int
    small_sample_corrected: bool

    def __str__(self) -> str:
        corrected = " (HLN-corrected)" if self.small_sample_corrected else ""
        return (
            f"DM test{corrected}: stat={self.statistic:.4f}, "
            f"p={self.p_value:.4f}, n={self.n_obs}, h={self.h}"
        )


def diebold_mariano(
    errors_a: np.ndarray,
    errors_b: np.ndarray,
    h: int = 1,
    alternative: str = "less",
    small_sample_correction: bool = True,
) -> DMResult:
    """Diebold-Mariano test for equal predictive accuracy.

    Tests H0: model A and model B have equal mean squared prediction error.
    H1 (alternative="less"): model A has smaller MSE than model B.

    Parameters
    ----------
    errors_a:  Forecast errors for model A (actual - predicted), length T.
    errors_b:  Forecast errors for model B (actual - predicted), length T.
    h:         Forecast horizon (bars). Use 1 for one-step-ahead forecasts.
               For h > 1 the loss differential has serial correlation of
               order h-1 accounted for via Newey-West HAC.
    alternative: "less" (A better than B), "greater", or "two-sided".
    small_sample_correction: Apply Harvey, Leybourne & Newbold (1997)
               correction. Recommended for T < 500.

    Returns
    -------
    DMResult with statistic, p-value, n_obs, h, and correction flag.
    """
    errors_a = np.asarray(errors_a, dtype=float)
    errors_b = np.asarray(errors_b, dtype=float)

    if errors_a.shape != errors_b.shape:
        raise ValueError(
            f"errors_a and errors_b must have the same shape, "
            f"got {errors_a.shape} and {errors_b.shape}"
        )
    if errors_a.ndim != 1:
        raise ValueError("errors_a and errors_b must be 1-D arrays")

    T = len(errors_a)
    if T < 4:
        raise ValueError(f"Need at least 4 observations, got {T}")
    if h < 1:
        raise ValueError(f"h must be >= 1, got {h}")

    d = errors_a ** 2 - errors_b ** 2
    d_bar = d.mean()

    # Newey-West HAC variance estimate with bandwidth h-1
    gamma_0 = np.var(d, ddof=0)
    gamma_sum = 0.0
    for lag in range(1, h):
        gamma_j = np.mean((d[lag:] - d_bar) * (d[:-lag] - d_bar))
        gamma_sum += (1 - lag / h) * gamma_j
    var_d_bar = (gamma_0 + 2 * gamma_sum) / T

    if var_d_bar <= 0:
        raise ValueError(
            "Variance of loss differential is non-positive — "
            "check that the two error series are not identical"
        )

    dm_stat = d_bar / np.sqrt(var_d_bar)

    if small_sample_correction:
        # Harvey, Leybourne & Newbold (1997): multiply by sqrt(correction)
        correction = (T + 1 - 2 * h + h * (h - 1) / T) / T
        dm_stat = dm_stat * np.sqrt(max(correction, 0.0))

    if alternative == "less":
        p_value = float(stats.norm.cdf(dm_stat))
    elif alternative == "greater":
        p_value = float(stats.norm.sf(dm_stat))
    elif alternative == "two-sided":
        p_value = float(2 * stats.norm.sf(abs(dm_stat)))
    else:
        raise ValueError(
            f"alternative must be 'less', 'greater', or 'two-sided', got {alternative!r}"
        )

    return DMResult(
        statistic=float(dm_stat),
        p_value=p_value,
        n_obs=T,
        h=h,
        small_sample_corrected=small_sample_correction,
    )
