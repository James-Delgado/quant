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
import json
from collections.abc import Callable
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


# A function returning the most recent timestamp for a lake dataset (or None).
LatestTimestampFn = Callable[[str, str], "dt.datetime | None"]
# A function returning the latest value for a FRED series (or None).
MarketValueFn = Callable[[str], "float | None"]
# A function returning the full history of a FRED series as a date-indexed
# (normalized, tz-naive) float Series, or None when the series is unavailable.
# Distinct from ``MarketValueFn`` (latest scalar) — the Conditions panel needs
# the whole series to label the OOS calendar by market regime.
MarketSeriesFn = Callable[[str], "pd.Series | None"]
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
    feature_monitor_fn: FeatureMonitorFn | None = None
    now_fn: NowFn | None = None

    @classmethod
    def default(cls) -> ConsoleSources:
        """Production sources wired to ``settings`` + the storage lake."""
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

        return cls(
            data_root=data_root,
            ledger_path=data_root / "ledger.yaml",
            catalog_path=Path(DEFAULT_CATALOG_PATH),
            strategy_roots=(data_root / "phase4a",),
            registry_path=Path(DEFAULT_REGISTRY_PATH),
            latest_timestamp_fn=_latest,
            market_value_fn=_market,
            market_series_fn=_market_series,
            # Lake-backed monitor (E1-M1-FEATURE-MONITOR). ``_load_feature_panel``
            # is invoked lazily on the first ``load_catalog`` call and memoized, so
            # constructing the sources stays cheap and a missing lake degrades to
            # registry-only rows rather than failing the export.
            feature_monitor_fn=build_feature_monitor(_load_feature_panel),
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


def _stability_verdict(
    column: pd.Series,
    *,
    recent_bars: int,
    drift_z_threshold: float,
    stale_bars: int,
) -> str:
    """Classify a feature column as ``stable`` | ``drifting`` | ``stale``.

    *stale*    — the column's most recent non-null observation is more than
                 ``stale_bars`` distinct trading dates behind the panel's last
                 date (the feature stopped updating), or it is entirely null.
    *drifting* — the standardized shift of the feature's per-date cross-sectional
                 mean over the last ``recent_bars`` dates versus the earlier
                 baseline reaches ``drift_z_threshold`` baseline σ.
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

    by_date = (
        valid.groupby(pd.DatetimeIndex(valid.index).normalize()).mean().sort_index()
    )
    if len(by_date) <= recent_bars + 1:
        return "stable"  # not enough baseline history to judge drift

    baseline = by_date.iloc[:-recent_bars]
    recent = by_date.iloc[-recent_bars:]
    shift = abs(float(recent.mean()) - float(baseline.mean()))
    base_std = float(baseline.std(ddof=1))
    if not np.isfinite(base_std) or base_std == 0.0:
        # Flat baseline: any real move off it is drift; no move is stable.
        return "drifting" if shift > 0.0 else "stable"
    return "drifting" if (shift / base_std) >= drift_z_threshold else "stable"


def _compute_feature_stats(
    panel: pd.DataFrame | None,
    *,
    hist_bins: int,
    recent_bars: int,
    drift_z_threshold: float,
    stale_bars: int,
) -> dict[str, dict]:
    """Per-column monitoring stats for a pooled (date-indexed) feature panel.

    ``panel`` rows are ``(symbol, date)`` observations stacked vertically with a
    normalized tz-naive DatetimeIndex; columns are feature names. Returns
    ``{feature: {coverage, mean, std, stability, distribution}}`` — exactly the
    dict shape ``readers.load_catalog`` reads. An empty/absent panel yields ``{}``.
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
            }
            continue
        arr = valid.to_numpy(dtype=float)
        stats[str(col)] = {
            "coverage": float(coverage),
            "mean": float(np.mean(arr)),
            "std": float(np.std(arr, ddof=1)) if n_valid > 1 else 0.0,
            "stability": _stability_verdict(
                column,
                recent_bars=recent_bars,
                drift_z_threshold=drift_z_threshold,
                stale_bars=stale_bars,
            ),
            "distribution": _feature_distribution(arr, hist_bins),
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
