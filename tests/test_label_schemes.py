"""Tests for src/quant/features/label_schemes.py.

Two label schemes are covered:
  * ``vol_scaled_returns`` — forward return divided by point-in-time realised vol
  * ``triple_barrier_labels`` — López de Prado AFML §3.5 three-barrier method
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.features.label_schemes import (
    LDP_DEFAULT,
    TripleBarrierConfig,
    triple_barrier_labels,
    vol_scaled_returns,
)
from quant.features.labels import LabelResult


def _prices(values: list[float]) -> pd.Series:
    dates = pd.bdate_range("2024-01-02", periods=len(values))
    return pd.Series(values, index=dates, dtype=float)


# ─── vol_scaled_returns ──────────────────────────────────────────────────────


class TestVolScaledReturns:
    def test_returns_label_result(self):
        prices = _prices([100.0 + i for i in range(30)])
        result = vol_scaled_returns(prices, horizon=1, vol_window=5)
        assert isinstance(result, LabelResult)

    def test_horizon_bars_matches_argument(self):
        prices = _prices([100.0 + i for i in range(30)])
        result = vol_scaled_returns(prices, horizon=3, vol_window=5)
        assert result.horizon_bars == 3

    def test_nan_tail_length(self):
        # Tail NaN count = horizon (forward window); the vol window adds
        # NaNs at the *head*, covered separately.
        prices = _prices([100.0 + i for i in range(30)])
        result = vol_scaled_returns(prices, horizon=2, vol_window=5)
        tail_nan = result.series.tail(2).isna().sum()
        assert tail_nan == 2

    def test_nan_head_length_equals_vol_window_minus_one(self):
        # rolling(vol_window).std() needs ``vol_window`` returns before
        # producing a value → first ``vol_window - 1`` price-positions get NaN.
        prices = _prices([100.0 + i for i in range(30)])
        result = vol_scaled_returns(prices, horizon=1, vol_window=5)
        head_nan = result.series.head(4).isna().sum()
        assert head_nan == 4

    def test_scaled_value_matches_manual_calc(self):
        prices = _prices([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 110.0])
        result = vol_scaled_returns(prices, horizon=1, vol_window=5)
        returns = prices.pct_change()
        expected_vol = returns.iloc[1:6].std()
        expected = (110.0 / 105.0 - 1.0) / expected_vol
        assert pytest.approx(result.series.iloc[5], rel=1e-6) == expected

    def test_point_in_time_no_lookahead(self):
        # Vol at bar 5 must be independent of prices after bar 5.
        common = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
        a = _prices(common + [200.0, 50.0])
        b = _prices(common + [105.5, 105.7])
        result_a = vol_scaled_returns(a, horizon=1, vol_window=5)
        result_b = vol_scaled_returns(b, horizon=1, vol_window=5)
        raw_a = a.iloc[6] / a.iloc[5] - 1.0
        raw_b = b.iloc[6] / b.iloc[5] - 1.0
        ratio_raw = raw_a / raw_b
        ratio_scaled = result_a.series.iloc[5] / result_b.series.iloc[5]
        assert pytest.approx(ratio_scaled, rel=1e-6) == ratio_raw

    def test_index_preserved(self):
        prices = _prices([100.0 + i for i in range(20)])
        result = vol_scaled_returns(prices, horizon=1, vol_window=5)
        assert list(result.series.index) == list(prices.index)

    def test_horizon_zero_raises(self):
        prices = _prices([100.0 + i for i in range(20)])
        with pytest.raises(ValueError, match="horizon must be >= 1"):
            vol_scaled_returns(prices, horizon=0, vol_window=5)

    def test_vol_window_lt_2_raises(self):
        prices = _prices([100.0 + i for i in range(20)])
        with pytest.raises(ValueError, match="vol_window must be >= 2"):
            vol_scaled_returns(prices, horizon=1, vol_window=1)

    def test_non_series_raises(self):
        with pytest.raises(TypeError, match="pandas Series"):
            vol_scaled_returns([100.0, 101.0], horizon=1, vol_window=2)  # type: ignore[arg-type]

    def test_empty_series_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            vol_scaled_returns(pd.Series([], dtype=float), horizon=1, vol_window=2)

    def test_zero_price_raises(self):
        prices = _prices([100.0, 0.0, 101.0, 102.0, 103.0, 104.0])
        with pytest.raises(ValueError, match="zero values"):
            vol_scaled_returns(prices, horizon=1, vol_window=3)

    def test_nan_in_prices_raises(self):
        prices = _prices([100.0, float("nan"), 101.0, 102.0, 103.0])
        with pytest.raises(ValueError, match="NaN"):
            vol_scaled_returns(prices, horizon=1, vol_window=3)

    def test_bool_dtype_raises(self):
        dates = pd.bdate_range("2024-01-02", periods=5)
        bool_series = pd.Series([True, False, True, False, True], index=dates)
        with pytest.raises(TypeError, match="numeric dtype"):
            vol_scaled_returns(bool_series, horizon=1, vol_window=2)

    def test_zero_vol_window_raises(self):
        flat = _prices([100.0] * 20)
        with pytest.raises(ValueError, match="zero realised vol"):
            vol_scaled_returns(flat, horizon=1, vol_window=5)

    def test_horizon_ge_length_raises(self):
        prices = _prices([100.0, 101.0, 102.0])
        with pytest.raises(ValueError, match="all labels would be NaN"):
            vol_scaled_returns(prices, horizon=3, vol_window=2)

    def test_unsorted_index_raises(self):
        dates = pd.to_datetime(["2024-01-04", "2024-01-02", "2024-01-03"])
        prices = pd.Series([100.0, 101.0, 102.0], index=dates)
        with pytest.raises(ValueError, match="sorted ascending"):
            vol_scaled_returns(prices, horizon=1, vol_window=2)


# ─── triple_barrier_labels ──────────────────────────────────────────────────


class TestTripleBarrierConfig:
    def test_default_values_match_ldp(self):
        # The LDP_DEFAULT values are pre-committed in docs/concepts/label-schemes.md.
        # Changes must come with a documented PR — this test pins them.
        assert LDP_DEFAULT.pt_sigma == 2.0
        assert LDP_DEFAULT.sl_sigma == 1.0
        assert LDP_DEFAULT.vol_window == 21
        assert LDP_DEFAULT.max_horizon == 5

    def test_config_is_frozen(self):
        with pytest.raises(Exception):
            LDP_DEFAULT.pt_sigma = 3.0  # type: ignore[misc]

    def test_custom_config_overrides_defaults(self):
        custom = TripleBarrierConfig(pt_sigma=3.0, sl_sigma=1.5)
        assert custom.pt_sigma == 3.0
        assert custom.sl_sigma == 1.5
        assert custom.vol_window == 21
        assert custom.max_horizon == 5


class TestTripleBarrierLabels:
    def test_returns_label_result(self):
        prices = _prices([100.0 + 0.1 * i for i in range(40)])
        result = triple_barrier_labels(prices)
        assert isinstance(result, LabelResult)

    def test_horizon_bars_equals_max_horizon(self):
        prices = _prices([100.0 + 0.1 * i for i in range(40)])
        cfg = TripleBarrierConfig(max_horizon=7)
        result = triple_barrier_labels(prices, config=cfg)
        assert result.horizon_bars == 7

    def test_index_preserved(self):
        prices = _prices([100.0 + 0.1 * i for i in range(40)])
        result = triple_barrier_labels(prices)
        assert list(result.series.index) == list(prices.index)

    def test_steady_ramp_hits_pt(self):
        rng = np.random.default_rng(0)
        n = 60
        log_returns = 0.005 + rng.normal(0, 0.001, n)
        levels = 100.0 * np.exp(np.cumsum(log_returns))
        prices = _prices(levels.tolist())
        cfg = TripleBarrierConfig(pt_sigma=2.0, sl_sigma=1.0, vol_window=10, max_horizon=5)
        result = triple_barrier_labels(prices, config=cfg)
        labels = result.series.iloc[cfg.vol_window:-cfg.max_horizon]
        pos_count = int((labels == 1).sum())
        neg_count = int((labels == -1).sum())
        assert pos_count > 0
        assert pos_count > neg_count

    def test_steady_crash_hits_sl(self):
        rng = np.random.default_rng(1)
        n = 60
        log_returns = -0.005 + rng.normal(0, 0.001, n)
        levels = 100.0 * np.exp(np.cumsum(log_returns))
        prices = _prices(levels.tolist())
        cfg = TripleBarrierConfig(pt_sigma=2.0, sl_sigma=1.0, vol_window=10, max_horizon=5)
        result = triple_barrier_labels(prices, config=cfg)
        labels = result.series.iloc[cfg.vol_window:-cfg.max_horizon]
        neg_count = int((labels == -1).sum())
        pos_count = int((labels == 1).sum())
        assert neg_count > 0
        assert neg_count > pos_count

    def test_custom_pt_sigma_changes_label_distribution(self):
        rng = np.random.default_rng(2)
        n = 60
        log_returns = 0.001 + rng.normal(0, 0.005, n)
        prices = _prices((100.0 * np.exp(np.cumsum(log_returns))).tolist())
        easy = triple_barrier_labels(
            prices,
            config=TripleBarrierConfig(pt_sigma=0.5, sl_sigma=1.0, vol_window=10, max_horizon=5),
        )
        hard = triple_barrier_labels(
            prices,
            config=TripleBarrierConfig(pt_sigma=4.0, sl_sigma=1.0, vol_window=10, max_horizon=5),
        )
        easy_pos = int((easy.series == 1).sum())
        hard_pos = int((hard.series == 1).sum())
        assert easy_pos > hard_pos

    def test_labels_are_in_minus_one_zero_plus_one(self):
        rng = np.random.default_rng(3)
        n = 50
        log_returns = rng.normal(0, 0.01, n)
        prices = _prices((100.0 * np.exp(np.cumsum(log_returns))).tolist())
        result = triple_barrier_labels(prices)
        valid = result.series.dropna()
        assert set(valid.unique()).issubset({-1, 0, 1})

    def test_max_horizon_zero_raises(self):
        prices = _prices([100.0 + 0.1 * i for i in range(40)])
        with pytest.raises(ValueError, match="max_horizon must be >= 1"):
            triple_barrier_labels(prices, config=TripleBarrierConfig(max_horizon=0))

    def test_vol_window_lt_2_raises(self):
        prices = _prices([100.0 + 0.1 * i for i in range(40)])
        with pytest.raises(ValueError, match="vol_window must be >= 2"):
            triple_barrier_labels(prices, config=TripleBarrierConfig(vol_window=1))

    def test_pt_sigma_non_positive_raises(self):
        prices = _prices([100.0 + 0.1 * i for i in range(40)])
        with pytest.raises(ValueError, match="pt_sigma must be > 0"):
            triple_barrier_labels(prices, config=TripleBarrierConfig(pt_sigma=0.0))

    def test_sl_sigma_non_positive_raises(self):
        prices = _prices([100.0 + 0.1 * i for i in range(40)])
        with pytest.raises(ValueError, match="sl_sigma must be > 0"):
            triple_barrier_labels(prices, config=TripleBarrierConfig(sl_sigma=-1.0))

    def test_non_series_raises(self):
        with pytest.raises(TypeError, match="pandas Series"):
            triple_barrier_labels([100.0, 101.0])  # type: ignore[arg-type]

    def test_empty_series_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            triple_barrier_labels(pd.Series([], dtype=float))

    def test_zero_price_raises(self):
        prices = _prices([100.0, 101.0, 0.0] + [100.0 + 0.1 * i for i in range(40)])
        with pytest.raises(ValueError, match="zero values"):
            triple_barrier_labels(prices)

    def test_nan_in_prices_raises(self):
        prices = _prices([100.0, float("nan")] + [100.0 + 0.1 * i for i in range(40)])
        with pytest.raises(ValueError, match="NaN"):
            triple_barrier_labels(prices)

    def test_unsorted_index_raises(self):
        dates = pd.to_datetime(
            ["2024-01-04", "2024-01-02", "2024-01-03", "2024-01-05", "2024-01-08"]
        )
        prices = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0], index=dates)
        with pytest.raises(ValueError, match="sorted ascending"):
            triple_barrier_labels(prices)

    def test_max_horizon_ge_length_raises(self):
        prices = _prices([100.0, 101.0, 102.0])
        with pytest.raises(ValueError, match="all labels would be NaN"):
            triple_barrier_labels(prices, config=TripleBarrierConfig(max_horizon=5))
