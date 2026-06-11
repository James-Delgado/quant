"""Tests for src/quant/backtest/regimes.py."""
from __future__ import annotations

import pandas as pd
import pytest

from quant.backtest.regimes import (
    DateRangeDetector,
    RegimeDetector,
    VIXThresholdDetector,
    tag_regimes,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _vix_series(values: list[float], start: str = "2020-01-02") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, name="VIXCLS")


def _date_index(start: str, periods: int) -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=periods)


# ─── VIXThresholdDetector ────────────────────────────────────────────────────

class TestVIXThresholdDetector:
    def test_low_vol_below_lower_threshold(self) -> None:
        vix = _vix_series([10.0, 12.0, 14.0])
        det = VIXThresholdDetector(vix)
        out = det.label(vix.index)
        assert (out == "low_vol").all()

    def test_high_vol_above_upper_threshold(self) -> None:
        vix = _vix_series([30.0, 40.0, 50.0])
        det = VIXThresholdDetector(vix)
        out = det.label(vix.index)
        assert (out == "high_vol").all()

    def test_mid_vol_between_thresholds(self) -> None:
        vix = _vix_series([16.0, 20.0, 24.0])
        det = VIXThresholdDetector(vix)
        out = det.label(vix.index)
        assert (out == "mid_vol").all()

    def test_boundary_low_inclusive_at_low(self) -> None:
        """At exactly the low threshold, classify as low_vol (strict `<` for mid)."""
        vix = _vix_series([15.0])
        det = VIXThresholdDetector(vix, low=15.0, high=25.0)
        out = det.label(vix.index)
        assert out.iloc[0] == "low_vol"

    def test_boundary_high_inclusive_at_high(self) -> None:
        """At exactly the high threshold, classify as high_vol (strict `>` for mid)."""
        vix = _vix_series([25.0])
        det = VIXThresholdDetector(vix, low=15.0, high=25.0)
        out = det.label(vix.index)
        assert out.iloc[0] == "high_vol"

    def test_low_must_be_below_high(self) -> None:
        with pytest.raises(ValueError, match="low.*high"):
            VIXThresholdDetector(_vix_series([15.0]), low=25.0, high=15.0)

    def test_missing_vix_date_raises(self) -> None:
        """No silent fill — a missing VIX bar at a requested date is an error."""
        vix = _vix_series([15.0, 20.0, 25.0])
        det = VIXThresholdDetector(vix)
        future = pd.bdate_range("2030-01-02", periods=2)
        with pytest.raises(ValueError, match="missing VIX"):
            det.label(future)

    def test_point_in_time_invariant(self) -> None:
        """Asking the detector to label date D must only consult vix.loc[D]."""
        vix = pd.Series(
            [10.0, 20.0, 30.0],
            index=pd.bdate_range("2020-01-02", periods=3),
            name="VIXCLS",
        )
        det = VIXThresholdDetector(vix)
        early_label = det.label(vix.index[:1]).iloc[0]
        # The detector cannot have known the later values when labeling day 0.
        assert early_label == "low_vol"

    def test_returns_series_aligned_with_dates(self) -> None:
        vix = _vix_series([15.0, 20.0, 25.0])
        det = VIXThresholdDetector(vix)
        out = det.label(vix.index)
        assert isinstance(out, pd.Series)
        assert out.index.equals(vix.index)
        assert len(out) == len(vix)

    def test_implements_regime_detector_protocol(self) -> None:
        vix = _vix_series([20.0])
        det = VIXThresholdDetector(vix)
        assert isinstance(det, RegimeDetector)


# ─── DateRangeDetector ───────────────────────────────────────────────────────

class TestDateRangeDetector:
    def test_default_ranges_map_pre_qe(self) -> None:
        det = DateRangeDetector()
        dates = pd.to_datetime(["2005-06-15"])
        assert det.label(dates).iloc[0] == "pre_qe"

    def test_default_ranges_map_qe_bull(self) -> None:
        det = DateRangeDetector()
        dates = pd.to_datetime(["2015-06-15"])
        assert det.label(dates).iloc[0] == "qe_bull"

    def test_default_ranges_map_covid(self) -> None:
        det = DateRangeDetector()
        dates = pd.to_datetime(["2020-06-15"])
        assert det.label(dates).iloc[0] == "covid"

    def test_default_ranges_map_rate_cycle(self) -> None:
        det = DateRangeDetector()
        dates = pd.to_datetime(["2024-06-15"])
        assert det.label(dates).iloc[0] == "rate_cycle"

    def test_boundary_2019_12_31_is_qe_bull(self) -> None:
        det = DateRangeDetector()
        assert det.label(pd.to_datetime(["2019-12-31"])).iloc[0] == "qe_bull"

    def test_boundary_2020_01_01_is_covid(self) -> None:
        det = DateRangeDetector()
        assert det.label(pd.to_datetime(["2020-01-01"])).iloc[0] == "covid"

    def test_boundary_2021_12_31_is_covid(self) -> None:
        det = DateRangeDetector()
        assert det.label(pd.to_datetime(["2021-12-31"])).iloc[0] == "covid"

    def test_boundary_2022_01_01_is_rate_cycle(self) -> None:
        det = DateRangeDetector()
        assert det.label(pd.to_datetime(["2022-01-01"])).iloc[0] == "rate_cycle"

    def test_custom_ranges_override(self) -> None:
        det = DateRangeDetector(
            ranges=[
                ("custom_a", "2020-01-01", "2020-12-31"),
                ("custom_b", "2021-01-01", "2021-12-31"),
            ],
            default_label="other",
        )
        dates = pd.to_datetime(["2019-06-01", "2020-06-01", "2021-06-01", "2022-06-01"])
        out = det.label(dates)
        assert list(out) == ["other", "custom_a", "custom_b", "other"]

    def test_overlapping_ranges_raise(self) -> None:
        with pytest.raises(ValueError, match="overlap"):
            DateRangeDetector(
                ranges=[
                    ("a", "2020-01-01", "2020-12-31"),
                    ("b", "2020-06-01", "2021-12-31"),
                ]
            )

    def test_returns_series_aligned_with_dates(self) -> None:
        det = DateRangeDetector()
        dates = _date_index("2018-01-02", 5)
        out = det.label(dates)
        assert isinstance(out, pd.Series)
        assert out.index.equals(dates)

    def test_implements_regime_detector_protocol(self) -> None:
        det = DateRangeDetector()
        assert isinstance(det, RegimeDetector)


# ─── tag_regimes() convenience function ──────────────────────────────────────

class TestTagRegimes:
    def test_delegates_to_detector(self) -> None:
        det = DateRangeDetector()
        dates = pd.to_datetime(["2015-06-15", "2020-06-15"])
        out = tag_regimes(dates, det)
        assert list(out) == ["qe_bull", "covid"]

    def test_works_with_vix_detector(self) -> None:
        vix = _vix_series([10.0, 20.0, 40.0])
        det = VIXThresholdDetector(vix)
        out = tag_regimes(vix.index, det)
        assert list(out) == ["low_vol", "mid_vol", "high_vol"]

    def test_preserves_index(self) -> None:
        det = DateRangeDetector()
        dates = _date_index("2018-01-02", 10)
        out = tag_regimes(dates, det)
        assert out.index.equals(dates)


# ─── Orthogonality of axes ───────────────────────────────────────────────────

class TestOrthogonality:
    """The volatility axis (VIX) and the era axis (DateRange) yield
    independent labels — a date can be both `high_vol` and `covid`, or
    `low_vol` and `rate_cycle`.
    """

    def test_same_date_gets_independent_labels(self) -> None:
        dates = pd.to_datetime(["2020-03-16"])  # mid-COVID, very high VIX
        vix = pd.Series([80.0], index=dates, name="VIXCLS")
        vix_det = VIXThresholdDetector(vix)
        era_det = DateRangeDetector()
        assert vix_det.label(dates).iloc[0] == "high_vol"
        assert era_det.label(dates).iloc[0] == "covid"
