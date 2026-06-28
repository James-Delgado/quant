"""Same-day point-in-time bar reader for live inference (C1-M2).

The batch lake path answers *"given history up to date T, what is the feature
matrix for T?"* and reads the whole lake at leisure. Same-day inference asks a
different question: **"it is `now`; what is the most recent point-in-time-correct
bar for every symbol as of this instant, and can I build today's feature row
from it without look-ahead?"**

This module is the *as-of read discipline* on top of the existing
`storage/catalog.py` — it does not re-implement storage. The three read
decisions it implements are frozen in the C1-M1 contract
(``docs/concepts/data-freshness-slas.md``):

1. **Processed-only.** ``get_pit_bar`` reads the ``processed`` (deduped,
   schema-validated, PIT-stamped) layer only — never the latest ``raw``
   landing — so the live and backtest paths share *exactly one* source. This
   is the structural prerequisite for the G2 train/serve-parity guarantee.
2. **As-of instant semantics.** ``asof`` is a timezone-aware UTC instant
   compared against the bar ``timestamp``. The reader never returns a bar whose
   ``timestamp > asof``. Because Tiingo stamps 00:00 UTC and Alpaca 04:00 UTC
   for the same session, the instant comparison is what prevents an
   off-by-one-day leak at the UTC date boundary.
3. **Weekend / holiday behaviour.** ``get_pit_bar(symbol, Saturday)`` returns
   Friday's bar — it is simply the most recent bar with ``timestamp <= asof``,
   so non-sessions never error or trip a stale flag.

**Dataset choice is the parity lever.** The reader defaults to
``equity_eod_tiingo`` (the adjusted-EOD dataset the backtest trains on, see
``scripts/run_b1_arms._load_prices_panel``), with the identical
``adjClose -> close`` mapping. Reading the same dataset the backtest reads is
what makes G2 parity *structural* rather than coincidental: feature rows built
from an ``asof``-truncated history equal the batch rows because every feature
in ``build_features`` is backward-looking, so dropping future bars cannot change
a retained row.

C1 makes no Sharpe claim — the gates here are deterministic correctness
predicates, not noisy estimates, so this module records no research-trial
ledger entry (METHODOLOGY §12; C1 PRD "Ledger discipline").
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd

from quant.storage.catalog import processed_glob

# The dataset the backtest path trains on (Tiingo adjusted EOD). Reading the
# SAME dataset the backtest reads is the train/serve-parity lever (G2). The
# ``adjClose`` column is renamed to ``close`` to match the batch loader
# (run_b1_arms._load_prices_panel) bit-for-bit.
PRICE_DATASET: str = "equity_eod_tiingo"

# OHLCV columns selected from the lake, in the order build_features expects.
# ``adjClose`` is mapped to ``close`` so the model trains/serves on the
# split/dividend-adjusted close.
_RAW_PRICE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "adjClose", "volume")
_PRICE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

# ─── Pinned gate thresholds (METHODOLOGY §1/§2; C1 PRD "Success Metrics") ──────
# These are the single source of truth for the C1-M2 gates. Changing any of them
# after a result is visible invalidates the run and requires a PRD revision plus
# a new ledger entry (METHODOLOGY §1). Prose in the PRD describes these; the code
# here is authoritative (METHODOLOGY §2).

# G2 train/serve parity: live features must equal batch features to within this
# relative tolerance, with zero material mismatches.
PARITY_RTOL: float = 1e-9
# G2 must be measured on at least this many (symbol, date) pairs spanning at
# least this many regimes for a PASS to count.
PARITY_MIN_PAIRS: int = 250
PARITY_MIN_REGIMES: int = 2


def _normalize_asof(asof: pd.Timestamp | dt.datetime | str) -> pd.Timestamp:
    """Coerce *asof* to a tz-aware UTC ``Timestamp``.

    A naive instant is interpreted as UTC (the lake's storage convention).
    The reader compares instants, so all inputs are normalised to one zone.
    """
    ts = pd.Timestamp(asof)
    if ts.tz is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _query_pit(
    symbols: Sequence[str],
    asof: pd.Timestamp,
    dataset: str,
    *,
    latest_only: bool,
) -> pd.DataFrame:
    """Run the as-of query against the processed lake.

    The ``timestamp <= ?`` comparison binds *asof* as a TIMESTAMPTZ parameter,
    which DuckDB compares as an instant regardless of session timezone — this is
    the look-ahead guard. (Contrast ``CAST(timestamp AS DATE)``, which DuckDB
    evaluates in the session timezone; we never do that here — see the
    ``_load_fred_wide`` note in features/engineering.py.)
    """
    if not symbols:
        raise ValueError("symbols must not be empty")
    glob = processed_glob(dataset)
    placeholders = ", ".join("?" * len(symbols))
    cols = ", ".join(_RAW_PRICE_COLUMNS)
    # latest_only uses a per-symbol window to take the single most-recent row;
    # otherwise we return the full as-of history for build_features.
    if latest_only:
        sql = f"""
            SELECT symbol, timestamp, {cols} FROM (
                SELECT
                    symbol, timestamp, {cols},
                    row_number() OVER (
                        PARTITION BY symbol ORDER BY timestamp DESC
                    ) AS _rn
                FROM read_parquet('{glob}', hive_partitioning = true)
                WHERE symbol IN ({placeholders}) AND timestamp <= ?
            )
            WHERE _rn = 1
        """
    else:
        sql = f"""
            SELECT symbol, timestamp, {cols}
            FROM read_parquet('{glob}', hive_partitioning = true)
            WHERE symbol IN ({placeholders}) AND timestamp <= ?
            ORDER BY symbol, timestamp
        """
    params = [*symbols, asof.to_pydatetime()]
    con = duckdb.connect()
    try:
        return con.execute(sql, params).df()
    except (duckdb.IOException, duckdb.CatalogException):
        # Dataset has never been written — caller treats as "no bar".
        return pd.DataFrame()
    finally:
        con.close()


def _to_utc_index(timestamps: pd.Series) -> pd.DatetimeIndex:
    """Convert a DuckDB timestamp column to a tz-aware UTC DatetimeIndex.

    DuckDB may hand TIMESTAMPTZ back in the session timezone; normalise to UTC
    so callers get a consistent instant regardless of the host machine.
    """
    return pd.DatetimeIndex(pd.to_datetime(timestamps, utc=True))


def get_pit_bar(
    symbol: str,
    asof: pd.Timestamp | dt.datetime | str,
    *,
    dataset: str = PRICE_DATASET,
) -> pd.Series | None:
    """Most recent point-in-time-correct bar for *symbol* as of *asof*.

    Returns a ``Series`` of OHLCV values whose ``.name`` is the bar's tz-aware
    UTC timestamp, or ``None`` if no bar exists at/before *asof* (e.g. *asof*
    predates the symbol's history, or the symbol is absent from the lake).

    **Look-ahead guard (G1):** the returned bar's timestamp is always
    ``<= asof``. A weekend/holiday *asof* returns the prior session's bar; it
    never errors and never returns a future bar.

    ``close`` is the split/dividend-adjusted close (``adjClose`` in the lake),
    matching the batch backtest path.
    """
    asof_ts = _normalize_asof(asof)
    df = _query_pit([symbol], asof_ts, dataset, latest_only=True)
    if df.empty:
        return None
    row = df.iloc[0]
    bar_ts = pd.Timestamp(pd.to_datetime(row["timestamp"], utc=True))
    values = (
        row.rename({"adjClose": "close"})[list(_PRICE_COLUMNS)]
        .to_numpy(dtype=float)
    )
    return pd.Series(values, index=list(_PRICE_COLUMNS), name=bar_ts)


def get_pit_panel(
    symbols: Sequence[str],
    asof: pd.Timestamp | dt.datetime | str,
    *,
    dataset: str = PRICE_DATASET,
) -> dict[str, pd.DataFrame]:
    """Assemble ``{symbol: OHLCV history with timestamp <= asof}``.

    The returned per-symbol frames are exactly what ``build_features`` consumes:
    a ``DatetimeIndex`` (tz-aware UTC) of bar timestamps and the columns
    ``open, high, low, close, volume`` (``adjClose`` mapped to ``close``),
    sorted ascending with NaN rows dropped — the same shape as the batch
    loader. Symbols absent from the lake at *asof* are omitted (a caller that
    needs every symbol present should check the returned keys).

    Pipeline ordering for a live run is *ingest → to_processed → get_pit_panel →
    build_features(asof) → predict* (C1-M1 contract): the processed layer must
    be current before this reader is queried.
    """
    asof_ts = _normalize_asof(asof)
    df = _query_pit(symbols, asof_ts, dataset, latest_only=False)
    panel: dict[str, pd.DataFrame] = {}
    if df.empty:
        return panel
    df = df.rename(columns={"adjClose": "close"})
    df.index = _to_utc_index(df["timestamp"])
    for sym in symbols:
        sub = df[df["symbol"] == sym][list(_PRICE_COLUMNS)].copy()
        if sub.empty:
            continue
        panel[sym] = sub.sort_index().dropna()
    return panel


# ─── Gate functions (G1 PIT, G2 parity) ───────────────────────────────────────


@dataclass(frozen=True)
class PitGateResult:
    """Verdict of the G1 no-look-ahead gate."""

    n_checked: int
    n_future_bars: int
    passed: bool


def pit_gate_report(
    checks: Sequence[tuple[pd.Timestamp, pd.Timestamp | None]],
) -> PitGateResult:
    """G1: every returned bar timestamp must be ``<= asof``.

    *checks* is a sequence of ``(asof, returned_bar_timestamp_or_None)`` pairs
    from a property-based sweep of as-of instants × symbols. A ``None`` return
    (no bar at/before *asof*) is **not** a violation. The future-bar count must
    be exactly 0 to pass (C1 PRD G1).
    """
    n = len(checks)
    violations = 0
    for asof, bar_ts in checks:
        if bar_ts is None:
            continue
        if _normalize_asof(bar_ts) > _normalize_asof(asof):
            violations += 1
    return PitGateResult(n_checked=n, n_future_bars=violations, passed=violations == 0)


@dataclass(frozen=True)
class ParityGateResult:
    """Verdict of the G2 train/serve-parity gate."""

    n_pairs: int
    n_regimes: int
    n_columns: int
    n_mismatches: int
    max_abs_diff: float
    max_rel_diff: float
    passed: bool


def parity_gate_report(
    batch: pd.DataFrame,
    live: pd.DataFrame,
    *,
    n_regimes: int,
    rtol: float = PARITY_RTOL,
    min_pairs: int = PARITY_MIN_PAIRS,
    min_regimes: int = PARITY_MIN_REGIMES,
) -> ParityGateResult:
    """G2: live features must equal batch features within *rtol*, 0 mismatches.

    *batch* and *live* are feature frames indexed by the sampled
    ``(symbol, date)`` pairs (any shared index) with feature columns. The
    comparison runs on the **intersection** of their indices and columns
    (warmup columns a caller drops on one side are excluded). NaN compares equal
    to NaN (warmup rows), so only material divergences count.

    A PASS requires **all** of: zero material mismatches, at least *min_pairs*
    compared rows, and at least *min_regimes* regimes spanned (*n_regimes* is
    supplied by the caller, which knows the date spans). The thresholds default
    to the pinned constants (C1 PRD G2: ``rtol ≤ 1e-9``, ≥250 pairs, ≥2
    regimes).
    """
    shared_cols = [c for c in batch.columns if c in set(live.columns)]
    shared_idx = batch.index.intersection(live.index)
    b = batch.loc[shared_idx, shared_cols]
    lv = live.loc[shared_idx, shared_cols]

    b_arr = b.to_numpy(dtype=float)
    l_arr = lv.to_numpy(dtype=float)

    close = np.isclose(b_arr, l_arr, rtol=rtol, atol=0.0, equal_nan=True)
    n_mismatches = int((~close).sum())

    # Diagnostics over cells where both sides are finite (NaN warmup excluded).
    both_finite = np.isfinite(b_arr) & np.isfinite(l_arr)
    if both_finite.any():
        abs_diff = np.abs(b_arr[both_finite] - l_arr[both_finite])
        denom = np.abs(b_arr[both_finite])
        rel_diff = np.divide(
            abs_diff, denom, out=abs_diff.copy(), where=denom > 0
        )
        max_abs = float(abs_diff.max())
        max_rel = float(rel_diff.max())
    else:
        max_abs = 0.0
        max_rel = 0.0

    n_pairs = len(shared_idx)
    passed = n_mismatches == 0 and n_pairs >= min_pairs and n_regimes >= min_regimes
    return ParityGateResult(
        n_pairs=n_pairs,
        n_regimes=n_regimes,
        n_columns=len(shared_cols),
        n_mismatches=n_mismatches,
        max_abs_diff=max_abs,
        max_rel_diff=max_rel,
        passed=passed,
    )
