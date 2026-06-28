"""Tests for src/quant/features/labels.py, engineering.py, and weights.py."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest.regimes import VIXThresholdDetector
from quant.features.weights import compute_sample_weights
from quant.features.engineering import (
    _FRED_SERIES,
    FRED_PUBLICATION_LAGS,
    VIX_REGIME_HIGH,
    VIX_REGIME_LOW,
    _add_regime_features,
    _attach_fred_features,
    _compute_price_features,
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


# Date-coded FRED fixtures: each value encodes its observation date as days
# since _CODE_BASE (plus a per-series offset of 1000/2000), so tests can
# recover exactly which observation a bar received from the merged value.
_CODE_BASE = pd.Timestamp("2022-12-01")
_SERIES_OFFSETS = {"DGS10": 0.0, "DFF": 1000.0, "VIXCLS": 2000.0}


def _encode_dates(dates: pd.DatetimeIndex) -> np.ndarray:
    naive = dates.tz_convert(None) if dates.tz is not None else dates
    return (naive.normalize() - _CODE_BASE).days.to_numpy(dtype=float)


def _decode_obs_date(value: float) -> pd.Timestamp:
    return _CODE_BASE + pd.Timedelta(days=int(value) % 1000)


def _fred_date_coded(start: str = "2022-12-26", periods: int = 15) -> pd.DataFrame:
    """Business-day FRED wide frame with date-encoded values."""
    dates = pd.bdate_range(start, periods=periods, tz="UTC")
    code = _encode_dates(dates)
    return pd.DataFrame(
        {series: code + offset for series, offset in _SERIES_OFFSETS.items()},
        index=dates,
    )


def _fred_daily_coded(start: str = "2022-12-26", periods: int = 19) -> pd.DataFrame:
    """Calendar-day FRED wide frame mimicking _load_fred_wide output.

    DFF carries genuine date-coded values every calendar day (it publishes
    on weekends); DGS10 and VIXCLS publish business days only, so their
    weekend rows are forward-filled smears of Friday's value — exactly the
    shape _load_fred_wide produces after its pivot + ffill.
    """
    dates = pd.date_range(start, periods=periods, freq="D", tz="UTC")
    code = _encode_dates(dates)
    is_bday = dates.dayofweek < 5
    cols = {}
    for series, offset in _SERIES_OFFSETS.items():
        values = pd.Series(code + offset, index=dates)
        if series != "DFF":
            values[~is_bday] = np.nan
        cols[series] = values
    return pd.DataFrame(cols, index=dates).ffill()


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


class TestFredPublicationLags:
    # Calendar anchors (2023): Jan 6 = Friday, Jan 9 = Monday, Jan 10 = Tuesday.
    FRI = pd.Timestamp("2023-01-06", tz="UTC")
    MON = pd.Timestamp("2023-01-09", tz="UTC")
    TUE = pd.Timestamp("2023-01-10", tz="UTC")

    def test_pinned_lags_constant(self):
        assert FRED_PUBLICATION_LAGS == {"DGS10": 1, "DFF": 1, "VIXCLS": 1}

    def test_lag1_tuesday_bar_receives_monday_obs(self):
        feats = _compute_price_features(_ohlcv(10))
        fred = _fred_date_coded()

        merged = _attach_fred_features(feats, fred, publication_lags=FRED_PUBLICATION_LAGS)

        for series in _FRED_SERIES:
            obs = _decode_obs_date(merged.loc[self.TUE, series])
            assert obs == self.MON.tz_convert(None), (
                f"{series}: Tuesday bar must receive Monday's observation under lag=1, got {obs}"
            )

    def test_none_tuesday_bar_receives_same_day_obs(self):
        feats = _compute_price_features(_ohlcv(10))
        fred = _fred_date_coded()

        merged = _attach_fred_features(feats, fred, publication_lags=None)

        for series in _FRED_SERIES:
            obs = _decode_obs_date(merged.loc[self.TUE, series])
            assert obs == self.TUE.tz_convert(None), (
                f"{series}: legacy join must give Tuesday the same-day observation, got {obs}"
            )

    def test_lag1_monday_bar_receives_friday_obs_no_weekend_smear(self):
        # Daily frame: DFF has genuine Sat/Sun observations, DGS10/VIXCLS
        # weekend rows are ffilled Friday smears. Under lag=1 the Monday bar
        # must see at most Friday's observation for every series — the
        # weekend rows must neither leak (DFF) nor smear unshifted values
        # back over the shift (DGS10/VIXCLS).
        feats = _compute_price_features(_ohlcv(10))
        fred = _fred_daily_coded()

        merged = _attach_fred_features(feats, fred, publication_lags=FRED_PUBLICATION_LAGS)

        for series in _FRED_SERIES:
            obs = _decode_obs_date(merged.loc[self.MON, series])
            assert obs == self.FRI.tz_convert(None), (
                f"{series}: Monday bar must receive Friday's observation under lag=1, got {obs}"
            )

    def test_invariant_received_obs_at_most_bar_minus_lag_bdays(self):
        feats = _compute_price_features(_ohlcv(10))
        fred = _fred_daily_coded()
        lag = 1

        merged = _attach_fred_features(
            feats, fred, publication_lags={s: lag for s in _FRED_SERIES}
        )

        for bar in merged.index:
            cutoff = (bar.tz_convert(None) - pd.offsets.BDay(lag)).normalize()
            for series in _FRED_SERIES:
                value = merged.loc[bar, series]
                if np.isnan(value):
                    continue
                obs = _decode_obs_date(value)
                assert obs <= cutoff, (
                    f"{series} at bar {bar.date()}: received obs {obs.date()} "
                    f"> cutoff {cutoff.date()} (t − {lag} business days)"
                )

    def test_none_reproduces_legacy_join_bit_for_bit(self):
        feats = _compute_price_features(_ohlcv(20))
        fred = _fred_wide(10)

        merged = _attach_fred_features(feats, fred, publication_lags=None)

        # Independent reference for the legacy backward-asof semantics:
        # reindex FRED onto the union of dates, ffill, evaluate at bar dates.
        union = fred.index.union(feats.index)
        expected = fred.reindex(union).ffill().loc[feats.index]
        for series in _FRED_SERIES:
            pd.testing.assert_series_equal(
                merged[series], expected[series], check_names=False, check_freq=False
            )

    def test_per_series_lags_shift_independently(self):
        feats = _compute_price_features(_ohlcv(10))
        fred = _fred_date_coded()

        merged = _attach_fred_features(
            feats, fred, publication_lags={"DGS10": 2, "DFF": 1}
        )

        assert _decode_obs_date(merged.loc[self.TUE, "DGS10"]) == self.FRI.tz_convert(None)
        assert _decode_obs_date(merged.loc[self.TUE, "DFF"]) == self.MON.tz_convert(None)
        # VIXCLS absent from the mapping → unshifted (legacy same-day join).
        assert _decode_obs_date(merged.loc[self.TUE, "VIXCLS"]) == self.TUE.tz_convert(None)

    def test_yield_curve_computed_from_shifted_series(self):
        feats = _compute_price_features(_ohlcv(10))
        fred = _fred_date_coded()

        merged = _attach_fred_features(
            feats, fred, publication_lags={"DGS10": 2, "DFF": 1}
        )

        expected = merged["DGS10"] - merged["DFF"]
        pd.testing.assert_series_equal(merged["yield_curve"], expected, check_names=False)

    def test_negative_lag_raises(self):
        feats = _compute_price_features(_ohlcv(10))
        fred = _fred_date_coded()
        with pytest.raises(ValueError, match="must be >= 0"):
            _attach_fred_features(feats, fred, publication_lags={"DGS10": -1})

    def test_empty_fred_with_lags_fills_nan(self):
        feats = _compute_price_features(_ohlcv(10))
        merged = _attach_fred_features(feats, pd.DataFrame(), publication_lags=FRED_PUBLICATION_LAGS)
        for col in _FRED_SERIES:
            assert merged[col].isna().all()
        assert merged["yield_curve"].isna().all()

    def test_build_features_default_applies_pinned_lags(self, monkeypatch):
        monkeypatch.setattr(
            "quant.features.engineering._load_fred_wide",
            lambda con: _fred_date_coded(),
        )
        result = build_features(["AAPL"], {"AAPL": _ohlcv(10)})

        obs = _decode_obs_date(result["AAPL"].loc[self.TUE, "DGS10"])
        assert obs == self.MON.tz_convert(None), (
            "build_features default must apply the pinned publication lags"
        )

    def test_build_features_none_gives_legacy_output(self, monkeypatch):
        monkeypatch.setattr(
            "quant.features.engineering._load_fred_wide",
            lambda con: _fred_date_coded(),
        )
        result = build_features(
            ["AAPL"], {"AAPL": _ohlcv(10)}, fred_publication_lags=None
        )

        obs = _decode_obs_date(result["AAPL"].loc[self.TUE, "DGS10"])
        assert obs == self.TUE.tz_convert(None), (
            "fred_publication_lags=None must reproduce the legacy unlagged join"
        )


class TestLoadFredWide:
    """Regression: observation-date extraction must be timezone-independent.

    FRED observation dates are stored as UTC-midnight TIMESTAMPTZ values
    (ingest/fred_macro.py). The loader previously extracted the date with a
    SQL ``CAST(timestamp AS DATE)``, which DuckDB evaluates in the *session*
    timezone — on any US-timezone machine that rotated every observation
    date back one calendar day, handing bar t the t+1 observation under the
    unlagged join (discovered in nb07 §2). The date must come out identical
    regardless of session timezone.
    """

    def _write_fred(self, dates: pd.DatetimeIndex) -> None:
        from quant.storage import lake

        df = pd.DataFrame(
            {
                "timestamp": dates,
                "series_id": "DGS10",
                "value": np.arange(len(dates), dtype=float),
                "ingested_at": pd.Timestamp.now(tz="UTC"),
            }
        )
        lake.write_processed(df, dataset="macro_fred", partition_cols=None)

    @pytest.mark.parametrize(
        "session_tz", ["UTC", "America/New_York", "Asia/Tokyo"]
    )
    def test_observation_dates_survive_session_timezone(self, lake_root, session_tz):
        import duckdb

        from quant.features.engineering import _load_fred_wide

        obs_dates = pd.bdate_range("2023-01-02", periods=5, tz="UTC")
        self._write_fred(obs_dates)

        con = duckdb.connect()
        try:
            con.execute(f"SET TimeZone = '{session_tz}'")
            wide = _load_fred_wide(con)
        finally:
            con.close()

        assert list(wide.index) == list(obs_dates), (
            f"observation dates rotated under session timezone {session_tz}"
        )
        # Value k encodes obs date k — the join key must not have rotated.
        assert wide["DGS10"].tolist() == [float(k) for k in range(5)]


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

    def test_asof_none_matches_full_history(self, monkeypatch):
        """asof=None (default) must reproduce the full-history output bit-for-bit
        (C1-M2 A/B-safe lever, mirroring fred_publication_lags=None)."""
        prices = {"AAPL": _ohlcv(60)}
        monkeypatch.setattr(
            "quant.features.engineering._load_fred_wide",
            lambda con: pd.DataFrame(),
        )
        full = build_features(["AAPL"], prices)["AAPL"]
        explicit = build_features(["AAPL"], prices, asof=None)["AAPL"]
        pd.testing.assert_frame_equal(full, explicit)

    def test_asof_truncates_without_changing_retained_rows(self, monkeypatch):
        """A truncated build's rows equal the full build's rows up to asof —
        the structural G2 train/serve-parity guarantee (storage/realtime.py)."""
        prices = {"AAPL": _ohlcv(60)}
        monkeypatch.setattr(
            "quant.features.engineering._load_fred_wide",
            lambda con: pd.DataFrame(),
        )
        cutoff = prices["AAPL"].index[40]
        full = build_features(["AAPL"], prices)["AAPL"].loc[:cutoff]
        live = build_features(["AAPL"], prices, asof=cutoff)["AAPL"]
        pd.testing.assert_frame_equal(full, live)


class TestRegimeFeatures:
    REGIME_COLS = ("vix_regime", "curve_inverted", "vol_regime_ratio", "trend_regime")

    @staticmethod
    def _fred_vix(values: list[float], start: str = "2023-01-02") -> pd.DataFrame:
        dates = pd.bdate_range(start, periods=len(values), tz="UTC")
        return pd.DataFrame(
            {"DGS10": 4.0, "DFF": 5.0, "VIXCLS": values}, index=dates, dtype=float
        )

    def _build(self, monkeypatch, fred: pd.DataFrame, n: int = 30) -> pd.DataFrame:
        monkeypatch.setattr(
            "quant.features.engineering._load_fred_wide", lambda con: fred
        )
        return build_features(["AAPL"], {"AAPL": _ohlcv(n)})["AAPL"]

    def test_thresholds_imported_from_detector_defaults(self):
        from dataclasses import fields

        defaults = {f.name: f.default for f in fields(VIXThresholdDetector)}
        assert VIX_REGIME_LOW == defaults["low"]
        assert VIX_REGIME_HIGH == defaults["high"]

    def test_vix_regime_matches_detector_convention(self, monkeypatch):
        # Span both thresholds, including exact boundary values.
        fred = self._fred_vix([10.0, 15.0, 20.0, 25.0, 30.0, 14.9, 25.1, 18.0])
        feat = self._build(monkeypatch, fred)

        vix = feat["VIXCLS"].dropna()
        detector = VIXThresholdDetector(vix_series=vix)
        labels = detector.label(pd.DatetimeIndex(vix.index))
        expected = labels.map(
            {"low_vol": 0.0, "mid_vol": 1.0, "high_vol": 2.0}
        ).astype(float)

        pd.testing.assert_series_equal(
            feat.loc[vix.index, "vix_regime"], expected, check_names=False
        )

    def test_vix_regime_boundary_values(self, monkeypatch):
        fred = self._fred_vix([15.0, 25.0, 20.0, 15.0, 25.0, 20.0, 15.0, 25.0])
        feat = self._build(monkeypatch, fred)

        at_low = feat["VIXCLS"] == 15.0
        at_high = feat["VIXCLS"] == 25.0
        assert at_low.any() and at_high.any()
        # Detector convention: vix <= low → low_vol(0), vix >= high → high_vol(2).
        assert (feat.loc[at_low, "vix_regime"] == 0.0).all()
        assert (feat.loc[at_high, "vix_regime"] == 2.0).all()

    def test_nan_vixcls_and_yield_curve_propagate(self, monkeypatch):
        # FRED starts two weeks after the bars: early bars have NaN macro values.
        fred = self._fred_vix([20.0] * 5, start="2023-01-16")
        feat = self._build(monkeypatch, fred)

        early = feat.index < pd.Timestamp("2023-01-16", tz="UTC")
        assert early.any()
        assert feat.loc[early, "VIXCLS"].isna().all()
        assert feat.loc[early, "vix_regime"].isna().all()
        assert feat.loc[early, "curve_inverted"].isna().all()

    def test_curve_inverted_values(self):
        idx = pd.bdate_range("2023-01-02", periods=3, tz="UTC")
        feat = pd.DataFrame(
            {
                "vol_21d": 0.1,
                "vol_63d": 0.2,
                "ma200_ratio": 1.0,
                "VIXCLS": 20.0,
                "yield_curve": [-0.5, 0.5, np.nan],
            },
            index=idx,
        )

        out = _add_regime_features(feat)

        assert out["curve_inverted"].iloc[0] == 1.0
        assert out["curve_inverted"].iloc[1] == 0.0
        assert np.isnan(out["curve_inverted"].iloc[2])

    def test_trend_regime_values(self):
        idx = pd.bdate_range("2023-01-02", periods=4, tz="UTC")
        feat = pd.DataFrame(
            {
                "vol_21d": 0.1,
                "vol_63d": 0.2,
                "ma200_ratio": [1.1, 0.9, 1.0, np.nan],
                "VIXCLS": 20.0,
                "yield_curve": 1.0,
            },
            index=idx,
        )

        out = _add_regime_features(feat)

        assert out["trend_regime"].iloc[0] == 1.0
        assert out["trend_regime"].iloc[1] == 0.0
        assert out["trend_regime"].iloc[2] == 0.0, "ma200_ratio == 1 is not a trend"
        assert np.isnan(out["trend_regime"].iloc[3])

    def test_vol_regime_ratio_zero_denominator_is_nan_not_inf(self):
        idx = pd.bdate_range("2023-01-02", periods=2, tz="UTC")
        feat = pd.DataFrame(
            {
                "vol_21d": [0.1, 0.2],
                "vol_63d": [0.0, 0.1],
                "ma200_ratio": 1.0,
                "VIXCLS": 20.0,
                "yield_curve": 1.0,
            },
            index=idx,
        )

        out = _add_regime_features(feat)

        assert np.isnan(out["vol_regime_ratio"].iloc[0])
        assert not np.isinf(out["vol_regime_ratio"]).any()
        assert out["vol_regime_ratio"].iloc[1] == pytest.approx(2.0)

    def test_no_fred_path_regime_columns_exist(self, monkeypatch):
        feat = self._build(monkeypatch, pd.DataFrame(), n=300)

        for col in self.REGIME_COLS:
            assert col in feat.columns
        assert feat["vix_regime"].isna().all()
        assert feat["curve_inverted"].isna().all()
        assert feat["vol_regime_ratio"].notna().sum() > 0
        assert feat["trend_regime"].notna().sum() > 0

    def test_column_count_with_fred(self, monkeypatch):
        feat = self._build(monkeypatch, _fred_wide(30))
        assert len(feat.columns) == 21  # 17 base + 4 regime

    def test_column_count_with_sentiment(self, monkeypatch):
        monkeypatch.setattr(
            "quant.features.engineering._load_fred_wide",
            lambda con: _fred_wide(30),
        )
        scored = pd.DataFrame(
            {
                "symbol": ["AAPL"],
                "published_at": pd.to_datetime(["2023-01-02"], utc=True),
                "sentiment_score": [0.5],
                "document_id": ["doc_0"],
            }
        )
        feat = build_features(["AAPL"], {"AAPL": _ohlcv(30)}, sentiment_df=scored)["AAPL"]
        assert len(feat.columns) == 24  # 17 base + 4 regime + 3 sentiment

    def test_regime_columns_appended_after_base(self, monkeypatch):
        feat = self._build(monkeypatch, _fred_wide(30))
        assert list(feat.columns)[-4:] == list(self.REGIME_COLS)

    def test_mom_21d_positional_contract(self, monkeypatch):
        # nb02 MomentumBaseline reads mom_21d positionally at index 5.
        feat = self._build(monkeypatch, _fred_wide(30))
        assert list(feat.columns).index("mom_21d") == 5


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
