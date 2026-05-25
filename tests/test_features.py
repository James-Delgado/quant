"""Tests for src/quant/features/labels.py."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.features.labels import LabelResult, generate_labels


def _prices(values: list[float]) -> pd.Series:
    dates = pd.date_range("2024-01-02", periods=len(values), freq="B")
    return pd.Series(values, index=dates, name="close", dtype=float)


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
