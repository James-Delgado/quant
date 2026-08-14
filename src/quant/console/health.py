"""Live feed health vs the pinned C1 SLA + lake gap detection (E4-M1).

Two E4 concerns live here, both consumed by :func:`quant.console.readers.data_status`
through injectable ``ConsoleSources`` seams:

* **Per-ingestor SLA health** — delegated to the C1-M3 machinery
  (``SOURCE_SLAS`` / ``evaluate_feed`` / ``monitor`` in
  ``scripts/monitor_freshness.py``), so the console panel, the API's
  ``GET /health``, and the cron monitor can never disagree about what "fresh"
  means (one SLA source of truth, METHODOLOGY §6). E4 *consumes* C1's SLA
  definitions; it does not redefine them (E4 PRD §6).
* **Lake gap detection** — a trading day strictly inside a price dataset's
  observed span with no bar means an ingest silently failed. The gap decision
  is a pure function (:func:`find_gap_dates`) over the observed date set; the
  lake-reading adapter (:func:`observed_trading_dates`) is a thin DuckDB
  wrapper, mirroring the C1-M3 pure-core / thin-adapter split so the seeded-gap
  acceptance test needs no real lake.

Layering note: :func:`load_monitor_module` (the importlib-by-file-path loader
for the non-package ``scripts/`` tree) moved here from
``quant.console.api.app`` so the API and the reader share ONE loader. The
inversion itself — an installed package importing a repo script — still stands
and remains filed as ``MISC-SLA-CORE-SRC``; this module is where the direct
import lands when that promotion happens.
"""
from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd

# ── Pinned gap-detection constants (METHODOLOGY §1) ──────────────────────────
# Max gap dates surfaced per dataset in the view. The COUNT is always exact;
# only the listed dates are capped (most recent first) so a badly-holed lake
# cannot balloon the export payload.
MAX_GAP_DATES_SURFACED = 10


# ── C1-M3 monitor loader (moved from api/app.py; one loader, two consumers) ──


def load_monitor_module() -> Any:
    """Load ``scripts/monitor_freshness.py`` as a module (scripts is not a package).

    The same importlib-by-file-path pattern ``scripts/trade_daily.py`` and the
    monitor's own tests use. The path is resolved relative to the installed
    ``quant`` package (src layout → repo root), so it works wherever the
    editable install runs from. A previously loaded instance (e.g. by the test
    suite or ``trade_daily``) is reused via ``sys.modules`` rather than
    re-executed. Raises ``RuntimeError`` when the script cannot be found/loaded
    — callers turn that into an honest degrade (a 503 on ``/health``, an
    omitted-SLA note in the Data panel), never fabricated freshness
    (METHODOLOGY §9).

    Layering note: a package importing a repo script is inverted; promoting the
    pure SLA core into ``src/quant`` is filed as follow-up MISC-SLA-CORE-SRC.
    """
    cached = sys.modules.get("monitor_freshness")
    if cached is not None:
        return cached
    import importlib.util

    import quant

    path = Path(quant.__file__).resolve().parents[2] / "scripts" / "monitor_freshness.py"
    spec = importlib.util.spec_from_file_location("monitor_freshness", path)
    if spec is None or spec.loader is None or not path.exists():
        raise RuntimeError(f"freshness monitor script not found at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["monitor_freshness"] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop("monitor_freshness", None)
        raise
    return module


def default_sla_statuses(now: dt.datetime) -> list[Any]:
    """Per-feed SLA verdicts from the real lake at instant *now*.

    The production ``ConsoleSources.sla_statuses_fn``: runs the C1-M3
    ``monitor`` (``evaluate_feed`` over the pinned ``SOURCE_SLAS``) against the
    processed lake. Failures propagate — the reader degrades honestly rather
    than this wrapper inventing a verdict.
    """
    return load_monitor_module().monitor(now=now)


# ── Lake gap detection ───────────────────────────────────────────────────────


def find_gap_dates(observed: Iterable[dt.date]) -> list[dt.date]:
    """NYSE sessions strictly inside the observed span with no observation.

    Pure predicate over the observed date set — no lake access. The expected
    session set is ``utils.calendar.trading_days(min(observed), max(observed))``,
    so weekends and exchange holidays are never gaps. Days *before* the first
    observation are not gaps (the dataset simply starts later) and days *after*
    the last are the freshness SLA's concern, not gap detection's — hence
    "strictly inside the span". Fewer than two distinct dates ⇒ no span ⇒ no
    gaps. Returned sorted ascending.
    """
    from quant.utils.calendar import trading_days

    dates = set(observed)
    if len(dates) < 2:
        return []
    expected = trading_days(min(dates), max(dates))
    return [d for d in expected if d not in dates]


def observed_trading_dates(dataset: str, ts_col: str = "timestamp") -> list[dt.date] | None:
    """Distinct observation dates for a lake dataset, or ``None`` if unreadable.

    The lake-reading adapter feeding :func:`find_gap_dates`. Dates are extracted
    timezone-independently — ``to_datetime(..., utc=True)`` then ``normalize()``
    — never a SQL ``CAST(... AS DATE)``, which DuckDB evaluates in the *session*
    timezone and would shift observations a calendar day on a US-timezone
    machine (the documented lake tz-alignment pitfall; mirrors
    ``sources._fred_series``). Any failure — never-written dataset, unresolved
    settings, malformed parquet — degrades to ``None`` so the reader reports
    "could not check" rather than a fabricated "no gaps" (METHODOLOGY §9).
    """
    import duckdb

    from quant.storage.catalog import processed_glob

    try:
        glob = processed_glob(dataset)
        sql = (
            f"SELECT DISTINCT {ts_col} AS ts "
            f"FROM read_parquet('{glob}', hive_partitioning = true)"
        )
        con = duckdb.connect()
        try:
            df = con.execute(sql).df()
        finally:
            con.close()
    except Exception:
        return None
    if df.empty:
        return None
    ts = pd.to_datetime(df["ts"], utc=True).dt.normalize().dt.tz_localize(None)
    return sorted({t.date() for t in ts})
