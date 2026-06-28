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

import pandas as pd

# Default GitHub repository the Provenance / Trial Registry commit links resolve
# to (DECISIONS.md decision 5: links go to github.com/James-Delgado/quant).
DEFAULT_REPO_URL = "https://github.com/James-Delgado/quant"

# Staleness threshold (calendar days) past which a daily feed is "stale".
# Pinned per METHODOLOGY §1 — a daily cadence tolerates a long weekend + a
# holiday before it is genuinely behind.
FRESH_THRESHOLD_DAYS = 4.0


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
            feature_monitor_fn=None,  # live lake monitor wired in a follow-up
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
