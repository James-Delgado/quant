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

Paired block-bootstrap Sharpe-delta CI
--------------------------------------
``bootstrap_sharpe_delta_ci`` resamples blocks of *dates* shared by two
return series and computes the Sharpe difference per resample. Used by the
Phase 4A feature-ablation noise guard.

Reference:
  Politis, D.N., & Romano, J.P. (1994). The Stationary Bootstrap.
  Journal of the American Statistical Association, 89(428), 1303-1313.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from quant.backtest.metrics import compute_metrics


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

    if not (np.all(np.isfinite(errors_a)) and np.all(np.isfinite(errors_b))):
        raise ValueError(
            "errors_a and errors_b must not contain NaN or Inf — "
            "clean forecast errors before calling diebold_mariano"
        )

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
        # Harvey, Leybourne & Newbold (1997): scale statistic and use t(T-1).
        # HLN specifies t(T-1) as the reference distribution, not N(0,1).
        # Using Normal here would partially undo the correction's purpose.
        correction = (T + 1 - 2 * h + h * (h - 1) / T) / T
        dm_stat = dm_stat * np.sqrt(max(correction, 0.0))
        p_dist = stats.t(df=T - 1)
    else:
        p_dist = stats.norm

    if alternative == "less":
        p_value = float(p_dist.cdf(dm_stat))
    elif alternative == "greater":
        p_value = float(p_dist.sf(dm_stat))
    elif alternative == "two-sided":
        p_value = float(2 * p_dist.sf(abs(dm_stat)))
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


def _stationary_block_positions(
    rng: np.random.Generator,
    n: int,
    block_len: int,
) -> np.ndarray:
    """One stationary-bootstrap resample: positions 0..n-1, length n.

    Blocks start at a uniform random position, have geometric length with
    mean ``block_len`` (Politis & Romano 1994), and wrap circularly so every
    date is equally likely to be sampled.
    """
    positions = np.empty(n, dtype=np.intp)
    filled = 0
    while filled < n:
        start = int(rng.integers(0, n))
        length = min(int(rng.geometric(1.0 / block_len)), n - filled)
        positions[filled:filled + length] = (start + np.arange(length)) % n
        filled += length
    return positions


def bootstrap_sharpe_delta_ci(
    returns_variant: pd.Series,
    returns_baseline: pd.Series,
    *,
    block_len: int = 21,
    n_boot: int = 1000,
    ci: float = 0.90,
    seed: int = 0,
) -> tuple[float, float]:
    """Paired stationary block-bootstrap CI on the annualized Sharpe delta.

    Both series are aligned on their common index (inner join), then blocks
    of *dates* — ~21 trading days, the T1 convention from
    ``docs/concepts/evaluation-standards.md`` — are resampled with
    replacement until the original length is reached. Within each resampled
    block the pairing is kept intact: the same dates are drawn from both
    series. Pairing preserves the (typically very high) variant/baseline
    correlation, so the delta's sampling noise mostly cancels and the CI is
    far tighter than independent per-arm resampling would give.

    Per resample the statistic is ``sharpe(variant) − sharpe(baseline)``
    (annualized, via ``compute_metrics`` so the definition matches every
    other Sharpe in this codebase). The returned interval is the percentile
    interval, e.g. (5th, 95th) for ``ci=0.90``.

    Parameters
    ----------
    returns_variant, returns_baseline:
        Daily return series with date indices. NaNs are dropped before
        alignment.
    block_len:
        Mean block length in trading days (geometric distribution).
    n_boot:
        Number of bootstrap resamples.
    ci:
        Central coverage of the returned percentile interval.
    seed:
        Seed for ``np.random.default_rng`` — results are deterministic.

    Returns
    -------
    ``(ci_low, ci_high)`` floats.

    Raises
    ------
    ValueError if the two series have no overlapping observations, or on
    invalid ``block_len`` / ``n_boot`` / ``ci``.
    """
    if block_len < 1:
        raise ValueError(f"block_len must be >= 1, got {block_len}")
    if n_boot < 1:
        raise ValueError(f"n_boot must be >= 1, got {n_boot}")
    if not 0.0 < ci < 1.0:
        raise ValueError(f"ci must be in (0, 1), got {ci}")

    variant = returns_variant.dropna()
    baseline = returns_baseline.dropna()
    common_idx = variant.index.intersection(baseline.index)
    n = len(common_idx)
    if n == 0:
        raise ValueError(
            "returns_variant and returns_baseline have no overlapping "
            "observations — cannot compute a paired bootstrap"
        )

    v = variant.loc[common_idx].to_numpy(dtype=float)
    b = baseline.loc[common_idx].to_numpy(dtype=float)

    rng = np.random.default_rng(seed)
    deltas = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        pos = _stationary_block_positions(rng, n, block_len)
        sharpe_v = compute_metrics(pd.Series(v[pos]))["sharpe"]
        sharpe_b = compute_metrics(pd.Series(b[pos]))["sharpe"]
        deltas[i] = sharpe_v - sharpe_b

    alpha = (1.0 - ci) / 2.0
    lo, hi = np.quantile(deltas, [alpha, 1.0 - alpha])
    return float(lo), float(hi)
