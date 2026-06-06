"""Tests for features/sentiment.py — validate_point_in_time and aggregate_sentiment."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.features.sentiment import aggregate_sentiment, validate_point_in_time


def _bar_dates(n: int = 10, start: str = "2023-01-03") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n, tz="UTC")


def _scored_df(symbol: str, pub_dates: list[str], scores: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": symbol,
        "published_at": pd.to_datetime(pub_dates, utc=True),
        "sentiment_score": scores,
        "document_id": [f"doc_{i}" for i in range(len(pub_dates))],
    })


# ---------------------------------------------------------------------------
# validate_point_in_time
# ---------------------------------------------------------------------------

class TestValidatePointInTime:
    def test_empty_df_passes(self):
        validate_point_in_time(pd.DataFrame())

    def test_valid_past_doc_passes(self):
        df = pd.DataFrame({
            "published_at_check": pd.to_datetime(["2023-01-02"], utc=True),
            "bar_date": pd.to_datetime(["2023-01-03"]),
        })
        validate_point_in_time(df)  # should not raise

    def test_future_dated_doc_raises(self):
        df = pd.DataFrame({
            "published_at_check": pd.to_datetime(["2023-01-04"], utc=True),
            "bar_date": pd.to_datetime(["2023-01-03"]),
        })
        with pytest.raises(ValueError, match="Point-in-time violation"):
            validate_point_in_time(df)

    def test_same_day_doc_raises(self):
        """published_at == bar_date must be rejected (strict less-than)."""
        df = pd.DataFrame({
            "published_at_check": pd.to_datetime(["2023-01-03"], utc=True),
            "bar_date": pd.to_datetime(["2023-01-03"]),
        })
        with pytest.raises(ValueError, match="Point-in-time violation"):
            validate_point_in_time(df)

    def test_reports_violation_count(self):
        df = pd.DataFrame({
            "published_at_check": pd.to_datetime(["2023-01-05", "2023-01-06"], utc=True),
            "bar_date": pd.to_datetime(["2023-01-03", "2023-01-03"]),
        })
        with pytest.raises(ValueError, match="2 document"):
            validate_point_in_time(df)


# ---------------------------------------------------------------------------
# aggregate_sentiment
# ---------------------------------------------------------------------------

class TestAggregateSentiment:
    def test_no_docs_returns_zeros(self):
        bars = _bar_dates(5)
        empty = pd.DataFrame(columns=["symbol", "published_at", "sentiment_score"])
        result = aggregate_sentiment("AAPL", bars, empty)
        assert result.shape == (5, 3)
        assert (result["sentiment_score"] == 0.0).all()
        assert (result["doc_count"] == 0).all()
        assert (~result["has_coverage"]).all()

    def test_symbol_not_in_df_returns_zeros(self):
        bars = _bar_dates(5)
        scored = _scored_df("MSFT", ["2023-01-02"], [0.5])
        result = aggregate_sentiment("AAPL", bars, scored)
        assert (result["sentiment_score"] == 0.0).all()

    def test_single_doc_aggregated_correctly(self):
        bars = _bar_dates(3, start="2023-01-05")
        scored = _scored_df("AAPL", ["2023-01-03"], [0.6])
        result = aggregate_sentiment("AAPL", bars, scored, lookback_days=10)
        assert result.iloc[0]["doc_count"] == 1
        assert abs(result.iloc[0]["sentiment_score"] - 0.6) < 1e-9
        assert bool(result.iloc[0]["has_coverage"]) is True

    def test_same_day_doc_excluded(self):
        """Doc published on bar_date must not be included (strict < required)."""
        bars = pd.DatetimeIndex([pd.Timestamp("2023-01-03", tz="UTC")])
        scored = _scored_df("AAPL", ["2023-01-03"], [0.9])
        result = aggregate_sentiment("AAPL", bars, scored, lookback_days=30)
        assert result.iloc[0]["doc_count"] == 0
        assert result.iloc[0]["sentiment_score"] == 0.0

    def test_multiple_docs_averaged(self):
        bars = _bar_dates(1, start="2023-01-10")
        scored = _scored_df("AAPL", ["2023-01-05", "2023-01-06", "2023-01-07"], [0.8, -0.2, 0.4])
        result = aggregate_sentiment("AAPL", bars, scored, lookback_days=30)
        expected = (0.8 + -0.2 + 0.4) / 3
        assert abs(result.iloc[0]["sentiment_score"] - expected) < 1e-9
        assert result.iloc[0]["doc_count"] == 3

    def test_lookback_window_respected(self):
        """Docs older than lookback_days should be excluded."""
        bars = _bar_dates(1, start="2023-03-01")
        scored = _scored_df("AAPL", ["2023-01-01", "2023-02-28"], [1.0, -1.0])
        result = aggregate_sentiment("AAPL", bars, scored, lookback_days=5)
        assert result.iloc[0]["doc_count"] == 1
        assert result.iloc[0]["sentiment_score"] == -1.0

    def test_lookback_boundary_doc_included(self):
        """Doc published exactly on bar_date - lookback_days is included (>= cutoff)."""
        bar_date = pd.Timestamp("2023-03-10", tz="UTC")
        bars = pd.DatetimeIndex([bar_date])
        lookback = 7
        pub_date = bar_date - pd.Timedelta(days=lookback)
        scored = _scored_df("AAPL", [pub_date.strftime("%Y-%m-%d")], [0.5])
        result = aggregate_sentiment("AAPL", bars, scored, lookback_days=lookback)
        assert result.iloc[0]["doc_count"] == 1
        assert abs(result.iloc[0]["sentiment_score"] - 0.5) < 1e-9

    def test_output_indexed_by_bar_dates(self):
        bars = _bar_dates(7)
        scored = _scored_df("AAPL", ["2023-01-02"], [0.5])
        result = aggregate_sentiment("AAPL", bars, scored)
        assert result.index.equals(bars)

    def test_output_columns_present(self):
        bars = _bar_dates(3)
        scored = _scored_df("AAPL", ["2023-01-02"], [0.1])
        result = aggregate_sentiment("AAPL", bars, scored)
        assert set(result.columns) == {"sentiment_score", "doc_count", "has_coverage"}


# ---------------------------------------------------------------------------
# engineering.py backward compat
# ---------------------------------------------------------------------------

class TestBuildFeaturesBackwardCompat:
    def _make_prices(self, n: int = 300, seed: int = 0) -> pd.DataFrame:
        dates = pd.bdate_range("2022-01-03", periods=n, tz="UTC")
        rng = np.random.default_rng(seed)
        close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        return pd.DataFrame({
            "open": close * 0.999, "high": close * 1.005,
            "low": close * 0.995, "close": close,
            "volume": rng.integers(100_000, 1_000_000, n).astype(float),
        }, index=dates)

    def test_no_sentiment_returns_base_cols(self):
        from quant.features.engineering import build_features
        prices = self._make_prices()
        result = build_features(["AAPL"], {"AAPL": prices})
        # 13 price cols minimum; FRED may not be present in test env
        assert len(result["AAPL"].columns) >= 13
        assert "sentiment_score" not in result["AAPL"].columns

    def test_with_sentiment_adds_3_cols(self):
        from quant.features.engineering import build_features
        prices = self._make_prices()
        scored = pd.DataFrame({
            "symbol": ["AAPL"],
            "published_at": pd.to_datetime(["2022-01-03"], utc=True),
            "sentiment_score": [0.5],
            "document_id": ["doc_0"],
        })
        base = build_features(["AAPL"], {"AAPL": prices})
        with_sent = build_features(["AAPL"], {"AAPL": prices}, sentiment_df=scored)
        assert len(with_sent["AAPL"].columns) == len(base["AAPL"].columns) + 3
        for col in ("sentiment_score", "doc_count", "has_coverage"):
            assert col in with_sent["AAPL"].columns
