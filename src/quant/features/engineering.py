"""Point-in-time correct feature engineering.

All features are timestamped to when they were knowable — no lookahead.

FRED macro data is joined with a backward ASOF merge on **observation
date**: each bar receives the most recent FRED observation whose
(publication-lag-shifted) observation date <= bar date. The lake stores
observation dates, not publication dates, and DFF/DGS10 are published the
*next* business day — so by default each series' observation dates are
shifted forward by its pinned publication lag (`FRED_PUBLICATION_LAGS`,
business days) before the merge, ensuring the model only sees values that
were publicly knowable at the close of the bar. Evidence, the decision-time
convention, and the update protocol live in
docs/concepts/fred-publication-lag.md. Passing `fred_publication_lags=None`
to build_features() reproduces the legacy unlagged join (Phase 2.5/3
results predate the lag fix).

The wide FRED frame is loaded with a single DuckDB query and merged in
pandas — O(T) per-bar catalog.query() calls are explicitly avoided.

FRED series used:
  - DGS10  10-Year Treasury yield   (H.15 release; FRED vintage next business day → lag 1)
  - DFF    Fed Funds effective rate (NY Fed EFFR, next business day ~9am ET → lag 1)
  - VIXCLS CBOE Volatility Index    (Cboe close ~4:15pm ET, after the 4:00pm
                                     signal close → lag 1 by decision-time convention)

Derived macro features (computed post-merge from the shifted series):
  - yield_curve = DGS10 − DFF  (term spread; negative = inverted curve)

Regime-indicator features (Phase 4A Milestone 3, appended after the 17 base
columns on both FRED paths — NaN-propagating throughout):
  - vix_regime       ordinal {0,1,2} from VIXCLS using the thresholds pinned
                     in backtest/regimes.py (VIXThresholdDetector defaults)
  - curve_inverted   binary: yield_curve < 0
  - vol_regime_ratio vol_21d / vol_63d (vol_63d == 0 → NaN, not inf)
  - trend_regime     binary: ma200_ratio > 1

Excluded:
  - CPI, UNRATE — subject to large revisions released weeks after reference
    period; the lake keeps latest vintage only (see ingest/fred_macro.py),
    so using them would require a separate real-time vintage data source.
"""
from __future__ import annotations

import logging
import warnings
from collections.abc import Mapping
from dataclasses import fields as _dataclass_fields

import duckdb
import numpy as np
import pandas as pd

from quant.backtest.regimes import VIXThresholdDetector
from quant.features.sentiment import aggregate_sentiment
from quant.storage.catalog import processed_glob

logger = logging.getLogger(__name__)

# FRED series that are safe to use — negligible revision risk under the
# lake's latest-vintage-only storage (see docs/concepts/fred-publication-lag.md).
_FRED_SERIES: tuple[str, ...] = ("DGS10", "DFF", "VIXCLS")

# Pinned publication lags in business days — verified against ALFRED vintage
# metadata on 2026-06-12 (VIXCLS pinned by decision-time convention, not
# ALFRED). Pre-committed on correctness grounds; do NOT retune to make a
# model pass. Rationale: docs/concepts/fred-publication-lag.md.
FRED_PUBLICATION_LAGS: dict[str, int] = {"DGS10": 1, "DFF": 1, "VIXCLS": 1}

# VIX regime thresholds — read from VIXThresholdDetector's dataclass defaults
# (backtest/regimes.py, Milestone 1) so the M1 evaluation axis and the M3
# vix_regime feature can never drift apart. Single source of truth: do NOT
# re-type the numbers here.
_VIX_DETECTOR_DEFAULTS = {
    f.name: f.default for f in _dataclass_fields(VIXThresholdDetector)
}
VIX_REGIME_LOW: float = _VIX_DETECTOR_DEFAULTS["low"]
VIX_REGIME_HIGH: float = _VIX_DETECTOR_DEFAULTS["high"]


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


def _shift_bdays(index: pd.DatetimeIndex, lag: int) -> pd.DatetimeIndex:
    """Shift observation dates forward by `lag` business days.

    Weekend observation dates (DFF publishes every calendar day) first roll
    forward to the next business day, then count `lag` from there — a Sunday
    observation with lag=1 becomes available Tuesday, matching the NY Fed's
    actual EFFR release schedule for weekend rates.
    """
    naive = index.tz_convert(None) if index.tz is not None else index
    shifted = np.busday_offset(naive.values.astype("datetime64[D]"), lag, roll="forward")
    # busday_offset returns datetime64[D]; restore the source resolution so
    # the merge keys keep a single unit.
    out = pd.DatetimeIndex(shifted.astype(naive.values.dtype))
    return out.tz_localize(index.tz) if index.tz is not None else out


def _apply_publication_lags(
    fred_df: pd.DataFrame,
    publication_lags: Mapping[str, int],
) -> pd.DataFrame:
    """Re-index each FRED series to its publication-lag-shifted dates.

    The input frame may already be forward-filled (`_load_fred_wide` ffills
    weekend/holiday gaps). Shifting the ffilled values is safe: an ffilled
    entry duplicates an *older* observation, so shifting it can never move
    information earlier in time. When several observation dates collide on
    one availability date (e.g., Fri/Sat/Sun all becoming available Monday
    or Tuesday), the latest observation wins (`keep="last"` on the sorted
    series). The combined frame is re-ffilled so series with different lags
    stay dense for the row-wise asof merge.
    """
    negative = {k: v for k, v in publication_lags.items() if v < 0}
    if negative:
        raise ValueError(f"publication lags must be >= 0 (got {negative})")
    shifted: dict[str, pd.Series] = {}
    for col in fred_df.columns:
        s = fred_df[col].dropna()
        lag = publication_lags.get(col, 0)
        if lag > 0 and not s.empty:
            s = pd.Series(s.to_numpy(), index=_shift_bdays(s.index, lag), name=col)
            s = s[~s.index.duplicated(keep="last")]
        shifted[col] = s
    return pd.DataFrame(shifted).sort_index().ffill()


def _attach_fred_features(
    bars: pd.DataFrame,
    fred_df: pd.DataFrame,
    publication_lags: Mapping[str, int] | None = None,
) -> pd.DataFrame:
    """Attach FRED macro features to bar data using a pandas ASOF merge.

    For each bar date, attaches the most recent FRED observation whose
    (shifted) observation date <= bar date — identical semantics to a
    DuckDB ASOF JOIN.

    When `publication_lags` is provided, each series' observation dates are
    shifted forward by its lag (business days) *before* the merge, so bar t
    only sees observations dated <= t − lag business days. The shift is
    applied per series via `_apply_publication_lags` — see its docstring for
    why composing the shift with the upstream weekend ffill is leak-safe.
    With `publication_lags=None` the legacy unlagged join is reproduced
    bit-for-bit.

    Parameters
    ----------
    bars:             Bar-level DataFrame with DatetimeIndex.
    fred_df:          FRED data in wide format: one column per series_id,
                      DatetimeIndex sorted ascending.
    publication_lags: Optional {series_id: lag in business days}. Series
                      missing from the mapping are left unshifted.
    """
    if fred_df.empty:
        out = bars.copy()
        for col in _FRED_SERIES:
            out[col] = np.nan
        out["yield_curve"] = np.nan
        return out

    if publication_lags:
        fred_df = _apply_publication_lags(fred_df, publication_lags)

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


def _add_regime_features(feat: pd.DataFrame) -> pd.DataFrame:
    """Append regime-indicator columns derived from existing feature columns.

    Columns are appended AFTER all existing ones — the positional contract
    of the 17 base columns must not change (nb02's MomentumBaseline reads
    ``mom_21d`` at index 5).

    Runs as a post-pass on BOTH FRED paths: when the lake has no FRED data
    the FRED-derived sources (``VIXCLS``, ``yield_curve``) are absent from
    ``feat``, and the dependent regime columns are emitted as all-NaN so the
    output column set is identical either way. Price-derived regime columns
    (``vol_regime_ratio``, ``trend_regime``) compute regardless.

    ``vix_regime`` follows VIXThresholdDetector's boundary convention:
    ``VIXCLS <= low`` → 0, ``VIXCLS >= high`` → 2, else 1; NaN propagates.
    """
    out = feat.copy()
    nan_col = pd.Series(np.nan, index=out.index)

    vix = out["VIXCLS"] if "VIXCLS" in out.columns else nan_col
    vix_regime = pd.Series(1.0, index=out.index)
    vix_regime[vix <= VIX_REGIME_LOW] = 0.0
    vix_regime[vix >= VIX_REGIME_HIGH] = 2.0
    vix_regime[vix.isna()] = np.nan
    out["vix_regime"] = vix_regime

    curve = out["yield_curve"] if "yield_curve" in out.columns else nan_col
    out["curve_inverted"] = (curve < 0).astype(float).where(curve.notna())

    out["vol_regime_ratio"] = out["vol_21d"] / out["vol_63d"].replace(0, np.nan)

    ma200 = out["ma200_ratio"]
    out["trend_regime"] = (ma200 > 1).astype(float).where(ma200.notna())
    return out


def _load_fred_wide(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Load approved FRED series from the lake and pivot to wide format.

    A single SQL query per connection — not one per bar.
    Returns empty DataFrame if the lake has no FRED data yet.

    Observation dates are stored as UTC-midnight TIMESTAMPTZ values (see
    ingest/fred_macro.py), so the observation date is extracted in pandas
    with an explicit UTC conversion. The previous implementation used a SQL
    ``CAST(timestamp AS DATE)``, which DuckDB evaluates in the *session*
    timezone (system-local by default): on any US-timezone machine that
    shifted every observation date back one calendar day, silently handing
    bar t the t+1 observation under the unlagged join. Discovered and
    measured in nb07 §2; see docs/concepts/fred-publication-lag.md.
    """
    glob = processed_glob("macro_fred")
    # Use positional parameters for the series filter rather than f-string
    # interpolation, so the list cannot become an injection vector if
    # _FRED_SERIES is ever made configurable.
    placeholders = ", ".join("?" * len(_FRED_SERIES))
    sql = f"""
        SELECT
            timestamp,
            series_id,
            value
        FROM read_parquet('{glob}', hive_partitioning = true)
        WHERE series_id IN ({placeholders})
        ORDER BY series_id, timestamp
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

    # Timezone-independent date extraction: convert to UTC, then truncate to
    # midnight. Never CAST to DATE in SQL — that uses the session timezone.
    df["date"] = pd.to_datetime(df["timestamp"], utc=True).dt.normalize()
    wide = df.pivot_table(index="date", columns="series_id", values="value", aggfunc="last")
    wide.index = pd.to_datetime(wide.index, utc=True)
    wide.columns.name = None
    # DFF publishes every calendar day; DGS10 and VIXCLS only publish on
    # market days (Mon–Thu, skipping holidays). Forward-fill so weekend and
    # holiday rows carry the last known value, preventing the ASOF join from
    # returning NaN for Friday market bars.
    wide = wide.sort_index().ffill()
    return wide


def _truncate_prices_asof(prices: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    """Drop bars with ``timestamp > asof`` from a price frame.

    The index may be tz-aware (NY or UTC) or naive; *asof* is a tz-aware UTC
    instant. A naive index is treated as UTC for the comparison (the lake's
    storage convention). Comparison is by instant, so a NY-stamped index and a
    UTC *asof* align correctly. The retained rows are byte-identical to the
    untruncated frame — this is the train/serve-parity lever (every feature is
    backward-looking, so dropping future bars cannot change a retained row).
    """
    idx = prices.index
    cmp_idx = idx.tz_localize("UTC") if idx.tz is None else idx
    return prices[cmp_idx <= asof]


def build_features(
    symbols: list[str],
    prices_by_symbol: dict[str, pd.DataFrame],
    sentiment_df: pd.DataFrame | None = None,
    sentiment_lookback_days: int = 30,
    fred_publication_lags: Mapping[str, int] | None = FRED_PUBLICATION_LAGS,
    asof: pd.Timestamp | None = None,
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
                             Existing callers passing None get the 21-column
                             output (17 base + 4 regime indicators).
    sentiment_lookback_days: Rolling window for sentiment aggregation (days).
    fred_publication_lags:   {series_id: lag in business days} applied to FRED
                             observation dates before the asof merge. Defaults
                             to the pinned FRED_PUBLICATION_LAGS. Pass None to
                             reproduce the legacy unlagged join (A/B control
                             arm; matches Phase 2.5/3 historical results).
    asof:                    Optional tz-aware UTC instant. When set, each price
                             frame is truncated to bars with timestamp <= asof
                             *before* features are computed, so the output is the
                             same-day feature matrix knowable at `asof` with no
                             look-ahead (C1-M2 live-inference path). Default None
                             keeps the full-history batch behaviour bit-for-bit
                             (the same A/B-safe pattern as fred_publication_lags).
                             Because every feature is backward-looking, a row
                             retained under truncation equals the batch row for
                             that date — this is the G2 train/serve-parity
                             guarantee (see storage/realtime.py).

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

    asof_ts: pd.Timestamp | None = None
    if asof is not None:
        asof_ts = pd.Timestamp(asof)
        if asof_ts.tz is None:
            asof_ts = asof_ts.tz_localize("UTC")
        else:
            asof_ts = asof_ts.tz_convert("UTC")

    result: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        prices = prices_by_symbol[sym]
        if asof_ts is not None:
            prices = _truncate_prices_asof(prices, asof_ts)
        price_feats = _compute_price_features(prices)
        feat = (
            _attach_fred_features(price_feats, fred_wide, fred_publication_lags)
            if not fred_wide.empty
            else price_feats
        )
        feat = _add_regime_features(feat)

        if sentiment_df is not None and not sentiment_df.empty:
            sent = aggregate_sentiment(
                sym, feat.index, sentiment_df, lookback_days=sentiment_lookback_days
            )
            feat = feat.join(sent[["sentiment_score", "doc_count", "has_coverage"]])

        result[sym] = feat
    return result
