"""Tests for src/quant/backtest/statistics.py."""
from __future__ import annotations

import numpy as np
import pytest

from quant.backtest.statistics import DMResult, diebold_mariano


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

    def test_str_repr(self):
        e_a, e_b = _errors(100, 0), _errors(100, 1)
        result = diebold_mariano(e_a, e_b)
        s = str(result)
        assert "DM test" in s and "stat=" in s and "p=" in s
