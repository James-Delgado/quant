"""Artifact sources for the console readers.

:class:`ConsoleSources` bundles every external input the readers touch — root
paths, the GitHub repo URL, and *injectable* callables for the lake and the
(optional) feature monitor. Production code uses :meth:`ConsoleSources.default`;
tests construct a ``ConsoleSources`` pointing at a synthetic ``tmp_path`` so they
never depend on the real (gitignored) ``data/`` tree.

This module also holds the low-level file readers (checkpoint discovery,
``metadata.json`` parsing, ``oos_returns.parquet`` loading) so the higher-level
view-model assembly in :mod:`quant.console.readers` stays readable.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# Default GitHub repository the Provenance / Trial Registry commit links resolve
# to (DECISIONS.md decision 5: links go to github.com/James-Delgado/quant).
DEFAULT_REPO_URL = "https://github.com/James-Delgado/quant"

# Staleness threshold (calendar days) past which a daily feed is "stale".
# Pinned per METHODOLOGY §1 — a daily cadence tolerates a long weekend + a
# holiday before it is genuinely behind.
FRESH_THRESHOLD_DAYS = 4.0

# ── Feature-monitor tunables (pinned; METHODOLOGY §1) ────────────────────────
# Histogram bins for the per-feature distribution mini the catalog panel renders.
FEATURE_HIST_BINS = 20
# Recent window (in distinct trading dates) compared against the earlier
# baseline when judging distribution drift of a feature.
FEATURE_DRIFT_RECENT_BARS = 252  # ≈ one trading year
# A feature is "drifting" when the recent-vs-baseline standardized shift of its
# per-date cross-sectional mean reaches this many baseline standard deviations.
FEATURE_DRIFT_Z_THRESHOLD = 1.0
# Distribution-SHAPE drift (E1-FEATURE-MONITOR-DRIFT-PSI). The mean-shift signal
# above only moves on a per-date cross-sectional MEAN change, so a variance/shape
# shift that leaves the mean put goes unflagged. A Population Stability Index (PSI)
# over the recent-vs-baseline windows is the second drift signal: a feature is
# "drifting" if EITHER the mean-shift z OR the PSI trips its threshold. The bands
# are the TEXTBOOK PSI convention (not a novel research threshold): PSI < 0.1
# stable, 0.1–0.25 moderate, > 0.25 a significant population shift. We trip
# "drifting" at the significant band. Pinned per METHODOLOGY §1 — mirrors the
# FEATURE_DRIFT_Z_THRESHOLD pin (a code-comment pin; no ledger.yaml row, as the
# existing monitor thresholds are likewise pinned only in code).
FEATURE_PSI_BINS = 10  # decile bins — the textbook PSI convention
FEATURE_PSI_DRIFT_THRESHOLD = 0.25  # textbook "significant population shift" band
FEATURE_PSI_EPSILON = 1e-4  # proportion floor so an empty bin keeps ln() finite
# Minimum observations PER BIN each window must hold before a PSI is trusted — the
# textbook expected-count ≥ 5 rule for binned distribution comparisons. Below
# ``FEATURE_PSI_BINS × FEATURE_PSI_MIN_PER_BIN`` observations in either window the
# PSI is too sampling-noisy to act on, so it degrades to ``None`` (no drift signal)
# rather than firing on noise. Pinned per METHODOLOGY §1.
FEATURE_PSI_MIN_PER_BIN = 5
# A feature is "stale" when its most recent non-null observation is more than
# this many distinct trading dates behind the panel's last date.
FEATURE_STALE_BARS = 21  # ≈ one trading month
# Sentiment aggregation window for the monitor's feature build — matches the
# Phase 3 / Phase 4A runner convention so the monitored columns equal production.
FEATURE_SENTIMENT_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class FeedSpec:
    """A lake dataset surfaced as a Data-panel feed."""

    key: str  # dataset name in the lake
    label: str  # UI label
    ts_col: str = "timestamp"


DEFAULT_FEEDS: tuple[FeedSpec, ...] = (
    FeedSpec("equity_bars_daily", "Daily equity bars", ts_col="timestamp"),
    FeedSpec("macro_fred", "FRED macro series", ts_col="timestamp"),
    FeedSpec("text_documents", "Filings & news", ts_col="published_at"),
    FeedSpec("sentiment_scored", "Sentiment scores", ts_col="scored_at"),
)

# FRED series shown in the market snapshot, mapped to MarketSnapshot fields.
MARKET_SERIES = {
    "VIXCLS": "vix",
    "DGS10": "ten_year",
    "DFF": "fed_funds",
}

# Benchmark symbol for the Overview hero overlay (E1-M3-OVERVIEW-BENCHMARK).
# SPY buy-and-hold is the practically-relevant benchmark a model must beat
# (mirrors models/buyandhold_baseline.py). Pinned per METHODOLOGY §1.
BENCHMARK_SYMBOL = "SPY"


# A function returning the most recent timestamp for a lake dataset (or None).
LatestTimestampFn = Callable[[str, str], "dt.datetime | None"]
# A function returning the latest value for a FRED series (or None).
MarketValueFn = Callable[[str], "float | None"]
# A function returning the full history of a FRED series as a date-indexed
# (normalized, tz-naive) float Series, or None when the series is unavailable.
# Distinct from ``MarketValueFn`` (latest scalar) — the Conditions panel needs
# the whole series to label the OOS calendar by market regime.
MarketSeriesFn = Callable[[str], "pd.Series | None"]
# A function returning the benchmark (SPY) adjusted-close price history as a
# date-indexed (normalized, tz-naive) float Series, or None when unavailable.
# Nullary because the benchmark symbol is pinned (``BENCHMARK_SYMBOL``); the
# Overview reader turns this into a buy-and-hold growth series.
BenchmarkPriceFn = Callable[[], "pd.Series | None"]
# A function returning monitoring stats for a feature, or None if unavailable.
FeatureMonitorFn = Callable[[str], "dict | None"]
# A clock, injectable for deterministic age computation in tests.
NowFn = Callable[[], "dt.datetime"]


@dataclass(frozen=True)
class ConsoleSources:
    """Every external input the console readers depend on (all injectable)."""

    data_root: Path
    ledger_path: Path
    catalog_path: Path
    strategy_roots: tuple[Path, ...]
    repo_url: str = DEFAULT_REPO_URL
    feeds: tuple[FeedSpec, ...] = DEFAULT_FEEDS
    # The C6 strategy registry the Portfolio panel reads. ``None`` falls back to
    # the committed default (``strategy_registry.DEFAULT_REGISTRY_PATH``) so tests
    # can point it at a synthetic registry without wiring the real artifact.
    registry_path: Path | None = None
    latest_timestamp_fn: LatestTimestampFn | None = None
    market_value_fn: MarketValueFn | None = None
    market_series_fn: MarketSeriesFn | None = None
    benchmark_price_fn: BenchmarkPriceFn | None = None
    # Display identity of the benchmark ``benchmark_price_fn`` computes, carried
    # into the export so the UI legend names the real series rather than a stale
    # frontend constant (E1-M3-BENCHMARK-COST-NAME). Defaults to the pinned
    # ``BENCHMARK_SYMBOL``; co-located with ``benchmark_price_fn`` so a test (or a
    # re-pin) sets price and name together.
    benchmark_name: str = BENCHMARK_SYMBOL
    feature_monitor_fn: FeatureMonitorFn | None = None
    now_fn: NowFn | None = None

    @classmethod
    def default(cls, *, feature_monitor: bool = True) -> ConsoleSources:
        """Production sources wired to ``settings`` + the storage lake.

        ``feature_monitor=False`` (the ``console export --no-monitor`` gate,
        E1-M1-FEATURE-MONITOR-EXPORT-COST) leaves ``feature_monitor_fn`` unset, so
        ``load_catalog`` renders registry-only rows and the export skips the ~1–2
        min full-panel build entirely — a fast schema-only export. The default
        (``True``) wires the lake-backed monitor over the *disk-cached* panel
        provider so repeat exports reuse a previously built panel when the lake
        and universe are unchanged.
        """
        # Imported lazily so importing the view-model/schema layer never pulls in
        # settings validation (which requires API credentials at import time).
        from quant.config import settings
        from quant.execution.strategy_registry import DEFAULT_REGISTRY_PATH
        from quant.features.catalog import DEFAULT_CATALOG_PATH
        from quant.storage import catalog as storage_catalog

        data_root = settings.data_root

        def _latest(dataset: str, ts_col: str = "timestamp") -> dt.datetime | None:
            return storage_catalog.latest_timestamp(dataset, ts_col=ts_col)

        def _market(series_id: str) -> float | None:
            return _latest_fred_value(storage_catalog, series_id)

        def _market_series(series_id: str) -> pd.Series | None:
            return _fred_series(storage_catalog, series_id)

        def _benchmark_price() -> pd.Series | None:
            return _benchmark_price_series(storage_catalog, BENCHMARK_SYMBOL)

        return cls(
            data_root=data_root,
            ledger_path=data_root / "ledger.yaml",
            catalog_path=Path(DEFAULT_CATALOG_PATH),
            strategy_roots=(data_root / "phase4a",),
            registry_path=Path(DEFAULT_REGISTRY_PATH),
            latest_timestamp_fn=_latest,
            market_value_fn=_market,
            market_series_fn=_market_series,
            benchmark_price_fn=_benchmark_price,
            # Lake-backed monitor (E1-M1-FEATURE-MONITOR) over the disk-cached panel
            # provider (E1-M1-FEATURE-MONITOR-EXPORT-COST). The panel build is invoked
            # lazily on the first ``load_catalog`` call and memoized per process; the
            # disk cache reuses it across processes (repeat exports) until the lake or
            # universe changes. A missing lake degrades to registry-only rows rather
            # than failing the export. ``feature_monitor=False`` disables it outright.
            feature_monitor_fn=(
                build_feature_monitor(_cached_feature_panel) if feature_monitor else None
            ),
            now_fn=lambda: dt.datetime.now(dt.timezone.utc),
        )

    def now(self) -> dt.datetime:
        return self.now_fn() if self.now_fn else dt.datetime.now(dt.timezone.utc)

    def commit_url(self, sha: str | None) -> str | None:
        if not sha:
            return None
        return f"{self.repo_url}/commit/{sha}"


def _latest_fred_value(storage_catalog, series_id: str) -> float | None:
    """Most recent value for a FRED series from the lake, or None if absent."""
    try:
        sql = (
            f"SELECT value FROM {storage_catalog.table('macro_fred')} "
            f"WHERE series_id = '{series_id}' ORDER BY timestamp DESC LIMIT 1"
        )
        df = storage_catalog.query(sql)
    except Exception:
        return None
    if df.empty or pd.isna(df.iloc[0]["value"]):
        return None
    return float(df.iloc[0]["value"])


def _fred_series(storage_catalog, series_id: str) -> pd.Series | None:
    """Full history of a FRED series as a date-indexed (tz-naive) float Series.

    Dates are extracted timezone-independently — ``to_datetime(..., utc=True)``
    then ``normalize()`` — never a SQL ``CAST(timestamp AS DATE)``, which DuckDB
    evaluates in the *session* timezone and would shift every observation back a
    calendar day on a US-timezone machine (the FRED publication-lag pitfall
    documented in ``features/engineering._load_fred_wide`` / nb07). The returned
    index carries midnight-UTC calendar dates with the tz dropped so it aligns
    by date with the OOS return calendar in :mod:`quant.console.readers`.
    """
    try:
        sql = (
            f"SELECT timestamp, value FROM {storage_catalog.table('macro_fred')} "
            f"WHERE series_id = '{series_id}' ORDER BY timestamp"
        )
        df = storage_catalog.query(sql)
    except Exception:
        return None
    if df.empty:
        return None
    dates = pd.to_datetime(df["timestamp"], utc=True).dt.normalize().dt.tz_localize(None)
    series = pd.Series(
        pd.to_numeric(df["value"], errors="coerce").to_numpy(),
        index=pd.DatetimeIndex(dates),
    )
    series = series[~series.index.duplicated(keep="last")].sort_index().dropna()
    return series if not series.empty else None


def _benchmark_price_series(storage_catalog, symbol: str) -> pd.Series | None:
    """Adjusted-close history for ``symbol`` as a date-indexed tz-naive Series.

    Reads the Tiingo adjusted EOD table — the same ``adjClose`` column the Phase
    4A arms trade — so the Overview benchmark is cost-consistent with the
    strategies it overlays. Dates are extracted timezone-independently then
    normalized to midnight-UTC calendar dates with the tz dropped (the same
    NY↔UTC alignment care as :func:`_fred_series`), so the series aligns by date
    with the OOS return calendar in :mod:`quant.console.readers`. Returns
    ``None`` on any failure / empty result (honest degrade, METHODOLOGY §9).
    """
    safe_symbol = symbol.replace("'", "''")
    try:
        sql = (
            f"SELECT timestamp, adjClose FROM {storage_catalog.table('equity_eod_tiingo')} "
            f"WHERE symbol = '{safe_symbol}' ORDER BY timestamp"
        )
        df = storage_catalog.query(sql)
    except Exception:
        return None
    if df.empty:
        return None
    dates = pd.to_datetime(df["timestamp"], utc=True).dt.normalize().dt.tz_localize(None)
    series = pd.Series(
        pd.to_numeric(df["adjClose"], errors="coerce").to_numpy(),
        index=pd.DatetimeIndex(dates),
    )
    series = series[~series.index.duplicated(keep="last")].sort_index().dropna()
    return series if not series.empty else None


# ── Feature monitor (lake-backed coverage / mu-sigma / distribution / drift) ──
#
# The Feature Catalog panel (PRD §5, DECISIONS §8) is a *monitoring* surface, not
# just a registry: each registered feature shows coverage, mean/std, a
# distribution mini, and a stable/drifting/stale verdict. The reader
# (``readers.load_catalog``) consumes a per-feature ``dict`` via an injectable
# ``feature_monitor_fn``; this section builds that monitor from the lake.
#
# Honesty (METHODOLOGY §9): an absent/empty lake, a failed build, or a feature
# the panel does not produce all degrade to "unmonitored" (the reader renders
# registry-only rows) — never to fabricated stats.


def _feature_distribution(values: np.ndarray, bins: int) -> list[int] | None:
    """Histogram counts over a feature's own value range (None when empty)."""
    if values.size == 0:
        return None
    counts, _edges = np.histogram(values, bins=bins)
    return [int(c) for c in counts]


def _population_stability_index(
    baseline: np.ndarray,
    recent: np.ndarray,
    *,
    bins: int,
    epsilon: float,
    min_per_bin: int = FEATURE_PSI_MIN_PER_BIN,
) -> float | None:
    """Population Stability Index of ``recent`` vs ``baseline`` (None if undefined).

    PSI = Σ_b (rₐ − bₐ)·ln(rₐ / bₐ), summed over bins b, where bₐ / rₐ are the
    baseline / recent proportions falling in bin b. Bin edges are the baseline's
    ``bins``-quantiles (the textbook decile-PSI convention); values are clipped to
    the baseline range so recent observations beyond it bin into the extremes.
    Empty bins are floored at ``epsilon`` so ``ln()`` stays finite.

    Returns ``None`` when either window holds fewer than ``bins × min_per_bin``
    observations (too sampling-noisy to trust), when either window is empty, or
    when the baseline is degenerate (constant → fewer than two distinct quantile
    edges → no distribution shape to compare). Honest degrade (METHODOLOGY §9): an
    undefined PSI is ``None``, never a fabricated 0.0.
    """
    if baseline.size == 0 or recent.size == 0:
        return None
    if baseline.size < bins * min_per_bin or recent.size < bins * min_per_bin:
        return None  # too few samples per bin → PSI is noise, not signal
    quantiles = np.linspace(0.0, 1.0, bins + 1)
    edges = np.unique(np.quantile(baseline, quantiles))
    if edges.size < 2:
        return None  # constant baseline → no shape to compare
    lo, hi = float(edges[0]), float(edges[-1])
    base_counts, _ = np.histogram(np.clip(baseline, lo, hi), bins=edges)
    recent_counts, _ = np.histogram(np.clip(recent, lo, hi), bins=edges)
    base_pct = base_counts / base_counts.sum()
    recent_pct = recent_counts / recent_counts.sum()
    base_pct = np.where(base_pct == 0.0, epsilon, base_pct)
    recent_pct = np.where(recent_pct == 0.0, epsilon, recent_pct)
    return float(np.sum((recent_pct - base_pct) * np.log(recent_pct / base_pct)))


def _drift_psi(
    column: pd.Series,
    *,
    recent_bars: int,
    psi_bins: int,
    psi_epsilon: float,
    psi_min_per_bin: int = FEATURE_PSI_MIN_PER_BIN,
) -> float | None:
    """PSI of the column's last ``recent_bars`` dates vs the earlier baseline.

    Partitions the column's *raw* valid observations (all ``(symbol, date)`` rows,
    not the per-date mean) by date into a baseline window and the recent window, so
    a cross-sectional variance/shape shift — invisible to the per-date mean-shift
    signal — is captured. Returns ``None`` with too little history to split
    (mirrors the mean-shift guard) or when the PSI is otherwise undefined.
    """
    valid = column.dropna()
    if valid.empty:
        return None
    norm_dates = pd.DatetimeIndex(valid.index).normalize()
    unique_dates = norm_dates.unique().sort_values()
    if len(unique_dates) <= recent_bars + 1:
        return None  # not enough baseline history to judge drift
    recent_dates = unique_dates[-recent_bars:]
    in_recent = norm_dates.isin(recent_dates)
    values = valid.to_numpy(dtype=float)
    return _population_stability_index(
        values[~in_recent],
        values[in_recent],
        bins=psi_bins,
        epsilon=psi_epsilon,
        min_per_bin=psi_min_per_bin,
    )


def _stability_verdict(
    column: pd.Series,
    *,
    recent_bars: int,
    drift_z_threshold: float,
    stale_bars: int,
    psi: float | None,
    psi_drift_threshold: float,
) -> str:
    """Classify a feature column as ``stable`` | ``drifting`` | ``stale``.

    *stale*    — the column's most recent non-null observation is more than
                 ``stale_bars`` distinct trading dates behind the panel's last
                 date (the feature stopped updating), or it is entirely null.
    *drifting* — EITHER drift signal trips: (1) the standardized shift of the
                 feature's per-date cross-sectional mean over the last
                 ``recent_bars`` dates versus the earlier baseline reaches
                 ``drift_z_threshold`` baseline σ, OR (2) the distribution-shape
                 ``psi`` (precomputed via :func:`_drift_psi`) reaches
                 ``psi_drift_threshold`` (the textbook "significant" PSI band).
                 The PSI signal catches a variance/shape shift that leaves the
                 mean — and hence signal (1) — unmoved.
    *stable*   — otherwise (including too little history to judge drift).

    Point-in-time and direction-agnostic: only the recorded panel is used; no
    future observation enters either window.
    """
    valid = column.dropna()
    if valid.empty:
        return "stale"

    all_dates = pd.DatetimeIndex(column.index).normalize().unique().sort_values()
    last_valid = pd.DatetimeIndex(valid.index).normalize().max()
    if int((all_dates > last_valid).sum()) > stale_bars:
        return "stale"

    psi_drift = psi is not None and psi >= psi_drift_threshold

    by_date = (
        valid.groupby(pd.DatetimeIndex(valid.index).normalize()).mean().sort_index()
    )
    if len(by_date) <= recent_bars + 1:
        # Not enough baseline history for the mean-shift signal; PSI may still flag.
        return "drifting" if psi_drift else "stable"

    baseline = by_date.iloc[:-recent_bars]
    recent = by_date.iloc[-recent_bars:]
    shift = abs(float(recent.mean()) - float(baseline.mean()))
    base_std = float(baseline.std(ddof=1))
    if not np.isfinite(base_std) or base_std == 0.0:
        # Flat baseline: any real move off it is drift; no move is stable.
        mean_drift = shift > 0.0
    else:
        mean_drift = (shift / base_std) >= drift_z_threshold
    return "drifting" if (mean_drift or psi_drift) else "stable"


def _compute_feature_stats(
    panel: pd.DataFrame | None,
    *,
    hist_bins: int,
    recent_bars: int,
    drift_z_threshold: float,
    stale_bars: int,
    psi_bins: int,
    psi_drift_threshold: float,
    psi_epsilon: float,
    psi_min_per_bin: int,
) -> dict[str, dict]:
    """Per-column monitoring stats for a pooled (date-indexed) feature panel.

    ``panel`` rows are ``(symbol, date)`` observations stacked vertically with a
    normalized tz-naive DatetimeIndex; columns are feature names. Returns
    ``{feature: {coverage, mean, std, stability, distribution, psi}}`` — the dict
    shape ``readers.load_catalog`` reads (it ignores the extra ``psi`` key). An
    empty/absent panel yields ``{}``.
    """
    stats: dict[str, dict] = {}
    if panel is None or panel.empty:
        return stats
    for col in panel.columns:
        column = panel[col]
        total = int(column.shape[0])
        valid = column.dropna()
        n_valid = int(valid.shape[0])
        coverage = (n_valid / total) if total else 0.0
        if n_valid == 0:
            stats[str(col)] = {
                "coverage": 0.0,
                "mean": None,
                "std": None,
                "stability": "stale",
                "distribution": None,
                "psi": None,
            }
            continue
        arr = valid.to_numpy(dtype=float)
        psi = _drift_psi(
            column,
            recent_bars=recent_bars,
            psi_bins=psi_bins,
            psi_epsilon=psi_epsilon,
            psi_min_per_bin=psi_min_per_bin,
        )
        stats[str(col)] = {
            "coverage": float(coverage),
            "mean": float(np.mean(arr)),
            "std": float(np.std(arr, ddof=1)) if n_valid > 1 else 0.0,
            "stability": _stability_verdict(
                column,
                recent_bars=recent_bars,
                drift_z_threshold=drift_z_threshold,
                stale_bars=stale_bars,
                psi=psi,
                psi_drift_threshold=psi_drift_threshold,
            ),
            "distribution": _feature_distribution(arr, hist_bins),
            "psi": psi,
        }
    return stats


# A function returning a pooled, date-indexed feature panel (or None/empty).
FeaturePanelFn = Callable[[], "pd.DataFrame | None"]


def build_feature_monitor(
    panel_fn: FeaturePanelFn | None,
    *,
    hist_bins: int = FEATURE_HIST_BINS,
    recent_bars: int = FEATURE_DRIFT_RECENT_BARS,
    drift_z_threshold: float = FEATURE_DRIFT_Z_THRESHOLD,
    stale_bars: int = FEATURE_STALE_BARS,
    psi_bins: int = FEATURE_PSI_BINS,
    psi_drift_threshold: float = FEATURE_PSI_DRIFT_THRESHOLD,
    psi_epsilon: float = FEATURE_PSI_EPSILON,
    psi_min_per_bin: int = FEATURE_PSI_MIN_PER_BIN,
) -> "FeatureMonitorFn":
    """Build a memoized feature monitor from a (lazy) feature-panel provider.

    The returned ``monitor(name)`` computes every feature's stats once — on the
    first call, by invoking ``panel_fn()`` and pooling the panel — then serves
    cached lookups. ``None`` is returned for an unmonitored feature (not in the
    panel) so the catalog reader renders a registry-only row. Any failure while
    loading/building the panel is swallowed and leaves the monitor empty
    (honest degrade — never fabricated stats, METHODOLOGY §9).
    """
    cache: dict[str, dict] = {}
    state = {"loaded": False}

    def monitor(name: str) -> dict | None:
        if not state["loaded"]:
            state["loaded"] = True
            try:
                panel = panel_fn() if panel_fn is not None else None
                cache.update(
                    _compute_feature_stats(
                        panel,
                        hist_bins=hist_bins,
                        recent_bars=recent_bars,
                        drift_z_threshold=drift_z_threshold,
                        stale_bars=stale_bars,
                        psi_bins=psi_bins,
                        psi_drift_threshold=psi_drift_threshold,
                        psi_epsilon=psi_epsilon,
                        psi_min_per_bin=psi_min_per_bin,
                    )
                )
            except Exception:
                cache.clear()  # unmonitored, not faked
        return cache.get(name)

    return monitor


def _load_prices_for_panel(storage_catalog, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Adjusted OHLCV per symbol from the lake (mirrors the Phase 4A runner).

    Returns ``{}`` on any failure / empty lake so the monitor degrades honestly.
    """
    if not symbols:
        return {}
    syms_sql = ", ".join("'" + s.replace("'", "''") + "'" for s in symbols)
    try:
        eq = storage_catalog.query(
            f"SELECT symbol, timestamp, open, high, low, adjClose, volume "
            f"FROM {storage_catalog.table('equity_eod_tiingo')} "
            f"WHERE symbol IN ({syms_sql}) ORDER BY symbol, timestamp"
        )
    except Exception:
        return {}
    if eq.empty:
        return {}
    eq["timestamp"] = pd.to_datetime(eq["timestamp"])
    eq = eq.set_index("timestamp")
    prices: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        sub = eq[eq["symbol"] == sym][["open", "high", "low", "adjClose", "volume"]].copy()
        if sub.empty:
            continue
        prices[sym] = sub.rename(columns={"adjClose": "close"}).sort_index().dropna()
    return prices


def _panel_from_features(features_by_symbol: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    """Stack per-symbol feature frames into one normalized-date-indexed panel.

    Each frame's index is converted to tz-naive UTC then normalized to the
    calendar date — the documented NY↔UTC alignment care (``project_lake_tz``
    memory; mirrors ``run_b1_arms._to_naive_utc``) — so the pooled per-date drift
    grouping lines up by date across symbols with no off-by-one.
    """
    frames: list[pd.DataFrame] = []
    for _sym, fdf in features_by_symbol.items():
        frame = fdf.copy()
        idx = pd.DatetimeIndex(frame.index)
        if idx.tz is not None:
            idx = idx.tz_convert("UTC").tz_localize(None)
        frame.index = idx.normalize()
        frames.append(frame)
    if not frames:
        return None
    return pd.concat(frames, axis=0).sort_index()


def _load_feature_panel() -> pd.DataFrame | None:
    """Production feature-panel provider: build the full universe from the lake.

    Loads ``settings.equity_universe`` prices, builds the production feature
    matrix (``build_features`` + the M3 cross-sectional survivor), and pools it.
    Returns ``None`` when the lake has no usable bars so the monitor stays empty.
    """
    from quant.config import settings
    from quant.features.cross_sectional import add_cross_sectional_features
    from quant.features.engineering import FRED_PUBLICATION_LAGS, build_features
    from quant.storage import catalog as storage_catalog
    from quant.storage import lake

    symbols = list(settings.equity_universe)
    prices = _load_prices_for_panel(storage_catalog, symbols)
    if not prices:
        return None

    try:
        sentiment = lake.read_processed("sentiment_scored")
    except Exception:
        sentiment = None
    sentiment_arg = sentiment if (sentiment is not None and not sentiment.empty) else None

    features = build_features(
        list(prices),
        prices,
        sentiment_df=sentiment_arg,
        sentiment_lookback_days=FEATURE_SENTIMENT_LOOKBACK_DAYS,
        fred_publication_lags=FRED_PUBLICATION_LAGS,
    )
    try:
        features = add_cross_sectional_features(features, columns=("vol_21d",))
    except Exception:
        pass  # the xs-rank survivor is optional; monitor the base columns anyway
    return _panel_from_features(features)


# ── Feature-panel disk cache (E1-M1-FEATURE-MONITOR-EXPORT-COST) ──────────────
#
# Building the production feature panel (build_features over the full universe +
# FRED + sentiment) costs ~1–2 min. ``console export`` runs in a fresh process
# each time, so ``build_feature_monitor``'s per-process memo never helps a repeat
# export. This disk cache lets repeat exports reuse a previously built panel as
# long as the lake inputs and the universe are unchanged.
#
# Correctness over speed (METHODOLOGY §9): the cache key folds in the universe and
# a fingerprint of the processed parquet feeding the panel, so ANY re-ingest
# (``write_processed`` rewrites part files → new size/mtime) or universe change
# changes the key and forces a rebuild — a stale panel is never served. Every
# cache read/write is guarded: a corrupt file, an unwritable dir, or unresolved
# settings falls back to a fresh (uncached) build and never raises. The cache is
# an optimization, not a correctness dependency.

# Bump when the panel-build logic (columns produced, build_features semantics)
# changes shape, so an old cache file is never reused under new code. Pinned §1.
FEATURE_PANEL_CACHE_VERSION = 1
# Processed lake datasets whose freshness invalidates the cached panel — exactly
# the inputs ``_load_feature_panel`` reads (prices + FRED + sentiment). Pinned §1.
PANEL_LAKE_DATASETS: tuple[str, ...] = (
    "equity_eod_tiingo",
    "macro_fred",
    "sentiment_scored",
)
# Cache directory name under the data root (a sibling of the phase4a checkpoints).
CACHE_DIR_NAME = "console_cache"


def _lake_fingerprint(processed_dir: Path, datasets: tuple[str, ...]) -> str:
    """Stable digest of the processed parquet feeding the panel (size + mtime).

    Hashes the sorted ``relpath:size:mtime_ns`` of every ``*.parquet`` under each
    dataset directory. A re-ingest (``write_processed`` rewrites part files) bumps
    size/mtime and changes the digest; an unchanged lake yields the same digest on
    every call. Absent dataset dirs and unreadable files contribute nothing.
    """
    parts: list[str] = []
    for dataset in datasets:
        base = processed_dir / dataset
        if not base.exists():
            continue
        for pq in sorted(base.rglob("*.parquet")):
            try:
                st = pq.stat()
            except OSError:
                continue
            rel = pq.relative_to(processed_dir)
            parts.append(f"{rel}:{st.st_size}:{st.st_mtime_ns}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _feature_panel_cache_key(universe: tuple[str, ...], processed_dir: Path) -> str:
    """Cache key folding the version, the sorted universe, and the lake digest."""
    payload = "|".join(
        (
            f"v{FEATURE_PANEL_CACHE_VERSION}",
            ",".join(sorted(universe)),
            _lake_fingerprint(processed_dir, PANEL_LAKE_DATASETS),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cached_feature_panel(
    panel_fn: FeaturePanelFn = _load_feature_panel,
    *,
    cache_dir: Path | None = None,
    processed_dir: Path | None = None,
    universe: tuple[str, ...] | None = None,
) -> pd.DataFrame | None:
    """Disk-cached wrapper around ``panel_fn`` keyed by universe + lake freshness.

    On a cache hit the stored parquet is read and returned (no rebuild). On a miss
    the panel is built via ``panel_fn`` and best-effort written for next time. The
    three keying inputs default to ``settings`` (production); tests inject them to
    drive the cache over a synthetic lake. Any failure — unresolved settings, a
    corrupt/unreadable cache file, an unwritable cache dir — degrades to a fresh
    build and never raises (METHODOLOGY §9).
    """
    if processed_dir is None or universe is None or cache_dir is None:
        try:
            from quant.config import settings

            if processed_dir is None:
                processed_dir = settings.processed_dir
            if universe is None:
                universe = tuple(settings.equity_universe)
            if cache_dir is None:
                cache_dir = settings.data_root / CACHE_DIR_NAME
        except Exception:
            return panel_fn()  # cannot resolve cache context → build uncached

    try:
        key = _feature_panel_cache_key(universe, processed_dir)
        cache_path = cache_dir / f"feature_panel_{key}.parquet"
    except Exception:
        return panel_fn()

    if cache_path.exists():
        try:
            return pd.read_parquet(cache_path)
        except Exception:
            pass  # corrupt / stale-format cache → rebuild below

    panel = panel_fn()
    if panel is not None and not panel.empty:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            panel.to_parquet(cache_path)
        except Exception:
            pass  # write is best-effort; the built panel is still returned
    return panel


# ── Low-level checkpoint readers ─────────────────────────────────────────────


@dataclass(frozen=True)
class ArmCheckpoint:
    """A discovered strategy checkpoint directory and its parsed metadata."""

    id: str
    path: Path
    metadata: dict


def discover_strategies(sources: ConsoleSources) -> list[ArmCheckpoint]:
    """Find non-smoke strategy checkpoints (``metadata.json`` + ``oos_returns``).

    Scans each ``strategy_roots`` directory's immediate children. A child is a
    strategy iff it holds both ``metadata.json`` and ``oos_returns.parquet`` and
    its metadata ``smoke`` flag is not ``True``. Results are sorted by id for
    deterministic export ordering.
    """
    found: list[ArmCheckpoint] = []
    for root in sources.strategy_roots:
        if not root.exists():
            continue
        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            meta_path = child / "metadata.json"
            returns_path = child / "oos_returns.parquet"
            if not (meta_path.exists() and returns_path.exists()):
                continue
            meta = read_metadata(meta_path)
            if meta.get("smoke") is True:
                continue
            arm_id = str(meta.get("arm") or child.name)
            found.append(ArmCheckpoint(id=arm_id, path=child, metadata=meta))
    found.sort(key=lambda c: c.id)
    return found


def read_metadata(path: Path) -> dict:
    """Parse a checkpoint ``metadata.json``."""
    with Path(path).open("r") as f:
        return json.load(f)


def is_git_sha(value: str | None) -> bool:
    """True iff ``value`` is a 40-char lowercase-hex string — a git commit SHA.

    The shared predicate that distinguishes a real commit (linkable to
    ``github.com/.../commit/<sha>``) from a 64-hex *content* hash. Pinned shape
    per METHODOLOGY §1; used by both the checkpoint join below and the Trial
    Registry reader.
    """
    return bool(value) and len(value) == 40 and all(c in "0123456789abcdef" for c in value)


def checkpoint_git_sha_index(sources: ConsoleSources) -> dict[str, str]:
    """Map each run's content ``config_hash`` to its real git ``git_sha``.

    Scans every ``metadata.json`` under ``sources.data_root`` (the phase4a / b1 /
    b2 / … run trees the ledger's ``artifacts`` point into) and records the run's
    git commit keyed by its content hash. The Trial Registry reader
    (:func:`quant.console.readers.load_ledger`) uses this to resolve a commit link
    for ledger entries whose ``config_hash`` is a 64-hex content hash rather than a
    40-hex git SHA — the real commit lives in the matching checkpoint, not in the
    ledger row. ``config_hash`` is the join key because it is recorded verbatim in
    *both* the ledger entry and the checkpoint metadata, uniquely tying one to the
    other (the ledger ``artifacts`` path is the human-facing pointer to the same
    directory).

    Honest degrade (METHODOLOGY §9): a missing ``data_root``, an unreadable
    ``metadata.json``, or a checkpoint without a git-sha-shaped ``git_sha`` (e.g.
    an audit run that recorded ``git_sha: null``) contributes no mapping — so its
    ledger row stays link-less rather than pointing at a non-commit.
    """
    index: dict[str, str] = {}
    root = sources.data_root
    if not root.exists():
        return index
    for meta_path in sorted(root.rglob("metadata.json")):
        try:
            meta = read_metadata(meta_path)
        except Exception:
            continue
        config_hash = meta.get("config_hash")
        git_sha = meta.get("git_sha")
        if isinstance(config_hash, str) and is_git_sha(git_sha):
            index[config_hash] = git_sha
    return index


def artifacts_git_sha(
    sources: ConsoleSources,
    config_hash: str | None,
    artifacts: Sequence[str],
) -> str | None:
    """Resolve a run's git commit via its ledger ``artifacts`` paths.

    The fallback for :func:`checkpoint_git_sha_index`, which only sees checkpoints
    *under* ``data_root`` (its ``rglob``). A run whose checkpoint lives OUTSIDE the
    data root still records that directory in its ledger ``artifacts``; this reads
    the commit from there. Each ``artifacts`` entry is resolved relative to
    ``data_root.parent`` — the repo root the ledger records paths against (e.g.
    ``data/phase4a/arima/``) — then the ``metadata.json`` beside it is read: the
    directory itself for a directory artifact, the parent directory for a file
    artifact. Its ``git_sha`` is returned only when the metadata's ``config_hash``
    equals the entry's — the same content-hash join key
    :func:`checkpoint_git_sha_index` uses, so an unrelated checkpoint sitting at
    the path never yields a spurious link. The first matching artifact wins.

    Honest degrade (METHODOLOGY §9): a missing ``config_hash``, an empty artifact
    list, a path that does not exist, an absent/unreadable ``metadata.json``, a
    ``config_hash`` mismatch, or a non-git-sha-shaped ``git_sha`` all contribute
    nothing — the row stays link-less rather than pointing at a non-commit.
    """
    if not config_hash:
        return None
    repo_root = sources.data_root.parent
    for art in artifacts:
        if not art:
            continue
        candidate = repo_root / art
        meta_dir = candidate if candidate.is_dir() else candidate.parent
        meta_path = meta_dir / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            meta = read_metadata(meta_path)
        except Exception:
            continue
        git_sha = meta.get("git_sha")
        if meta.get("config_hash") == config_hash and is_git_sha(git_sha):
            return git_sha
    return None


def read_oos_returns(path: Path) -> pd.Series:
    """Load an ``oos_returns.parquet`` as a tz-naive-indexed float Series.

    The checkpoint index is timezone-aware (America/New_York close stamps).
    We convert to UTC then drop the tz so downstream date formatting and the
    macro-era ``DateRangeDetector`` (which compares against naive Timestamps)
    behave consistently — the project's documented tz-alignment convention.
    """
    df = pd.read_parquet(Path(path))
    series = df.iloc[:, 0]
    idx = pd.DatetimeIndex(series.index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    series.index = idx
    return series.astype(float)
