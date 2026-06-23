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
    bootstrap_sharpe_delta_ci,
    deflated_sharpe_ratio,
    diebold_mariano,
    expected_max_sharpe,
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
