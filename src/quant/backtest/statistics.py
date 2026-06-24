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

Deflated Sharpe Ratio (Bailey & López de Prado)
-----------------------------------------------
``expected_max_sharpe`` is the expected best-of-N Sharpe a no-skill researcher
obtains by trying N strategies; ``deflated_sharpe_ratio`` is the Probabilistic
Sharpe Ratio measured against that benchmark, the second stage of the
``regime_metrics.dsr_aware_gate_report`` gate (METHODOLOGY §13). The trial
count N comes from ``quant.ledger.cumulative_trial_count`` — see
``docs/concepts/evaluation-standards.md`` T4 for the pinned ``DSR > 0.5`` rule.

Reference:
  Bailey, D.H., & López de Prado, M. (2014). The Deflated Sharpe Ratio:
  Correcting for Selection Bias, Backtest Overfitting, and Non-Normality.
  Journal of Portfolio Management, 40(5), 94-107.
  López de Prado, M. (2018). Advances in Financial Machine Learning, ch. 14.
"""
from __future__ import annotations

import warnings
from collections.abc import Callable
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


def bootstrap_metric_delta_ci(
    variant: np.ndarray,
    baseline: np.ndarray,
    metric_fn: Callable[[np.ndarray], float],
    *,
    block_len: int = 21,
    n_boot: int = 1000,
    ci: float = 0.90,
    seed: int = 0,
) -> tuple[float, float]:
    """Paired stationary block-bootstrap CI on an arbitrary metric delta.

    Generalises ``bootstrap_sharpe_delta_ci`` from Sharpe to any per-observation
    metric — ROC-AUC, MAE, Sharpe — for the B1 gate's significance stage
    (METHODOLOGY §10, the B1 PRD "Significance" column). ``variant`` and
    ``baseline`` are per-observation arrays sharing row order; the same resampled
    *rows* are drawn from both (paired), and per resample the statistic is
    ``metric_fn(variant[rows]) - metric_fn(baseline[rows])``. The returned
    interval is the percentile interval (e.g. 5th, 95th for ``ci=0.90``).

    Both arrays may be 1-D (e.g. a return series for Sharpe) or 2-D (e.g.
    ``column_stack([y_true, y_score])`` for AUC, ``column_stack([y_true, pred])``
    for MAE) — the first axis is always the observation axis. Carrying ``y_true``
    in a column of *both* arrays keeps the metrics commensurable: each resample
    scores the variant and the baseline against the identically resampled truth.

    The CI is computed on ``metric_fn(variant) - metric_fn(baseline)``; the gate's
    significance test is "the interval excludes 0", which is sign-agnostic, so the
    caller may order the arguments either way.

    Parameters
    ----------
    variant, baseline:
        Per-observation arrays, identical shape. Rows are observations.
    metric_fn:
        Maps a resampled array (same column layout as the inputs) to a scalar.
    block_len, n_boot, ci, seed:
        As ``bootstrap_sharpe_delta_ci``.

    Returns
    -------
    ``(ci_low, ci_high)`` floats.

    Raises
    ------
    ValueError on invalid ``block_len`` / ``n_boot`` / ``ci``, on shape mismatch,
    on an empty observation axis, or when *every* resample's ``metric_fn`` call
    failed (e.g. AUC undefined because a resample drew a single class throughout).
    Individual failed resamples are dropped with a warning rather than aborting —
    a known case for ROC-AUC on imbalanced labels.
    """
    if block_len < 1:
        raise ValueError(f"block_len must be >= 1, got {block_len}")
    if n_boot < 1:
        raise ValueError(f"n_boot must be >= 1, got {n_boot}")
    if not 0.0 < ci < 1.0:
        raise ValueError(f"ci must be in (0, 1), got {ci}")

    variant = np.asarray(variant, dtype=float)
    baseline = np.asarray(baseline, dtype=float)
    if variant.shape != baseline.shape:
        raise ValueError(
            f"variant and baseline must have the same shape, "
            f"got {variant.shape} and {baseline.shape}"
        )
    if variant.ndim not in (1, 2):
        raise ValueError(f"variant/baseline must be 1-D or 2-D, got {variant.ndim}-D")
    n = variant.shape[0]
    if n == 0:
        raise ValueError("variant/baseline have no observations")

    rng = np.random.default_rng(seed)
    deltas = np.full(n_boot, np.nan, dtype=float)
    n_failed = 0
    for i in range(n_boot):
        pos = _stationary_block_positions(rng, n, block_len)
        try:
            value = metric_fn(variant[pos]) - metric_fn(baseline[pos])
        except ValueError:
            # metric raised on this resample (e.g. a metric_fn that raises on a
            # single-class AUC) — drop it rather than abort the whole CI.
            n_failed += 1
            continue
        # Some metrics (e.g. sklearn roc_auc_score on a single-class resample)
        # return NaN + warn instead of raising; treat non-finite as a drop too.
        if np.isfinite(value):
            deltas[i] = value
        else:
            n_failed += 1

    finite = deltas[np.isfinite(deltas)]
    if finite.size == 0:
        raise ValueError(
            "every bootstrap resample's metric_fn failed — the metric is "
            "undefined on this data (e.g. a single-class target)"
        )
    if n_failed:
        warnings.warn(
            f"{n_failed}/{n_boot} bootstrap resamples dropped (metric_fn raised "
            "ValueError, e.g. single-class AUC); CI computed on the remainder",
            stacklevel=2,
        )

    alpha = (1.0 - ci) / 2.0
    lo, hi = np.quantile(finite, [alpha, 1.0 - alpha])
    return float(lo), float(hi)


# ─── Deflated Sharpe Ratio (Bailey & López de Prado 2014) ────────────────────

# Euler-Mascheroni constant — appears in the expected-maximum-of-N-Gaussians
# estimator below.
EULER_MASCHERONI = 0.5772156649015329

# Annualisation factor — must match metrics._TRADING_DAYS (252 for equity).
TRADING_DAYS = 252

# Default cross-trial Sharpe dispersion V̂[{SR_n}]^(1/2) (annualised) feeding the
# expected-max benchmark. 0.35 is the rough M3 noise-guard estimate quoted in
# docs/PHASE_4A_REPORT.md §7; overridable per call. PINNED (METHODOLOGY §1) — do
# not retune it to make a result pass.
DEFAULT_SHARPE_STD = 0.35

# Gate threshold for the deflated-Sharpe second stage. DSR is a probability in
# [0, 1]; DSR > 0.5 is exactly equivalent to "observed Sharpe exceeds the
# best-of-N benchmark" (the DSR numerator changes sign at SR̂ = SR₀), which is
# both METHODOLOGY §13's "deflated (excess) Sharpe > 0" and
# evaluation-standards.md T4's "DSR > 0.5". PINNED (METHODOLOGY §1).
DSR_THRESHOLD = 0.5


@dataclass(frozen=True)
class DSRResult:
    """Result of a Deflated Sharpe Ratio computation.

    All Sharpe fields are in the project's **annualised** convention (matching
    ``compute_metrics``); the per-observation conversion the formula needs is
    internal to ``deflated_sharpe_ratio``.
    """

    dsr: float                  # probability in [0, 1] — the deflated Sharpe ratio
    sr_observed: float          # observed annualised Sharpe of the return series
    sr_benchmark: float         # expected best-of-N annualised Sharpe under the null
    n_trials: int               # N — the deflation trial count
    n_obs: int                  # T — number of return observations
    skew: float                 # skewness of the returns (γ₃)
    kurtosis: float             # NON-excess kurtosis of the returns (γ₄; normal = 3)
    sharpe_std: float           # annualised cross-trial Sharpe dispersion used
    threshold: float            # DSR pass threshold
    passed: bool                # dsr > threshold

    def __str__(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        return (
            f"DSR={self.dsr:.4f} ({verdict} vs {self.threshold:.2f}): "
            f"SR_obs={self.sr_observed:.3f} vs SR_benchmark={self.sr_benchmark:.3f} "
            f"(N={self.n_trials}, T={self.n_obs})"
        )


def expected_max_sharpe(
    n_trials: int,
    sharpe_std: float = DEFAULT_SHARPE_STD,
) -> float:
    """Expected maximum Sharpe ratio under the null of no skill across N trials.

    Bailey & López de Prado (2014) / AFML (2018) ch. 14 estimator of the best
    Sharpe a no-skill researcher would obtain by trying ``n_trials`` independent
    strategies whose Sharpe estimates have cross-sectional standard deviation
    ``sharpe_std``::

        E[max SR] ≈ sharpe_std · [ (1 − γ)·Z⁻¹(1 − 1/N) + γ·Z⁻¹(1 − 1/(N·e)) ]

    where γ is the Euler-Mascheroni constant and Z⁻¹ is the inverse standard
    normal CDF. The result is in the **same units** as ``sharpe_std``
    (annualised in → annualised out).

    For ``n_trials <= 1`` there is no multiple-testing selection, so the
    benchmark collapses to ``0.0`` (a plain Sharpe-vs-zero test).
    """
    if sharpe_std < 0:
        raise ValueError(f"sharpe_std must be non-negative, got {sharpe_std}")
    if n_trials <= 1:
        return 0.0
    n = float(n_trials)
    z1 = float(stats.norm.ppf(1.0 - 1.0 / n))
    z2 = float(stats.norm.ppf(1.0 - 1.0 / (n * np.e)))
    gamma = EULER_MASCHERONI
    return float(sharpe_std * ((1.0 - gamma) * z1 + gamma * z2))


def deflated_sharpe_ratio(
    returns: pd.Series,
    n_trials: int,
    *,
    sharpe_std: float = DEFAULT_SHARPE_STD,
    trading_days: int = TRADING_DAYS,
    threshold: float = DSR_THRESHOLD,
) -> DSRResult:
    """Deflated Sharpe Ratio (Bailey & López de Prado, 2014) for one return series.

    The DSR is the Probabilistic Sharpe Ratio measured against a
    multiple-testing benchmark — the probability that the strategy's true Sharpe
    exceeds the expected best-of-``n_trials`` Sharpe a no-skill search would have
    produced, adjusting for sample length and the return distribution's
    skew/kurtosis::

        DSR = Φ[ (SR̂ − SR₀)·√(T−1) / √(1 − γ₃·SR̂ + ((γ₄−1)/4)·SR̂²) ]

    Here SR̂ and SR₀ are the **per-observation** (non-annualised) observed Sharpe
    and the ``expected_max_sharpe`` benchmark, T is the number of returns, γ₃ is
    skewness, and γ₄ is the **non-excess** kurtosis (normal = 3). Inputs and the
    reported Sharpe fields use the project's annualised convention
    (``compute_metrics``); the ÷√trading_days conversion is internal.

    Parameters
    ----------
    returns:      Daily arithmetic return series. NaNs are dropped.
    n_trials:     N — the deflation trial count (typically
                  ``quant.ledger.cumulative_trial_count()``).
    sharpe_std:   Annualised cross-trial Sharpe dispersion (default
                  ``DEFAULT_SHARPE_STD``).
    trading_days: Annualisation factor (default 252).
    threshold:    DSR pass threshold (default ``DSR_THRESHOLD`` = 0.5).

    Returns
    -------
    ``DSRResult`` — ``.passed`` is ``dsr > threshold``.

    Raises
    ------
    ValueError if fewer than 2 non-NaN observations remain (DSR is undefined).
    """
    r = returns.dropna()
    n_obs = len(r)
    if n_obs < 2:
        raise ValueError(
            f"deflated_sharpe_ratio needs >= 2 non-NaN observations, got {n_obs}"
        )

    ann = float(trading_days)
    sr_ann = compute_metrics(r)["sharpe"]
    sr_obs = sr_ann / np.sqrt(ann)  # per-observation Sharpe

    # pandas .skew()/.kurtosis() are undefined (NaN) on near-degenerate samples
    # (constant series, n < 3/4). Fall back to the normal moments so the DSR is
    # still defined — a flat series carries no skew/excess-kurtosis information.
    skew = float(r.skew())
    excess_kurt = float(r.kurtosis())  # pandas returns EXCESS (Fisher) kurtosis
    if not np.isfinite(skew):
        skew = 0.0
    if not np.isfinite(excess_kurt):
        excess_kurt = 0.0
    kurt = excess_kurt + 3.0  # non-excess kurtosis (normal = 3) for the formula

    sr0_ann = expected_max_sharpe(n_trials, sharpe_std)
    sr0_obs = sr0_ann / np.sqrt(ann)

    denom_var = 1.0 - skew * sr_obs + ((kurt - 1.0) / 4.0) * sr_obs ** 2
    if denom_var <= 0:
        # Extreme skew/kurtosis can drive the variance estimate non-positive.
        # Clamp (loudly — never silently) so the gate degrades to a conservative
        # verdict rather than crashing or NaN-propagating (METHODOLOGY §9).
        warnings.warn(
            f"DSR variance estimate non-positive ({denom_var:.4g}); "
            "clamping to a tiny positive value — DSR is unreliable here",
            stacklevel=2,
        )
        denom_var = 1e-12

    dsr_stat = (sr_obs - sr0_obs) * np.sqrt(n_obs - 1) / np.sqrt(denom_var)
    dsr = float(stats.norm.cdf(dsr_stat))

    return DSRResult(
        dsr=dsr,
        sr_observed=float(sr_ann),
        sr_benchmark=float(sr0_ann),
        n_trials=int(n_trials),
        n_obs=int(n_obs),
        skew=skew,
        kurtosis=kurt,
        sharpe_std=float(sharpe_std),
        threshold=float(threshold),
        passed=bool(dsr > threshold),
    )


# ─── Forecast-skill z-score (the DSR analog for non-tradeable targets) ────────


@dataclass(frozen=True)
class SkillZResult:
    """Result of a forecast-skill z-score test.

    The deflation second stage (METHODOLOGY §13) for the B1 targets that have **no
    tradeable return series** — T1 drawdown probability and T2 log-realized-vol —
    where the Bailey-López de Prado DSR is undefined. The skill statistic is a paired
    per-observation improvement of the variant model over its better baseline (a
    larger positive ``skill_i`` = the variant predicted observation ``i`` better),
    and the test is whether the *mean* improvement is reliably positive::

        z = mean(skill) / se(skill),   se(skill) = std(skill, ddof=1) / sqrt(n)

    ``passed`` is ``z > threshold`` (default 0) — the analog of "deflated Sharpe > 0".
    """

    z: float
    mean_skill: float
    se_skill: float
    n_obs: int
    threshold: float
    passed: bool

    def __str__(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        return (
            f"skill-z={self.z:.4f} ({verdict} vs {self.threshold:.2f}): "
            f"mean={self.mean_skill:.4g}, se={self.se_skill:.4g}, n={self.n_obs}"
        )


def forecast_skill_z(
    skill: np.ndarray | pd.Series,
    *,
    threshold: float = 0.0,
) -> SkillZResult:
    """Forecast-skill z-score: is the mean per-observation skill reliably positive?

    ``skill`` is a per-observation improvement series — for a regression target,
    ``|baseline_error| - |variant_error|`` per bar; for a probability target, the
    Brier-score improvement ``(baseline_prob - y)^2 - (variant_prob - y)^2`` per bar
    — so a positive value means the variant beat the baseline on that observation.
    The test computes ``z = mean / standard-error`` and passes when ``z > threshold``
    (default 0). This is the ``spec.deflation == "skill_z"`` stage the B1 gate
    consumes for the non-tradeable T1/T2 targets (the DSR is for the directional
    Sharpe arms).

    NaNs are dropped. With ``n >= 2`` and a positive dispersion the z-score is the
    usual one-sample statistic. If every retained skill value is identical (zero
    dispersion) the z-score is ``+inf`` when the constant mean exceeds ``threshold``,
    ``-inf`` when it is below, and ``0`` when it equals ``threshold`` — so a perfectly
    consistent improvement passes and a perfectly consistent non-improvement fails,
    without a divide-by-zero.

    Raises
    ------
    ValueError if fewer than 2 non-NaN observations remain (the standard error is
    undefined).
    """
    s = np.asarray(skill, dtype=float)
    s = s[np.isfinite(s)]
    n = int(s.size)
    if n < 2:
        raise ValueError(
            f"forecast_skill_z needs >= 2 non-NaN observations, got {n}"
        )

    mean_skill = float(s.mean())

    if np.ptp(s) == 0.0:
        # Zero dispersion: every observation has the identical skill value (ptp is
        # exactly 0 even when std(ddof=1) carries float-rounding noise). The z-score
        # limit is ±inf by the sign of (mean - threshold); 0 on a tie.
        se = 0.0
        if mean_skill > threshold:
            z = float("inf")
        elif mean_skill < threshold:
            z = float("-inf")
        else:
            z = 0.0
    else:
        se = float(s.std(ddof=1)) / np.sqrt(n)
        z = mean_skill / se

    return SkillZResult(
        z=float(z),
        mean_skill=mean_skill,
        se_skill=float(se),
        n_obs=n,
        threshold=float(threshold),
        passed=bool(z > threshold),
    )
