"""Point-in-time correct feature engineering.

All features are timestamped to when they were knowable — no lookahead.

FRED macro data is joined via a DuckDB ASOF JOIN rather than per-bar
catalog.query() calls. ASOF JOIN runs in a single SQL query and attaches
the most recent FRED observation whose ingested_at <= bar_date for each bar.
Opening O(T) DuckDB connections (one per bar) is explicitly avoided.

FRED series used:
  - DGS10  10-Year Treasury yield   (same-day publication, no revision lag)
  - DFF    Fed Funds effective rate  (same-day publication, no revision lag)
  - VIXCLS CBOE Volatility Index     (same-day publication, no revision lag)

Derived macro features (computed post-merge, no additional ingestion):
  - yield_curve = DGS10 − DFF  (term spread; negative = inverted curve)

Excluded:
  - CPI, UNRATE — subject to large revisions released weeks after reference
    period; using real-time vintage requires a separate vintage data source.
"""
from __future__ import annotations

import logging
import warnings

import duckdb
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

from quant.features.sentiment import aggregate_sentiment
from quant.storage.catalog import processed_glob

# FRED series that are safe to use — no revision lag.
_FRED_SERIES: tuple[str, ...] = ("DGS10", "DFF", "VIXCLS")


def _rsi(close: pd.Series, window: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _compute_price_features(prices: pd.DataFrame) -> pd.DataFrame:
    """Compute price-derived features from an OHLCV DataFrame.

    All features use only past information relative to each bar.
    """
    close = prices["close"]
    ret = close.pct_change()

    feats: dict[str, pd.Series] = {
        "ret_1d": ret,
        "ret_5d": close.pct_change(5),
        "ret_21d": close.pct_change(21),
        "vol_21d": ret.rolling(21).std(),
        "vol_63d": ret.rolling(63).std(),
        "mom_21d": np.sign(ret.rolling(21).sum()),
        "rsi_14": _rsi(close, 14),
        "log_volume": np.log1p(prices["volume"]),
        # Trend / momentum / regime features (Phase 2.5)
        "ret_252d": close.pct_change(252),
        "ret_126d": close.pct_change(126),
        "ma200_ratio": close / close.rolling(200).mean(),
        "ma50_ratio": close / close.rolling(50).mean(),
        "volume_ratio": prices["volume"] / prices["volume"].rolling(63).mean(),
    }
    return pd.DataFrame(feats, index=prices.index)


def _attach_fred_features(
    bars: pd.DataFrame,
    fred_df: pd.DataFrame,
) -> pd.DataFrame:
    """Attach FRED macro features to bar data using a pandas ASOF merge.

    For each bar date, attaches the most recent FRED observation whose
    date <= bar date — identical semantics to a DuckDB ASOF JOIN.

    Parameters
    ----------
    bars:     Bar-level DataFrame with DatetimeIndex.
    fred_df:  FRED data in wide format: one column per series_id,
              DatetimeIndex sorted ascending.
    """
    if fred_df.empty:
        out = bars.copy()
        for col in _FRED_SERIES:
            out[col] = np.nan
        out["yield_curve"] = np.nan
        return out

    # Ensure we have a stable column name for the merge key regardless of
    # whether the caller's index is named.
    date_col = bars.index.name or "date"
    bars_reset = bars.copy()
    bars_reset.index.name = date_col
    bars_reset = bars_reset.reset_index()

    fred_reset = fred_df.copy()
    fred_reset.index.name = date_col
    fred_reset = fred_reset.reset_index()

    # Strip timezone to tz-naive UTC for merge — use tz_convert(None) so that
    # non-UTC inputs are first converted to UTC, then the tz label is dropped.
    # tz_localize(None) would re-label the wall-clock time as naive, which is
    # wrong for non-UTC timezones and produces an incorrect merge key.
    def _to_naive(col: pd.Series) -> pd.Series:
        col = pd.to_datetime(col)
        if col.dt.tz is not None:
            return col.dt.tz_convert(None)
        return col  # already naive — pass through

    bars_reset[date_col] = _to_naive(bars_reset[date_col])
    fred_reset[date_col] = _to_naive(fred_reset[date_col])

    merged = pd.merge_asof(
        bars_reset.sort_values(date_col),
        fred_reset.sort_values(date_col),
        on=date_col,
        direction="backward",
    )
    merged = merged.set_index(date_col)
    # Restore UTC timezone on the merged index so callers get a consistent
    # tz-aware DatetimeIndex regardless of whether FRED data was available.
    merged.index = pd.to_datetime(merged.index, utc=True)

    fred_cols = [c for c in _FRED_SERIES if c in merged.columns]
    if fred_cols:
        nan_frac = merged[fred_cols].isna().mean()
        bad = nan_frac[nan_frac > 0]
        if not bad.empty:
            warnings.warn(
                f"ASOF merge produced NaN in FRED columns — possible coverage gap "
                f"(bars predate earliest FRED observation): {bad.to_dict()}",
                stacklevel=3,
            )

    # Derived term spread: negative = inverted yield curve.
    if "DGS10" in merged.columns and "DFF" in merged.columns:
        merged["yield_curve"] = merged["DGS10"] - merged["DFF"]
    else:
        merged["yield_curve"] = np.nan

    return merged


def _load_fred_wide(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Load approved FRED series from the lake and pivot to wide format.

    A single SQL query per connection — not one per bar.
    Returns empty DataFrame if the lake has no FRED data yet.
    """
    glob = processed_glob("macro_fred")
    # Use positional parameters for the series filter rather than f-string
    # interpolation, so the list cannot become an injection vector if
    # _FRED_SERIES is ever made configurable.
    placeholders = ", ".join("?" * len(_FRED_SERIES))
    sql = f"""
        SELECT
            CAST(timestamp AS DATE) AS date,
            series_id,
            value
        FROM read_parquet('{glob}', hive_partitioning = true)
        WHERE series_id IN ({placeholders})
        ORDER BY series_id, date
    """
    try:
        df = con.execute(sql, list(_FRED_SERIES)).df()
    except (duckdb.IOException, duckdb.CatalogException) as exc:
        logger.warning(
            "FRED parquet load failed (%s) — macro features will be absent", exc
        )
        return pd.DataFrame()

    if df.empty:
        return pd.DataFrame()

    wide = df.pivot_table(index="date", columns="series_id", values="value", aggfunc="last")
    wide.index = pd.to_datetime(wide.index, utc=True)
    wide.columns.name = None
    # DFF publishes every calendar day; DGS10 and VIXCLS only publish on
    # market days (Mon–Thu, skipping holidays). Forward-fill so weekend and
    # holiday rows carry the last known value, preventing the ASOF join from
    # returning NaN for Friday market bars.
    wide = wide.sort_index().ffill()
    return wide


def build_features(
    symbols: list[str],
    prices_by_symbol: dict[str, pd.DataFrame],
    sentiment_df: pd.DataFrame | None = None,
    sentiment_lookback_days: int = 30,
) -> dict[str, pd.DataFrame]:
    """Build a point-in-time correct feature matrix for each symbol.

    Uses a single DuckDB connection for FRED data — not one per bar.
    Price-based features are computed in pandas.

    Parameters
    ----------
    symbols:                 Tickers to build features for.
    prices_by_symbol:        {symbol: OHLCV DataFrame} — same keys as symbols.
    sentiment_df:            Optional DataFrame from sentiment_scored/ dataset
                             (columns: symbol, published_at, sentiment_score).
                             When provided, adds sentiment_score, doc_count,
                             and has_coverage columns to every feature matrix.
                             Existing callers passing None get the original
                             17-column output unchanged.
    sentiment_lookback_days: Rolling window for sentiment aggregation (days).

    Returns
    -------
    {symbol: feature DataFrame} with the same DatetimeIndex as prices.
    Rows with NaN from rolling warmup windows are retained — callers
    must decide whether to dropna() before passing to run_backtest().
    """
    if not symbols:
        raise ValueError("symbols must not be empty")
    missing = [s for s in symbols if s not in prices_by_symbol]
    if missing:
        raise ValueError(f"prices_by_symbol is missing symbols: {missing}")

    con = duckdb.connect()
    try:
        fred_wide = _load_fred_wide(con)
    finally:
        con.close()

    result: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        price_feats = _compute_price_features(prices_by_symbol[sym])
        feat = (
            _attach_fred_features(price_feats, fred_wide)
            if not fred_wide.empty
            else price_feats
        )

        if sentiment_df is not None and not sentiment_df.empty:
            sent = aggregate_sentiment(
                sym, feat.index, sentiment_df, lookback_days=sentiment_lookback_days
            )
            feat = feat.join(sent[["sentiment_score", "doc_count", "has_coverage"]])

        result[sym] = feat
    return result
