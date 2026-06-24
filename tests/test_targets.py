"""Tests for src/quant/features/targets.py.

Covers the four B1 candidate targets, the in-code ``TARGET_CATALOG`` registry,
and the ``make_target_labels`` dispatch. Targets are point-in-time label series
whose horizon constant couples to the backtester's purge/embargo logic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.features.labels import LabelResult
from quant.features.targets import (
    DIRECTIONAL_HORIZON_LONG,
    DIRECTIONAL_HORIZON_SHORT,
    DRAWDOWN_HORIZON,
    VOL_HORIZON,
    TARGET_CATALOG,
    MaterialityCriterion,
    TargetSpec,
    directional_labels,
    drawdown_event_labels,
    make_target_labels,
    realized_vol_labels,
)


def _prices(values: list[float]) -> pd.Series:
    dates = pd.bdate_range("2024-01-02", periods=len(values))
    return pd.Series(values, index=dates, dtype=float)


# ─── drawdown_event_labels ────────────────────────────────────────────────────


class TestDrawdownEventLabels:
    def test_returns_label_result(self):
        result = drawdown_event_labels(_prices([100.0 + i for i in range(30)]))
        assert isinstance(result, LabelResult)

    def test_horizon_bars_matches_argument(self):
        result = drawdown_event_labels(_prices([100.0] * 20), horizon=7)
        assert result.horizon_bars == 7

    def test_rising_path_has_no_drawdown(self):
        prices = _prices([100.0 + i for i in range(10)])
        result = drawdown_event_labels(prices, horizon=3)
        valid = result.series.dropna()
        assert (valid == 0.0).all()

    def test_crash_exceeding_threshold_is_labelled_one(self):
        # A 10% drop sits inside every forward window of the first three bars.
        prices = _prices([100.0, 100.0, 100.0, 90.0, 100.0, 100.0])
        result = drawdown_event_labels(prices, horizon=3, dd_threshold=0.05)
        assert result.series.iloc[0] == 1.0
        assert result.series.iloc[1] == 1.0
        assert result.series.iloc[2] == 1.0

    def test_small_dip_below_threshold_is_labelled_zero(self):
        # A 2% dip never exceeds the 5% threshold.
        prices = _prices([100.0, 100.0, 98.0, 100.0, 100.0, 100.0])
        result = drawdown_event_labels(prices, horizon=3, dd_threshold=0.05)
        assert result.series.iloc[0] == 0.0

    def test_last_horizon_bars_are_nan(self):
        prices = _prices([100.0 + i for i in range(10)])
        result = drawdown_event_labels(prices, horizon=3)
        assert result.series.iloc[-3:].isna().all()
        assert result.series.iloc[: len(prices) - 3].notna().all()

    def test_horizon_at_least_one(self):
        with pytest.raises(ValueError, match="horizon must be >= 1"):
            drawdown_event_labels(_prices([100.0] * 10), horizon=0)

    def test_threshold_in_unit_interval(self):
        with pytest.raises(ValueError, match="dd_threshold must be in"):
            drawdown_event_labels(_prices([100.0] * 10), dd_threshold=1.5)

    def test_horizon_too_large_raises(self):
        with pytest.raises(ValueError, match="must be < len"):
            drawdown_event_labels(_prices([100.0] * 5), horizon=5)

    def test_nan_prices_raise(self):
        prices = _prices([100.0, np.nan, 102.0, 103.0, 104.0])
        with pytest.raises(ValueError, match="NaN"):
            drawdown_event_labels(prices, horizon=2)


# ─── realized_vol_labels ──────────────────────────────────────────────────────


class TestRealizedVolLabels:
    def test_returns_label_result(self):
        prices = _prices([100.0 * (1.01 ** i) for i in range(40)])
        result = realized_vol_labels(prices, horizon=5)
        assert isinstance(result, LabelResult)

    def test_horizon_bars_matches_argument(self):
        prices = _prices([100.0 + i for i in range(40)])
        result = realized_vol_labels(prices, horizon=5)
        assert result.horizon_bars == 5

    def test_matches_forward_log_std_formula(self):
        rng = np.random.default_rng(3)
        rets = rng.normal(0.0, 0.01, size=40)
        prices = _prices(list(100.0 * np.cumprod(1.0 + rets)))
        horizon = 5
        result = realized_vol_labels(prices, horizon=horizon)

        ret_series = prices.pct_change()
        expected = np.log(
            ret_series.rolling(window=horizon, min_periods=horizon)
            .std(ddof=1)
            .shift(-horizon)
        )
        pd.testing.assert_series_equal(
            result.series, expected, check_names=False
        )

    def test_higher_forward_vol_gives_higher_label(self):
        # Calm first half, volatile second half. The label looks forward, so an
        # early bar (calm forward window) must score below a late bar (volatile).
        calm = [100.0 + 0.1 * i for i in range(20)]
        volatile = list(100.0 + 2.0 + np.array([(-1) ** i * 5.0 for i in range(20)]))
        prices = _prices(calm + volatile)
        result = realized_vol_labels(prices, horizon=5)
        assert result.series.iloc[2] < result.series.iloc[25]

    def test_last_horizon_bars_are_nan(self):
        prices = _prices([100.0 * (1.0 + 0.01 * ((-1) ** i)) for i in range(30)])
        result = realized_vol_labels(prices, horizon=5)
        assert result.series.iloc[-5:].isna().all()

    def test_constant_prices_raise(self):
        with pytest.raises(ValueError, match="zero realised vol"):
            realized_vol_labels(_prices([100.0] * 30), horizon=5)

    def test_horizon_at_least_two(self):
        with pytest.raises(ValueError, match="horizon must be >= 2"):
            realized_vol_labels(_prices([100.0 + i for i in range(10)]), horizon=1)


# ─── directional_labels ───────────────────────────────────────────────────────


class TestDirectionalLabels:
    def test_returns_label_result(self):
        result = directional_labels(_prices([100.0 + i for i in range(10)]), horizon=2)
        assert isinstance(result, LabelResult)

    def test_rising_is_one_falling_is_zero(self):
        rising = directional_labels(_prices([100.0 + i for i in range(10)]), horizon=2)
        falling = directional_labels(_prices([100.0 - i for i in range(10)]), horizon=2)
        assert rising.series.dropna().eq(1.0).all()
        assert falling.series.dropna().eq(0.0).all()

    def test_flat_is_zero(self):
        prices = _prices([100.0, 100.0, 100.0, 100.0, 100.0])
        result = directional_labels(prices, horizon=2)
        assert result.series.dropna().eq(0.0).all()

    def test_last_horizon_bars_are_nan(self):
        prices = _prices([100.0 + i for i in range(10)])
        result = directional_labels(prices, horizon=3)
        assert result.series.iloc[-3:].isna().all()
        assert result.series.iloc[: len(prices) - 3].notna().all()

    def test_horizon_too_large_raises(self):
        with pytest.raises(ValueError, match="must be < len"):
            directional_labels(_prices([100.0] * 4), horizon=4)


# ─── TARGET_CATALOG + specs ───────────────────────────────────────────────────


class TestTargetCatalog:
    def test_has_four_targets(self):
        assert set(TARGET_CATALOG) == {
            "drawdown_21d",
            "realized_vol_21d",
            "directional_5d",
            "directional_21d",
        }

    def test_specs_are_frozen(self):
        spec = TARGET_CATALOG["drawdown_21d"]
        with pytest.raises(Exception):
            spec.name = "mutated"  # type: ignore[misc]

    def test_every_spec_has_materiality(self):
        for spec in TARGET_CATALOG.values():
            assert isinstance(spec, TargetSpec)
            assert len(spec.materiality) >= 1
            for crit in spec.materiality:
                assert isinstance(crit, MaterialityCriterion)
                assert crit.threshold > 0

    def test_directional_targets_gate_both_auc_and_sharpe(self):
        for tid in ("directional_5d", "directional_21d"):
            metrics = {c.metric for c in TARGET_CATALOG[tid].materiality}
            assert metrics == {"auc", "sharpe"}

    def test_horizons_match_pinned_constants(self):
        assert TARGET_CATALOG["drawdown_21d"].horizon_bars == DRAWDOWN_HORIZON
        assert TARGET_CATALOG["realized_vol_21d"].horizon_bars == VOL_HORIZON
        assert TARGET_CATALOG["directional_5d"].horizon_bars == DIRECTIONAL_HORIZON_SHORT
        assert TARGET_CATALOG["directional_21d"].horizon_bars == DIRECTIONAL_HORIZON_LONG

    def test_deflation_method_per_target(self):
        assert TARGET_CATALOG["drawdown_21d"].deflation == "skill_z"
        assert TARGET_CATALOG["realized_vol_21d"].deflation == "skill_z"
        assert TARGET_CATALOG["directional_5d"].deflation == "dsr"
        assert TARGET_CATALOG["directional_21d"].deflation == "dsr"


# ─── make_target_labels ───────────────────────────────────────────────────────


class TestMakeTargetLabels:
    def test_dispatches_with_pinned_horizon(self):
        prices = _prices([100.0 * (1.0 + 0.01 * ((-1) ** i)) for i in range(60)])
        for tid, spec in TARGET_CATALOG.items():
            result = make_target_labels(tid, prices)
            assert isinstance(result, LabelResult)
            assert result.horizon_bars == spec.horizon_bars

    def test_directional_5d_uses_horizon_5(self):
        prices = _prices([100.0 + i for i in range(30)])
        result = make_target_labels("directional_5d", prices)
        assert result.horizon_bars == 5

    def test_unknown_target_raises(self):
        with pytest.raises(KeyError, match="unknown target id"):
            make_target_labels("nope", _prices([100.0] * 30))
