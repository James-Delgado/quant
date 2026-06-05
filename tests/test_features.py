"""Tests for src/quant/features/labels.py, engineering.py, and weights.py."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.features.weights import compute_sample_weights
from quant.features.engineering import (
    _FRED_SERIES,
    _attach_fred_features,
    _compute_price_features,
    _rsi,
    build_features,
)
from quant.features.labels import LabelResult, generate_labels


def _ohlcv(n: int = 30, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    dates = pd.bdate_range("2023-01-02", periods=n, tz="UTC")
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": rng.integers(100_000, 1_000_000, n).astype(float),
        },
        index=dates,
    )


def _fred_wide(n: int = 10) -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-02", periods=n, tz="UTC")
    return pd.DataFrame(
        {
            "DGS10": np.linspace(3.5, 4.0, n),
            "DFF": np.linspace(5.0, 5.25, n),
            "VIXCLS": np.linspace(20.0, 25.0, n),
        },
        index=dates,
    )


def _prices(values: list[float]) -> pd.Series:
    dates = pd.date_range("2024-01-02", periods=len(values), freq="B")
    return pd.Series(values, index=dates, name="close", dtype=float)


class TestComputePriceFeatures:
    def test_returns_dataframe_same_index(self):
        prices = _ohlcv(30)
        feats = _compute_price_features(prices)
        assert isinstance(feats, pd.DataFrame)
        assert feats.index.equals(prices.index)

    def test_expected_columns_present(self):
        feats = _compute_price_features(_ohlcv(30))
        expected = (
            "ret_1d", "ret_5d", "ret_21d", "vol_21d", "vol_63d",
            "mom_21d", "rsi_14", "log_volume",
            "ret_252d", "ret_126d", "ma200_ratio", "ma50_ratio", "volume_ratio",
        )
        for col in expected:
            assert col in feats.columns, f"missing column: {col}"

    def test_new_price_features_nan_during_warmup(self):
        # 30 bars is insufficient for 50-, 63-, 126-, and 200-bar lookbacks.
        feats = _compute_price_features(_ohlcv(30))
        assert feats["ret_252d"].isna().all(), "ret_252d needs 252 bars — should be all NaN at n=30"
        assert feats["ret_126d"].isna().all(), "ret_126d needs 126 bars — should be all NaN at n=30"
        assert feats["ma200_ratio"].isna().all(), "ma200_ratio needs 200 bars — should be all NaN at n=30"

    def test_new_price_features_valid_after_warmup(self):
        feats = _compute_price_features(_ohlcv(260))
        assert feats["ret_252d"].notna().sum() > 0, "ret_252d should have valid values after 252 bars"
        assert feats["ret_126d"].notna().sum() > 0, "ret_126d should have valid values after 126 bars"
        assert feats["ma200_ratio"].notna().sum() > 0, "ma200_ratio should have valid values after 200 bars"
        assert feats["ma50_ratio"].notna().sum() > 0
        assert feats["volume_ratio"].notna().sum() > 0

    def test_ma_ratios_positive_when_valid(self):
        feats = _compute_price_features(_ohlcv(260))
        assert (feats["ma200_ratio"].dropna() > 0).all(), "price / MA must be positive"
        assert (feats["ma50_ratio"].dropna() > 0).all()
        assert (feats["volume_ratio"].dropna() > 0).all()

    def test_ret_1d_is_pct_change(self):
        prices = _ohlcv(10)
        feats = _compute_price_features(prices)
        expected = prices["close"].pct_change()
        pd.testing.assert_series_equal(feats["ret_1d"], expected, check_names=False)

    def test_log_volume_positive(self):
        feats = _compute_price_features(_ohlcv(10))
        assert (feats["log_volume"].dropna() > 0).all()

    def test_rsi_bounded(self):
        feats = _compute_price_features(_ohlcv(50))
        rsi = feats["rsi_14"].dropna()
        assert (rsi >= 0).all() and (rsi <= 100).all()


class TestAttachFredFeatures:
    def test_asof_attach_no_future_leak(self):
        prices = _ohlcv(20)
        # FRED data has only 5 observations in the first half of the price window
        fred = _fred_wide(5)
        feats = _compute_price_features(prices)
        merged = _attach_fred_features(feats, fred)

        # Each bar's FRED value must not exceed the last FRED date available
        last_fred_date = fred.index[-1]
        # Bars after last_fred_date should have the last known value (not NaN)
        late_bars = merged[merged.index > last_fred_date]
        assert late_bars["DGS10"].notna().all(), (
            "Bars after last FRED observation should carry forward the last known value"
        )
        # Bars before first FRED date should be NaN
        first_fred_date = fred.index[0]
        early_bars = merged[merged.index < first_fred_date]
        if not early_bars.empty:
            assert early_bars["DGS10"].isna().all(), (
                "Bars before first FRED observation must be NaN (no future data)"
            )

    def test_empty_fred_fills_nan(self):
        prices = _ohlcv(10)
        feats = _compute_price_features(prices)
        merged = _attach_fred_features(feats, pd.DataFrame())
        for col in _FRED_SERIES:
            assert col in merged.columns
            assert merged[col].isna().all()
        assert "yield_curve" in merged.columns
        assert merged["yield_curve"].isna().all()

    def test_yield_curve_column_present(self):
        prices = _ohlcv(20)
        fred = _fred_wide(10)
        feats = _compute_price_features(prices)
        merged = _attach_fred_features(feats, fred)
        assert "yield_curve" in merged.columns

    def test_yield_curve_equals_dgs10_minus_dff(self):
        prices = _ohlcv(20)
        fred = _fred_wide(10)
        feats = _compute_price_features(prices)
        merged = _attach_fred_features(feats, fred)
        valid = merged["yield_curve"].dropna()
        assert len(valid) > 0
        expected = (merged["DGS10"] - merged["DFF"]).dropna()
        pd.testing.assert_series_equal(valid, expected.loc[valid.index], check_names=False)

    def test_index_preserved_after_attach(self):
        prices = _ohlcv(20)
        fred = _fred_wide(10)
        feats = _compute_price_features(prices)
        merged = _attach_fred_features(feats, fred)
        assert len(merged) == len(prices)

    def test_fred_series_columns_present(self):
        prices = _ohlcv(20)
        fred = _fred_wide(10)
        feats = _compute_price_features(prices)
        merged = _attach_fred_features(feats, fred)
        for col in _FRED_SERIES:
            assert col in merged.columns

    def test_nan_gaps_in_fred_do_not_propagate(self):
        # Simulate the real-world pattern: DGS10 has NaN on Friday/weekend rows
        # (DFF publishes daily; DGS10 only Mon–Thu).  The bar that falls on or
        # after a NaN row should get the last known DGS10 value, not NaN.
        prices = _ohlcv(10)
        feats = _compute_price_features(prices)

        # Build a FRED wide table with an intentional mid-week NaN in DGS10
        fred = _fred_wide(10).copy()
        fred.iloc[3, fred.columns.get_loc("DGS10")] = float("nan")  # simulate Friday gap

        merged = _attach_fred_features(feats, fred)
        # The bar that aligns with the NaN row should carry the previous value
        assert merged["DGS10"].notna().sum() > 0, "At least some DGS10 values should be non-NaN"


class TestBuildFeatures:
    def test_returns_dict_keyed_by_symbol(self, monkeypatch):
        prices = {"AAPL": _ohlcv(30), "MSFT": _ohlcv(30, seed=1)}
        monkeypatch.setattr(
            "quant.features.engineering._load_fred_wide",
            lambda con: pd.DataFrame(),
        )
        result = build_features(["AAPL", "MSFT"], prices)
        assert set(result.keys()) == {"AAPL", "MSFT"}

    def test_empty_symbols_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            build_features([], {})

    def test_missing_symbol_raises(self):
        with pytest.raises(ValueError, match="missing symbols"):
            build_features(["AAPL"], {"MSFT": _ohlcv(10)})

    def test_feature_index_matches_prices(self, monkeypatch):
        prices = {"AAPL": _ohlcv(30)}
        monkeypatch.setattr(
            "quant.features.engineering._load_fred_wide",
            lambda con: pd.DataFrame(),
        )
        result = build_features(["AAPL"], prices)
        assert result["AAPL"].index.equals(prices["AAPL"].index)


class TestGenerateLabels:
    def test_returns_label_result(self):
        result = generate_labels(_prices([100.0, 110.0, 121.0]), horizon=1)
        assert isinstance(result, LabelResult)

    def test_horizon_bars_matches_argument(self):
        result = generate_labels(_prices([100.0, 110.0, 121.0]), horizon=2)
        assert result.horizon_bars == 2

    def test_forward_return_values_horizon_1(self):
        # 100 → 110 → 121: returns should be 0.10, 0.10, NaN
        result = generate_labels(_prices([100.0, 110.0, 121.0]), horizon=1)
        assert pytest.approx(result.series.iloc[0]) == 0.10
        assert pytest.approx(result.series.iloc[1]) == 0.10
        assert np.isnan(result.series.iloc[2])

    def test_forward_return_values_horizon_2(self):
        # 100 → 121 over 2 bars = 21% return; last 2 bars are NaN
        result = generate_labels(_prices([100.0, 110.0, 121.0]), horizon=2)
        assert pytest.approx(result.series.iloc[0]) == 0.21
        assert np.isnan(result.series.iloc[1])
        assert np.isnan(result.series.iloc[2])

    def test_nan_tail_length_equals_horizon(self):
        prices = _prices([float(i) for i in range(1, 11)])
        for h in (1, 3, 5):
            result = generate_labels(prices, horizon=h)
            nan_count = result.series.isna().sum()
            assert nan_count == h, f"horizon={h}: expected {h} NaNs, got {nan_count}"

    def test_index_preserved(self):
        prices = _prices([100.0, 105.0, 110.0])
        result = generate_labels(prices, horizon=1)
        assert list(result.series.index) == list(prices.index)

    def test_horizon_zero_raises(self):
        with pytest.raises(ValueError, match="horizon must be >= 1"):
            generate_labels(_prices([100.0, 110.0]), horizon=0)

    def test_horizon_negative_raises(self):
        with pytest.raises(ValueError, match="horizon must be >= 1"):
            generate_labels(_prices([100.0, 110.0]), horizon=-1)

    def test_non_series_raises(self):
        with pytest.raises(TypeError, match="pandas Series"):
            generate_labels([100.0, 110.0], horizon=1)  # type: ignore[arg-type]

    def test_empty_series_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            generate_labels(pd.Series([], dtype=float), horizon=1)

    def test_label_result_is_namedtuple(self):
        result = generate_labels(_prices([100.0, 110.0]), horizon=1)
        # Destructuring works — horizon_bars is inseparable from series
        series, horizon_bars = result
        assert horizon_bars == 1
        assert len(series) == 2

    def test_zero_price_raises(self):
        with pytest.raises(ValueError, match="zero values"):
            generate_labels(_prices([100.0, 0.0, 110.0]), horizon=1)

    def test_bool_dtype_raises(self):
        dates = pd.date_range("2024-01-02", periods=3, freq="B")
        bool_series = pd.Series([True, False, True], index=dates)
        with pytest.raises(TypeError, match="numeric dtype"):
            generate_labels(bool_series, horizon=1)

    def test_nan_in_prices_raises(self):
        with pytest.raises(ValueError, match="NaN"):
            generate_labels(_prices([100.0, float("nan"), 110.0]), horizon=1)

    def test_horizon_ge_length_raises(self):
        with pytest.raises(ValueError, match="all labels would be NaN"):
            generate_labels(_prices([100.0, 110.0, 121.0]), horizon=3)

    def test_unsorted_datetime_index_raises(self):
        dates = pd.to_datetime(["2024-01-04", "2024-01-02", "2024-01-03"])
        prices = pd.Series([100.0, 110.0, 120.0], index=dates)
        with pytest.raises(ValueError, match="sorted ascending"):
            generate_labels(prices, horizon=1)


class TestComputeSampleWeights:
    def test_returns_ndarray_correct_shape(self):
        w = compute_sample_weights(10, horizon=5)
        assert isinstance(w, np.ndarray)
        assert w.shape == (10,)

    def test_mean_is_one(self):
        for n, h in [(10, 1), (20, 5), (100, 10), (5, 5)]:
            w = compute_sample_weights(n, h)
            assert pytest.approx(w.mean(), abs=1e-10) == 1.0

    def test_all_positive(self):
        w = compute_sample_weights(20, horizon=5)
        assert (w > 0).all()

    def test_horizon_one_uniform(self):
        # No overlap when horizon=1: each label uses only one future bar.
        w = compute_sample_weights(10, horizon=1)
        assert np.allclose(w, 1.0)

    def test_edge_samples_higher_weight(self):
        # With overlap (horizon>1), first and last samples share fewer neighbours
        # and should have above-average (>1.0) weights.
        w = compute_sample_weights(20, horizon=5)
        assert w[0] > 1.0, "first sample should be above mean"
        assert w[-1] > 1.0, "last sample should be above mean"

    def test_n_samples_one(self):
        w = compute_sample_weights(1, horizon=5)
        assert w.shape == (1,)
        assert pytest.approx(w[0]) == 1.0

    def test_invalid_n_samples_raises(self):
        with pytest.raises(ValueError, match="n_samples must be >= 1"):
            compute_sample_weights(0, horizon=1)

    def test_invalid_horizon_raises(self):
        with pytest.raises(ValueError, match="horizon must be >= 1"):
            compute_sample_weights(10, horizon=0)
