"""Tests for src/quant/models/arima_baseline.py and buyandhold_baseline.py."""
from __future__ import annotations

import numpy as np
import pytest

from quant.models.arima_baseline import ARIMABaseline
from quant.models.buyandhold_baseline import BuyAndHoldBaseline


def _ar1_series(n: int = 100, phi: float = 0.5, seed: int = 0) -> np.ndarray:
    """AR(1) series with known autocorrelation — ARIMA should fit well."""
    rng = np.random.default_rng(seed)
    y = np.zeros(n)
    y[0] = rng.standard_normal()
    for t in range(1, n):
        y[t] = phi * y[t - 1] + rng.standard_normal()
    return y


class TestARIMABaseline:
    def test_fit_predict_shapes(self):
        y = _ar1_series(80)
        model = ARIMABaseline()
        model.fit(np.zeros((80, 3)), y)
        preds = model.predict(np.zeros((20, 3)))
        assert preds.shape == (20,)

    def test_predict_returns_floats(self):
        y = _ar1_series(60)
        model = ARIMABaseline()
        model.fit(np.zeros((60, 2)), y)
        preds = model.predict(np.zeros((10, 2)))
        assert preds.dtype == float

    def test_predict_empty_test_window(self):
        y = _ar1_series(60)
        model = ARIMABaseline()
        model.fit(np.zeros((60, 2)), y)
        preds = model.predict(np.zeros((0, 2)))
        assert preds.shape == (0,)

    def test_predict_before_fit_raises(self):
        model = ARIMABaseline()
        with pytest.raises(RuntimeError, match="fit\\(\\) must be called"):
            model.predict(np.zeros((5, 2)))

    def test_predict_one_step_before_fit_raises(self):
        model = ARIMABaseline()
        with pytest.raises(RuntimeError, match="fit\\(\\) must be called"):
            model.predict_one_step()

    def test_predict_one_step_returns_scalar(self):
        y = _ar1_series(60)
        model = ARIMABaseline()
        model.fit(np.zeros((60, 2)), y)
        val = model.predict_one_step()
        assert isinstance(val, float)

    def test_too_short_series_raises(self):
        with pytest.raises(ValueError, match="too short"):
            ARIMABaseline().fit(np.zeros((2, 1)), np.array([1.0, 2.0]))

    def test_custom_order_accepted(self):
        y = _ar1_series(80)
        model = ARIMABaseline(order=(0, 1, 1))
        model.fit(np.zeros((80, 1)), y)
        preds = model.predict(np.zeros((5, 1)))
        assert len(preds) == 5

    def test_fit_does_not_use_X(self):
        """Two fits with different X but same y must produce identical forecasts."""
        y = _ar1_series(80)
        m1 = ARIMABaseline()
        m2 = ARIMABaseline()
        m1.fit(np.zeros((80, 3)), y)
        m2.fit(np.ones((80, 3)), y)
        p1 = m1.predict(np.zeros((5, 3)))
        p2 = m2.predict(np.zeros((5, 3)))
        np.testing.assert_array_almost_equal(p1, p2)

    def test_single_fit_protocol_no_refit(self):
        """predict() must not re-fit — same fitted model must return same forecasts
        regardless of what X is passed."""
        y = _ar1_series(80)
        model = ARIMABaseline()
        model.fit(np.zeros((80, 2)), y)
        p1 = model.predict(np.zeros((10, 2)))
        p2 = model.predict(np.ones((10, 2)))
        np.testing.assert_array_equal(p1, p2)


class TestBuyAndHoldBaseline:
    def test_predict_all_ones(self):
        model = BuyAndHoldBaseline()
        model.fit(np.zeros((10, 3)), np.zeros(10))
        preds = model.predict(np.zeros((20, 3)))
        np.testing.assert_array_equal(preds, np.ones(20))

    def test_fit_is_noop(self):
        m1 = BuyAndHoldBaseline()
        m2 = BuyAndHoldBaseline()
        m1.fit(np.zeros((5, 2)), np.array([-1.0] * 5))
        m2.fit(np.ones((5, 2)), np.array([1.0] * 5))
        np.testing.assert_array_equal(
            m1.predict(np.zeros((3, 2))), m2.predict(np.zeros((3, 2)))
        )

    def test_predict_empty_returns_empty(self):
        model = BuyAndHoldBaseline()
        model.fit(np.zeros((5, 1)), np.zeros(5))
        preds = model.predict(np.zeros((0, 1)))
        assert preds.shape == (0,)

    def test_predict_dtype_float(self):
        model = BuyAndHoldBaseline()
        model.fit(np.zeros((5, 1)), np.zeros(5))
        assert model.predict(np.zeros((3, 1))).dtype == float
