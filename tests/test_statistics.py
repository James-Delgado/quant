"""Tests for src/quant/backtest/statistics.py."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest.statistics import (
    DMResult,
    bootstrap_sharpe_delta_ci,
    diebold_mariano,
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
