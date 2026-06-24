"""Tests for src/quant/backtest/statistics.py."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest.metrics import compute_metrics
from quant.backtest.statistics import (
    DEFAULT_SHARPE_STD,
    DSR_THRESHOLD,
    DMResult,
    DSRResult,
    SkillZResult,
    bootstrap_metric_delta_ci,
    bootstrap_sharpe_delta_ci,
    deflated_sharpe_ratio,
    diebold_mariano,
    expected_max_sharpe,
    forecast_skill_z,
)


def _errors(n: int = 100, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal(n)


class TestDieboldMariano:
    def test_returns_dm_result(self):
        e = _errors()
        result = diebold_mariano(e, e + 0.1)
        assert isinstance(result, DMResult)

    def test_better_model_low_p_value(self):
        """Model A with much smaller errors should yield p < 0.05."""
        rng = np.random.default_rng(1)
        e_a = rng.normal(0, 0.1, 200)
        e_b = rng.normal(0, 1.0, 200)
        result = diebold_mariano(e_a, e_b, alternative="less")
        assert result.p_value < 0.05

    def test_identical_errors_raises(self):
        """Identical errors → zero variance → ValueError."""
        e = _errors(200)
        with pytest.raises(ValueError, match="non-positive"):
            diebold_mariano(e, e, alternative="two-sided")

    def test_p_value_in_unit_interval(self):
        e_a, e_b = _errors(100, 0), _errors(100, 1)
        for alt in ("less", "greater", "two-sided"):
            r = diebold_mariano(e_a, e_b, alternative=alt)
            assert 0.0 <= r.p_value <= 1.0

    def test_n_obs_matches_input(self):
        e = _errors(150)
        result = diebold_mariano(e, e + 0.1)
        assert result.n_obs == 150

    def test_h_stored_in_result(self):
        e = _errors(100)
        result = diebold_mariano(e, e + 0.1, h=3)
        assert result.h == 3

    def test_small_sample_correction_changes_stat(self):
        e = _errors(100)
        r_corr = diebold_mariano(e, e + 0.1, small_sample_correction=True)
        r_uncorr = diebold_mariano(e, e + 0.1, small_sample_correction=False)
        assert r_corr.small_sample_corrected is True
        assert r_uncorr.small_sample_corrected is False
        assert r_corr.statistic != r_uncorr.statistic

    def test_shape_mismatch_raises(self):
        with pytest.raises(ValueError, match="same shape"):
            diebold_mariano(_errors(10), _errors(20))

    def test_2d_array_raises(self):
        e = _errors(10).reshape(2, 5)
        with pytest.raises(ValueError, match="1-D"):
            diebold_mariano(e, e)

    def test_too_few_obs_raises(self):
        with pytest.raises(ValueError, match="at least 4"):
            diebold_mariano(_errors(3), _errors(3))

    def test_h_zero_raises(self):
        with pytest.raises(ValueError, match="h must be >= 1"):
            diebold_mariano(_errors(10), _errors(10), h=0)

    def test_invalid_alternative_raises(self):
        with pytest.raises(ValueError, match="alternative"):
            diebold_mariano(_errors(10, seed=0), _errors(10, seed=1), alternative="bad")

    def test_nan_inputs_raise(self):
        e = _errors(20)
        e_nan = e.copy()
        e_nan[5] = float("nan")
        with pytest.raises(ValueError, match="NaN or Inf"):
            diebold_mariano(e_nan, e)

    def test_inf_inputs_raise(self):
        e = _errors(20)
        e_inf = e.copy()
        e_inf[3] = float("inf")
        with pytest.raises(ValueError, match="NaN or Inf"):
            diebold_mariano(e, e_inf)

    def test_str_repr(self):
        e_a, e_b = _errors(100, 0), _errors(100, 1)
        result = diebold_mariano(e_a, e_b)
        s = str(result)
        assert "DM test" in s and "stat=" in s and "p=" in s


# ─── bootstrap_sharpe_delta_ci ───────────────────────────────────────────────


def _returns_series(
    n: int,
    mean: float = 0.0005,
    vol: float = 0.01,
    seed: int = 0,
    start: str = "2015-01-02",
) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    return pd.Series(rng.normal(mean, vol, n), index=idx)


class TestBootstrapSharpeDeltaCI:
    def test_returns_tuple_low_le_high(self):
        base = _returns_series(300, seed=0)
        variant = base + _returns_series(300, mean=0.0, vol=0.002, seed=1).to_numpy()
        lo, hi = bootstrap_sharpe_delta_ci(variant, base, n_boot=200)
        assert isinstance(lo, float) and isinstance(hi, float)
        assert lo <= hi

    def test_identical_series_ci_centered_at_zero(self):
        base = _returns_series(300, seed=2)
        lo, hi = bootstrap_sharpe_delta_ci(base, base.copy(), n_boot=200)
        # Paired resampling of identical series → delta is exactly 0 in every
        # resample → degenerate (0, 0) interval.
        assert lo <= 0.0 <= hi
        assert hi - lo < 1e-12

    def test_constant_shift_ci_excludes_zero(self):
        base = _returns_series(500, seed=3)
        variant = base + 0.002
        lo, hi = bootstrap_sharpe_delta_ci(variant, base, n_boot=300)
        # A constant positive shift raises the mean without changing the std,
        # so every paired resample has a strictly positive Sharpe delta.
        assert lo > 0.0

    def test_pairing_tighter_than_shuffled(self):
        base = _returns_series(500, seed=4)
        variant = base + _returns_series(500, mean=0.0001, vol=0.002, seed=5).to_numpy()
        lo_p, hi_p = bootstrap_sharpe_delta_ci(variant, base, n_boot=300, seed=7)
        # Break the pairing: permute the baseline's values on the same index.
        rng = np.random.default_rng(6)
        shuffled = pd.Series(
            rng.permutation(base.to_numpy()), index=base.index
        )
        lo_s, hi_s = bootstrap_sharpe_delta_ci(variant, shuffled, n_boot=300, seed=7)
        assert (hi_s - lo_s) > (hi_p - lo_p), (
            "breaking the variant/baseline pairing should widen the CI"
        )

    def test_deterministic_same_seed(self):
        base = _returns_series(300, seed=8)
        variant = base + 0.0005
        ci_1 = bootstrap_sharpe_delta_ci(variant, base, n_boot=200, seed=42)
        ci_2 = bootstrap_sharpe_delta_ci(variant, base, n_boot=200, seed=42)
        assert ci_1 == ci_2

    def test_different_seed_close_but_not_identical(self):
        base = _returns_series(300, seed=9)
        variant = base + 0.0005
        ci_a = bootstrap_sharpe_delta_ci(variant, base, n_boot=500, seed=1)
        ci_b = bootstrap_sharpe_delta_ci(variant, base, n_boot=500, seed=2)
        assert ci_a != ci_b
        # Monte Carlo error only — endpoints should agree to well under
        # the constant shift's Sharpe lift (~0.8 annualized).
        assert abs(ci_a[0] - ci_b[0]) < 0.5
        assert abs(ci_a[1] - ci_b[1]) < 0.5

    def test_alignment_uses_common_index(self):
        base = _returns_series(300, seed=10)
        variant = (base + 0.002).iloc[50:]  # variant starts later
        lo, hi = bootstrap_sharpe_delta_ci(variant, base, n_boot=200)
        assert lo > 0.0  # still detects the shift on the overlap

    def test_empty_overlap_raises(self):
        a = _returns_series(50, seed=11, start="2010-01-04")
        b = _returns_series(50, seed=12, start="2020-01-06")
        with pytest.raises(ValueError, match="overlap"):
            bootstrap_sharpe_delta_ci(a, b)


# ─── expected_max_sharpe ─────────────────────────────────────────────────────


class TestExpectedMaxSharpe:
    def test_no_multiple_testing_returns_zero(self):
        """N <= 1 means no selection bias — benchmark collapses to 0."""
        assert expected_max_sharpe(0) == 0.0
        assert expected_max_sharpe(1) == 0.0

    def test_monotonic_increasing_in_n(self):
        """More trials searched → a higher expected best-of-N Sharpe."""
        vals = [expected_max_sharpe(n) for n in (2, 5, 20, 62, 200)]
        assert all(b > a for a, b in zip(vals, vals[1:]))

    def test_scales_linearly_with_sharpe_std(self):
        a = expected_max_sharpe(50, sharpe_std=0.2)
        b = expected_max_sharpe(50, sharpe_std=0.4)
        assert b == pytest.approx(2 * a)

    def test_zero_dispersion_gives_zero_benchmark(self):
        assert expected_max_sharpe(62, sharpe_std=0.0) == 0.0

    def test_known_value_phase4a_n62(self):
        """At the Phase 4A ledger N≈62 and the §7 0.35 dispersion estimate, the
        Bailey-LdP expected-max benchmark is ~0.83 annualized (the report's
        rougher √(2 ln N) sketch said ~0.71; both far exceed the +0.177 best
        GBM arm, so the verdict is identical)."""
        assert expected_max_sharpe(62, sharpe_std=0.35) == pytest.approx(0.825, abs=0.02)

    def test_negative_dispersion_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            expected_max_sharpe(50, sharpe_std=-0.1)


# ─── deflated_sharpe_ratio ───────────────────────────────────────────────────


class TestDeflatedSharpeRatio:
    def test_returns_dsr_result(self):
        r = _returns_series(300, mean=0.001, vol=0.01, seed=0)
        res = deflated_sharpe_ratio(r, n_trials=1)
        assert isinstance(res, DSRResult)

    def test_dsr_in_unit_interval(self):
        r = _returns_series(300, seed=1)
        res = deflated_sharpe_ratio(r, n_trials=10)
        assert 0.0 <= res.dsr <= 1.0

    def test_strong_edge_low_n_passes(self):
        """A strong positive-drift series vs a zero benchmark (N=1) clears DSR."""
        r = _returns_series(500, mean=0.001, vol=0.01, seed=2)
        res = deflated_sharpe_ratio(r, n_trials=1)
        assert res.sr_benchmark == 0.0
        assert res.dsr > 0.5
        assert res.passed is True

    def test_deflation_monotonic_in_n(self):
        """Holding the series fixed, raising N raises the benchmark and lowers DSR."""
        r = _returns_series(500, mean=0.0008, vol=0.01, seed=3)
        dsr_low = deflated_sharpe_ratio(r, n_trials=2).dsr
        dsr_high = deflated_sharpe_ratio(r, n_trials=500).dsr
        assert dsr_high < dsr_low

    def test_large_n_can_fail_a_modest_edge(self):
        """A modest edge that passes at N=1 fails once deflated by many trials."""
        r = _returns_series(400, mean=0.0004, vol=0.01, seed=4)
        assert deflated_sharpe_ratio(r, n_trials=1).passed is True
        assert deflated_sharpe_ratio(r, n_trials=100_000).passed is False

    def test_zero_edge_does_not_pass(self):
        r = _returns_series(500, mean=0.0, vol=0.01, seed=5)
        res = deflated_sharpe_ratio(r, n_trials=10)
        assert res.passed is False

    def test_sr_observed_matches_compute_metrics(self):
        """The reported observed Sharpe is the project's annualized convention."""
        r = _returns_series(300, mean=0.0007, vol=0.01, seed=6)
        res = deflated_sharpe_ratio(r, n_trials=5)
        assert res.sr_observed == pytest.approx(compute_metrics(r)["sharpe"])

    def test_kurtosis_is_non_excess(self):
        """The stored γ₄ is non-excess (pandas excess + 3)."""
        r = _returns_series(400, seed=7)
        res = deflated_sharpe_ratio(r, n_trials=5)
        assert res.kurtosis == pytest.approx(float(r.kurtosis()) + 3.0)

    def test_custom_threshold_changes_verdict(self):
        r = _returns_series(500, mean=0.0006, vol=0.01, seed=8)
        loose = deflated_sharpe_ratio(r, n_trials=1, threshold=0.5)
        strict = deflated_sharpe_ratio(r, n_trials=1, threshold=0.999999)
        assert loose.passed is True
        assert strict.passed is False

    def test_drops_nans(self):
        r = _returns_series(300, mean=0.001, seed=9)
        r.iloc[::10] = np.nan
        res = deflated_sharpe_ratio(r, n_trials=2)
        assert res.n_obs == int(r.notna().sum())

    def test_too_few_observations_raises(self):
        r = pd.Series([0.01], index=pd.bdate_range("2020-01-02", periods=1))
        with pytest.raises(ValueError, match=">= 2"):
            deflated_sharpe_ratio(r, n_trials=1)

    def test_defaults_are_pinned_constants(self):
        """Guard the pinned §1 defaults against silent drift."""
        assert DSR_THRESHOLD == 0.5
        assert DEFAULT_SHARPE_STD == 0.35


# ─── bootstrap_metric_delta_ci ───────────────────────────────────────────────


def _sharpe_metric(a: np.ndarray) -> float:
    return compute_metrics(pd.Series(a))["sharpe"]


def _auc_metric(a: np.ndarray) -> float:
    """ROC-AUC on column-stacked [y_true, score], raising on a single class.

    sklearn's ``roc_auc_score`` returns NaN + warns on a single-class input;
    raising ``ValueError`` instead exercises the documented ``metric_fn``
    contract (the gate's significance bootstrap drops such resamples) and keeps
    the test output free of ``UndefinedMetricWarning`` noise.
    """
    from sklearn.metrics import roc_auc_score

    y_true = a[:, 0]
    if len(np.unique(y_true)) < 2:
        raise ValueError("only one class present in this resample")
    return roc_auc_score(y_true, a[:, 1])


class TestBootstrapMetricDeltaCI:
    def test_returns_tuple_low_le_high(self):
        rng = np.random.default_rng(0)
        variant = rng.normal(0.001, 0.01, size=300)
        baseline = rng.normal(0.0, 0.01, size=300)
        lo, hi = bootstrap_metric_delta_ci(
            variant, baseline, _sharpe_metric, n_boot=200
        )
        assert lo <= hi

    def test_identical_series_brackets_zero(self):
        base = np.random.default_rng(1).normal(0.0, 0.01, size=300)
        lo, hi = bootstrap_metric_delta_ci(
            base, base.copy(), _sharpe_metric, n_boot=200
        )
        assert lo <= 0.0 <= hi

    def test_constant_shift_excludes_zero(self):
        base = np.random.default_rng(2).normal(0.0, 0.005, size=300)
        variant = base + 0.01  # uniformly higher returns -> higher Sharpe
        lo, hi = bootstrap_metric_delta_ci(
            variant, base, _sharpe_metric, n_boot=300
        )
        assert lo > 0.0

    def test_deterministic_same_seed(self):
        rng = np.random.default_rng(4)
        variant = rng.normal(0.001, 0.01, size=200)
        baseline = rng.normal(0.0, 0.01, size=200)
        ci_a = bootstrap_metric_delta_ci(
            variant, baseline, _sharpe_metric, n_boot=200, seed=42
        )
        ci_b = bootstrap_metric_delta_ci(
            variant, baseline, _sharpe_metric, n_boot=200, seed=42
        )
        assert ci_a == ci_b

    def test_2d_auc_variant_better_positive_ci(self):
        rng = np.random.default_rng(5)
        y_true = np.array([0.0, 1.0] * 40)  # balanced, well mixed
        score_variant = y_true + rng.normal(0.0, 0.2, size=80)  # separates classes
        score_baseline = rng.normal(0.0, 1.0, size=80)  # ~chance

        variant = np.column_stack([y_true, score_variant])
        baseline = np.column_stack([y_true, score_baseline])

        lo, hi = bootstrap_metric_delta_ci(
            variant, baseline, _auc_metric, n_boot=300, seed=0
        )
        assert lo > 0.0  # ΔAUC well above zero

    def test_single_class_raises(self):
        y_true = np.zeros(80)  # one class only -> AUC undefined everywhere
        score = np.random.default_rng(6).normal(size=80)
        variant = np.column_stack([y_true, score])
        baseline = np.column_stack([y_true, score])

        with pytest.raises(ValueError, match="every bootstrap resample"):
            bootstrap_metric_delta_ci(variant, baseline, _auc_metric, n_boot=100)

    def test_partial_failures_warn(self):
        # Minority class clustered at the first 3 of 80 rows; with block_len=1
        # many resamples miss it (single class -> dropped), some include it.
        y_true = np.zeros(80)
        y_true[:3] = 1.0
        score = np.random.default_rng(7).normal(size=80)
        variant = np.column_stack([y_true, score])
        baseline = np.column_stack([y_true, score])

        with pytest.warns(UserWarning, match="bootstrap resamples dropped"):
            lo, hi = bootstrap_metric_delta_ci(
                variant, baseline, _auc_metric, block_len=1, n_boot=300, seed=0
            )
        assert lo <= hi

    def test_shape_mismatch_raises(self):
        with pytest.raises(ValueError, match="same shape"):
            bootstrap_metric_delta_ci(
                np.zeros(10), np.zeros(11), _sharpe_metric
            )

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="no observations"):
            bootstrap_metric_delta_ci(
                np.array([]), np.array([]), _sharpe_metric
            )

    def test_3d_raises(self):
        with pytest.raises(ValueError, match="1-D or 2-D"):
            bootstrap_metric_delta_ci(
                np.zeros((4, 2, 2)), np.zeros((4, 2, 2)), _sharpe_metric
            )

    def test_invalid_params_raise(self):
        a = np.zeros(10)
        with pytest.raises(ValueError, match="block_len"):
            bootstrap_metric_delta_ci(a, a, _sharpe_metric, block_len=0)
        with pytest.raises(ValueError, match="n_boot"):
            bootstrap_metric_delta_ci(a, a, _sharpe_metric, n_boot=0)
        with pytest.raises(ValueError, match="ci must be"):
            bootstrap_metric_delta_ci(a, a, _sharpe_metric, ci=1.5)


class TestForecastSkillZ:
    """Skill-z deflation analog for the non-tradeable B1 targets (T1/T2)."""

    def test_positive_mean_skill_passes(self):
        rng = np.random.default_rng(0)
        skill = rng.normal(0.05, 0.10, 500)  # variant reliably beats baseline
        res = forecast_skill_z(skill)
        assert isinstance(res, SkillZResult)
        assert res.z > 0
        assert res.passed
        assert res.n_obs == 500
        assert res.mean_skill == pytest.approx(float(np.mean(skill)))

    def test_negative_mean_skill_fails(self):
        rng = np.random.default_rng(1)
        skill = rng.normal(-0.05, 0.10, 500)
        res = forecast_skill_z(skill)
        assert res.z < 0
        assert not res.passed

    def test_noise_around_zero_does_not_pass(self):
        rng = np.random.default_rng(2)
        skill = rng.normal(0.0, 1.0, 2000)  # no real skill
        res = forecast_skill_z(skill)
        assert abs(res.z) < 3  # not a confident positive
        # passed only if z>0 by chance and small; assert it's not a strong claim
        assert not (res.passed and res.z > 2)

    def test_zero_dispersion_positive_is_inf_pass(self):
        res = forecast_skill_z(np.full(50, 0.2))
        assert res.z == float("inf")
        assert res.passed
        assert res.se_skill == 0.0

    def test_zero_dispersion_negative_is_neg_inf_fail(self):
        res = forecast_skill_z(np.full(50, -0.2))
        assert res.z == float("-inf")
        assert not res.passed

    def test_zero_dispersion_at_threshold_is_zero_fail(self):
        res = forecast_skill_z(np.zeros(50))
        assert res.z == 0.0
        assert not res.passed

    def test_threshold_argument(self):
        # mean skill 0.1, threshold 0.2 → should fail at the higher bar
        rng = np.random.default_rng(3)
        skill = rng.normal(0.1, 0.001, 1000)  # tightly around 0.1
        assert forecast_skill_z(skill, threshold=0.0).passed
        assert not forecast_skill_z(skill, threshold=float("inf")).passed

    def test_nans_dropped(self):
        skill = np.array([0.1, np.nan, 0.2, 0.15, np.nan, 0.05])
        res = forecast_skill_z(skill)
        assert res.n_obs == 4

    def test_fewer_than_two_obs_raises(self):
        with pytest.raises(ValueError, match=">= 2 non-NaN"):
            forecast_skill_z(np.array([0.1]))
        with pytest.raises(ValueError, match=">= 2 non-NaN"):
            forecast_skill_z(np.array([0.1, np.nan]))

    def test_accepts_pandas_series(self):
        res = forecast_skill_z(pd.Series([0.1, 0.2, 0.15, 0.05]))
        assert res.n_obs == 4
        assert res.passed
