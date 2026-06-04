"""Tests for src/quant/models/arima_baseline.py, buyandhold_baseline.py, and gbm.py."""
from __future__ import annotations

import numpy as np
import pytest

from quant.models.arima_baseline import ARIMABaseline
from quant.models.buyandhold_baseline import BuyAndHoldBaseline
from quant.models.gbm import GBMModel


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


def _make_xy(n: int = 50, n_features: int = 4, seed: int = 42):
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((n, n_features))
    y = rng.standard_normal(n)
    return X, y


class TestGBMModel:
    """All tests use n_iter=2, n_splits=2 for speed — correctness only."""

    def _fast_model(self, label_horizon: int = 1) -> GBMModel:
        return GBMModel(label_horizon=label_horizon, n_iter=2, n_splits=2, random_state=0)

    def test_fit_predict_shapes(self):
        X, y = _make_xy(30)
        model = self._fast_model()
        model.fit(X, y)
        preds = model.predict(X[:10])
        assert preds.shape == (10,)

    def test_predict_returns_floats(self):
        X, y = _make_xy(30)
        model = self._fast_model()
        model.fit(X, y)
        preds = model.predict(X[:5])
        assert preds.dtype == float

    def test_predict_continuous_not_discrete(self):
        """predict() must return raw floats, not discretized {-1, 0, +1}."""
        X, y = _make_xy(50)
        model = self._fast_model()
        model.fit(X, y)
        preds = model.predict(X)
        unique_vals = np.unique(np.round(preds, 6))
        assert len(unique_vals) > 3, (
            "predict() returned only discrete values — must return raw regression output"
        )

    def test_predict_before_fit_raises(self):
        model = self._fast_model()
        with pytest.raises(RuntimeError, match="fit\\(\\) must be called"):
            model.predict(np.zeros((5, 4)))

    def test_feature_importances_shape(self):
        X, y = _make_xy(30, n_features=4)
        model = self._fast_model()
        model.fit(X, y)
        fi = model.feature_importances_
        assert fi.shape == (4,)

    def test_feature_importances_before_fit_raises(self):
        model = self._fast_model()
        with pytest.raises(RuntimeError, match="fit\\(\\) must be called"):
            _ = model.feature_importances_

    def test_feature_importances_sum_to_one(self):
        X, y = _make_xy(30, n_features=6)
        model = self._fast_model()
        model.fit(X, y)
        assert pytest.approx(model.feature_importances_.sum(), abs=1e-5) == 1.0

    def test_too_small_training_window_raises(self):
        model = GBMModel(n_iter=2, n_splits=3, random_state=0)
        X, y = _make_xy(n=5)
        with pytest.raises(ValueError, match="Training window too small"):
            model.fit(X, y)

    def test_fit_returns_self(self):
        X, y = _make_xy(30)
        model = self._fast_model()
        result = model.fit(X, y)
        assert result is model

    def test_reproducible_with_same_seed(self):
        X, y = _make_xy(30)
        m1 = GBMModel(n_iter=2, n_splits=2, random_state=7)
        m2 = GBMModel(n_iter=2, n_splits=2, random_state=7)
        m1.fit(X, y)
        m2.fit(X, y)
        np.testing.assert_array_equal(m1.predict(X[:10]), m2.predict(X[:10]))

    def test_horizon_affects_weights_not_output_shape(self):
        """label_horizon only affects sample weights; output shape is unchanged."""
        X, y = _make_xy(30)
        m1 = GBMModel(label_horizon=1, n_iter=2, n_splits=2, random_state=0)
        m2 = GBMModel(label_horizon=5, n_iter=2, n_splits=2, random_state=0)
        m1.fit(X, y)
        m2.fit(X, y)
        assert m1.predict(X[:5]).shape == m2.predict(X[:5]).shape
