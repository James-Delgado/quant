"""Same-day data freshness monitor (C1-M3).

The batch lake path is correct for *backtesting* — it reads a clean,
deduplicated, point-in-time-stamped historical lake at leisure. But a *live*
system that silently trades on yesterday's data because a feed was late is
worse than one that halts and alerts. This script is that alert: it reads the
latest stored observation per feed, compares it against the **pinned per-source
freshness SLA** frozen in the C1-M1 contract
(``docs/concepts/data-freshness-slas.md``), and emits a per-feed status
(``fresh | stale | missing``) with a **non-zero exit on any non-fresh feed** so
``cron`` / CI surfaces it (C1 PRD G3; delivery channel = exit-code + stderr per
the PRD Open-Question — a richer email/Slack channel is a C5 concern).

Design — the SLA-evaluation core is *pure*
------------------------------------------
``evaluate_feed(spec, latest, now)`` is a deterministic predicate over
``(latest_observation, now)``; it touches no lake. That is the heart the
**G3 gate** (``freshness_gate_report``) exercises on synthetic fresh/stale
fixtures: ``(false_stale_count, missed_stale_count) == (0, 0)``. The lake-reading
wrappers (``read_latest*``) are thin adapters that feed the core the real
``catalog.latest_timestamp`` per dataset.

Pinned thresholds (METHODOLOGY §1/§2; drift-contracted to C1-M1, §6)
--------------------------------------------------------------------
The SLA *values* live in one place — ``SOURCE_SLAS`` below — consumed by both
the monitor and its tests, so prose and code cannot diverge. FRED reuses the
already-pinned ``engineering.FRED_PUBLICATION_LAGS`` (the macro train/serve
parity lever, M5); no new lag values are introduced here. Changing any SLA value
is a PRD revision + a new ledger entry, visible in ``git diff`` — never an
in-flight override (C1-M1 "Update protocol").

Declared approximations (METHODOLOGY §9)
----------------------------------------
* **Business days = NYSE trading days.** The FRED publication-lag arithmetic and
  the EDGAR scan-liveness window count *trading* days via ``utils/calendar.py``
  (FRED uses ``numpy`` business days, weekday-only). FRED technically releases on
  the U.S. *federal* calendar, which differs from NYSE/weekday on a handful of
  days a year (e.g. Columbus/Veterans Day). The one extra business-day grace on
  the FRED window (``FRED_GRACE_BDAYS = 1``) absorbs this and the "occasional
  2-business-day first-release lag" the C1-M1 audit notes, so the approximation
  cannot cause a false-stale alert — only, at worst, a one-session-late
  detection, which the conservative SLA already tolerates.
* **Availability times are the C1-M1 desk-research estimates**, not live-measured
  (the C1-M1 declared deviation; ``C1-M1-MEASURE`` is the follow-up). They are
  deliberately conservative so the monitor does not cry wolf.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

import duckdb
import numpy as np
import pandas as pd

from quant.features.engineering import FRED_PUBLICATION_LAGS, _FRED_SERIES
from quant.storage.catalog import latest_timestamp, processed_glob
from quant.utils.calendar import last_trading_day, trading_days

# ─── Status enum ───────────────────────────────────────────────────────────────


class FeedState(str, Enum):
    """Per-feed freshness verdict. ``stale`` and ``missing`` both alert."""

    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"


# ─── Freshness predicate kinds ─────────────────────────────────────────────────


class FreshnessKind(str, Enum):
    """How a source's staleness is evaluated (see ``evaluate_feed``)."""

    # Equity bars: today's (T) bar must be available by a wall-clock deadline.
    PRICE_DEADLINE = "price_deadline"
    # FRED: observation dated D is published D + lag business days later.
    FRED_RELEASE = "fred_release"
    # Event-driven feeds: alert if the newest item is older than a liveness window.
    LIVENESS_TRADING = "liveness_trading"
    LIVENESS_CALENDAR = "liveness_calendar"


# ─── Pinned SLA constants (single source of truth — drift-contracted to C1-M1) ──
# Alpaca IEX daily bar: today's (T) settled bar available by 23:00 UTC on T.
ALPACA_DEADLINE_HOUR_UTC: int = 23
ALPACA_DEADLINE_DAY_OFFSET: int = 0
# Tiingo adjusted EOD: today's (T) adjusted bar available by 12:00 UTC on T+1.
TIINGO_DEADLINE_HOUR_UTC: int = 12
TIINGO_DEADLINE_DAY_OFFSET: int = 1
# FRED: one extra business-day grace on top of the per-series publication lag
# (absorbs the market-day-only publishing + the holiday-shifted release the
# C1-M1 audit flags). See the "Declared approximations" docstring note.
FRED_GRACE_BDAYS: int = 1
# EDGAR 8-K: scan-liveness — alert if no new filing in > this many trading days.
EDGAR_MAX_STALE_TRADING_DAYS: int = 1
# RSS: item-liveness — alert if no new item in > this many calendar days.
RSS_MAX_STALE_CALENDAR_DAYS: int = 1


@dataclass(frozen=True)
class SourceSLA:
    """One monitored feed: which dataset it reads and how staleness is judged.

    *deadline_hour_utc* / *deadline_day_offset* apply to ``PRICE_DEADLINE``;
    *max_stale_days* applies to the liveness kinds; *fred_series* names the FRED
    series for ``FRED_RELEASE`` (its lag is read from ``FRED_PUBLICATION_LAGS``).
    *doc_source* filters the shared ``text_documents`` dataset (EDGAR vs RSS).
    """

    name: str
    dataset: str
    kind: FreshnessKind
    deadline_hour_utc: int | None = None
    deadline_day_offset: int | None = None
    max_stale_days: int | None = None
    fred_series: str | None = None
    doc_source: str | None = None  # exact ("edgar") or prefix ("rss") match


def _fred_slas() -> tuple[SourceSLA, ...]:
    """One SLA per *model-relevant* FRED series (the three in ``_FRED_SERIES``)."""
    return tuple(
        SourceSLA(
            name=f"fred:{series}",
            dataset="macro_fred",
            kind=FreshnessKind.FRED_RELEASE,
            fred_series=series,
        )
        for series in _FRED_SERIES
    )


# The full monitored feed set: 2 equity + 3 FRED series + EDGAR + RSS = 7 feeds.
# This is the C1-M1 "Pinned per-source freshness SLA" table, in code.
SOURCE_SLAS: tuple[SourceSLA, ...] = (
    SourceSLA(
        name="alpaca",
        dataset="equity_bars_daily",
        kind=FreshnessKind.PRICE_DEADLINE,
        deadline_hour_utc=ALPACA_DEADLINE_HOUR_UTC,
        deadline_day_offset=ALPACA_DEADLINE_DAY_OFFSET,
    ),
    SourceSLA(
        name="tiingo",
        dataset="equity_eod_tiingo",
        kind=FreshnessKind.PRICE_DEADLINE,
        deadline_hour_utc=TIINGO_DEADLINE_HOUR_UTC,
        deadline_day_offset=TIINGO_DEADLINE_DAY_OFFSET,
    ),
    *_fred_slas(),
    SourceSLA(
        name="edgar",
        dataset="text_documents",
        kind=FreshnessKind.LIVENESS_TRADING,
        max_stale_days=EDGAR_MAX_STALE_TRADING_DAYS,
        doc_source="edgar",
    ),
    SourceSLA(
        name="rss",
        dataset="text_documents",
        kind=FreshnessKind.LIVENESS_CALENDAR,
        max_stale_days=RSS_MAX_STALE_CALENDAR_DAYS,
        doc_source="rss",
    ),
)


# ─── Result type ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FeedStatus:
    """The verdict for one feed at one evaluation instant."""

    name: str
    state: FeedState
    latest: pd.Timestamp | None
    # The oldest observation date that would still count as fresh; *latest* must
    # be on/after this. ``None`` when no observation exists (state == MISSING).
    required_date: dt.date | None
    detail: str

    @property
    def is_alert(self) -> bool:
        """True when the feed needs operator attention (stale or missing)."""
        return self.state is not FeedState.FRESH


# ─── Date helpers ──────────────────────────────────────────────────────────────


def _normalize_now(now: pd.Timestamp | dt.datetime | str | None) -> pd.Timestamp:
    """Coerce *now* to a tz-aware UTC ``Timestamp`` (default: current time)."""
    ts = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    if ts.tz is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _to_date(ts: pd.Timestamp | dt.datetime | None) -> dt.date | None:
    """The UTC calendar date of an observation timestamp, or None."""
    if ts is None:
        return None
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")
    return t.date()


def _nth_trading_day_before(reference: dt.date, n: int) -> dt.date:
    """The session *n* trading days before the last session on/≤ *reference*.

    ``n == 0`` returns ``last_trading_day(reference)`` itself. Used for the
    price-deadline backstep and the EDGAR scan-liveness window.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    # A generous lookback window: ~2 calendar weeks plus slack covers n sessions.
    window = trading_days(reference - dt.timedelta(days=10 + 2 * n), reference)
    if not window:
        raise ValueError(f"no trading days on or before {reference}")
    idx = len(window) - 1 - n
    if idx < 0:
        raise ValueError(f"fewer than {n} trading days before {reference}")
    return window[idx]


def _price_required_date(spec: SourceSLA, now: pd.Timestamp) -> dt.date:
    """Most recent trading day whose availability deadline has passed by *now*.

    For a bar dated ``T``, the deadline is ``T + deadline_day_offset`` calendar
    days at ``deadline_hour_utc`` UTC. We walk back from the last session until
    its deadline is ``<= now``; that session's bar is the one we must already
    have. (If no recent session's deadline has passed yet — e.g. early on a
    trading morning — we keep stepping back, so the monitor never demands a bar
    that is not yet due.)
    """
    assert spec.deadline_hour_utc is not None and spec.deadline_day_offset is not None
    session = last_trading_day(now.date())
    for _ in range(12):  # bounded backstep; 12 sessions ≈ 2.5 weeks
        deadline = pd.Timestamp(
            session + dt.timedelta(days=spec.deadline_day_offset),
            tz="UTC",
        ) + pd.Timedelta(hours=spec.deadline_hour_utc)
        if deadline <= now:
            return session
        session = _nth_trading_day_before(session - dt.timedelta(days=1), 0)
    # Fallback: the deepest session we stepped to (extremely stale lake / clock).
    return session


def _fred_required_date(spec: SourceSLA, now: pd.Timestamp) -> dt.date:
    """Oldest acceptable latest-observation date for a FRED series.

    Observation ``D`` is published ``lag`` business days later, so as of *now*
    the newest observation we can expect is dated ``lag`` business days back; we
    add ``FRED_GRACE_BDAYS`` of slack (holiday-shifted releases, market-day-only
    series). A series whose latest stored observation is older than this is stale.
    """
    assert spec.fred_series is not None
    lag = FRED_PUBLICATION_LAGS[spec.fred_series]
    offset = lag + FRED_GRACE_BDAYS
    required = np.busday_offset(now.date(), -offset, roll="backward")
    return pd.Timestamp(required).date()


def _liveness_required_date(spec: SourceSLA, now: pd.Timestamp) -> dt.date:
    """Oldest acceptable latest-item date for an event-driven (liveness) feed."""
    assert spec.max_stale_days is not None
    if spec.kind is FreshnessKind.LIVENESS_TRADING:
        return _nth_trading_day_before(now.date(), spec.max_stale_days)
    # Calendar liveness (RSS): simple calendar-day window.
    return now.date() - dt.timedelta(days=spec.max_stale_days)


def _required_date(spec: SourceSLA, now: pd.Timestamp) -> dt.date:
    """Dispatch to the per-kind required-date computation."""
    if spec.kind is FreshnessKind.PRICE_DEADLINE:
        return _price_required_date(spec, now)
    if spec.kind is FreshnessKind.FRED_RELEASE:
        return _fred_required_date(spec, now)
    return _liveness_required_date(spec, now)


# ─── The pure SLA-evaluation core (no lake access) ─────────────────────────────


def evaluate_feed(
    spec: SourceSLA,
    latest: pd.Timestamp | dt.datetime | None,
    now: pd.Timestamp | dt.datetime | str | None = None,
) -> FeedStatus:
    """Classify one feed as ``fresh | stale | missing`` at instant *now*.

    *latest* is the most recent observation timestamp for the feed (the lake's
    ``catalog.latest_timestamp`` for the dataset, or ``None`` if the dataset has
    never been written). The verdict is the deterministic predicate G3 tests:
    the feed is **fresh** iff its latest observation date is on/after the SLA's
    *required date*, **missing** iff there is no observation, **stale** otherwise.
    """
    now_ts = _normalize_now(now)
    required = _required_date(spec, now_ts)
    latest_date = _to_date(latest)

    if latest_date is None:
        return FeedStatus(
            name=spec.name,
            state=FeedState.MISSING,
            latest=None,
            required_date=required,
            detail=f"no observation in lake (required ≥ {required})",
        )
    if latest_date >= required:
        return FeedStatus(
            name=spec.name,
            state=FeedState.FRESH,
            latest=pd.Timestamp(latest),
            required_date=required,
            detail=f"latest {latest_date} ≥ required {required}",
        )
    return FeedStatus(
        name=spec.name,
        state=FeedState.STALE,
        latest=pd.Timestamp(latest),
        required_date=required,
        detail=f"latest {latest_date} < required {required}",
    )


# ─── G3 gate function (METHODOLOGY §2) ─────────────────────────────────────────


@dataclass(frozen=True)
class FreshnessGateResult:
    """Verdict of the C1 G3 freshness-monitor gate."""

    n_fresh_cases: int
    n_stale_cases: int
    false_stale_count: int  # known-fresh fixtures the monitor wrongly flagged
    missed_stale_count: int  # known-stale fixtures the monitor wrongly passed
    passed: bool


def freshness_gate_report(
    fresh_cases: Sequence[tuple[SourceSLA, pd.Timestamp | dt.datetime | None, object]],
    stale_cases: Sequence[tuple[SourceSLA, pd.Timestamp | dt.datetime | None, object]],
) -> FreshnessGateResult:
    """G3: the monitor flags **exactly** the stale feeds — ``(0, 0)`` to pass.

    *fresh_cases* and *stale_cases* are sequences of ``(spec, latest, now)``
    tuples derived from the pinned SLA table — fixtures known to be within and
    beyond SLA respectively. ``false_stale_count`` counts fresh fixtures the
    monitor reported non-fresh; ``missed_stale_count`` counts stale fixtures it
    reported fresh. The gate passes iff both are zero (C1 PRD G3).
    """
    false_stale = sum(
        1
        for spec, latest, now in fresh_cases
        if evaluate_feed(spec, latest, now).state is not FeedState.FRESH
    )
    missed_stale = sum(
        1
        for spec, latest, now in stale_cases
        if evaluate_feed(spec, latest, now).state is FeedState.FRESH
    )
    return FreshnessGateResult(
        n_fresh_cases=len(fresh_cases),
        n_stale_cases=len(stale_cases),
        false_stale_count=false_stale,
        missed_stale_count=missed_stale,
        passed=false_stale == 0 and missed_stale == 0,
    )


# ─── Lake-reading adapters (thin; feed the pure core real data) ────────────────


def _latest_doc_timestamp(source_prefix: str) -> pd.Timestamp | None:
    """Max ``published_at`` in ``text_documents`` for a given ``source`` prefix.

    EDGAR and RSS share the ``text_documents`` dataset, distinguished by the
    ``source`` column (``"edgar"`` vs ``"rss_*"``). ``catalog.latest_timestamp``
    cannot filter, so this runs the per-source max directly (same exception
    handling: a never-written dataset reads as ``None``).
    """
    glob = processed_glob("text_documents")
    sql = (
        "SELECT max(published_at) AS m "
        f"FROM read_parquet('{glob}', hive_partitioning = true) "
        "WHERE source LIKE ?"
    )
    con = duckdb.connect()
    try:
        result = con.execute(sql, [f"{source_prefix}%"]).df()
    except (duckdb.IOException, duckdb.CatalogException):
        return None
    finally:
        con.close()
    if result.empty or pd.isna(result.iloc[0]["m"]):
        return None
    ts = pd.Timestamp(result.iloc[0]["m"])
    return ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")


def _latest_fred_timestamp(series: str) -> pd.Timestamp | None:
    """Max ``timestamp`` for one FRED ``series_id`` in ``macro_fred``."""
    glob = processed_glob("macro_fred")
    sql = (
        "SELECT max(timestamp) AS m "
        f"FROM read_parquet('{glob}', hive_partitioning = true) "
        "WHERE series_id = ?"
    )
    con = duckdb.connect()
    try:
        result = con.execute(sql, [series]).df()
    except (duckdb.IOException, duckdb.CatalogException):
        return None
    finally:
        con.close()
    if result.empty or pd.isna(result.iloc[0]["m"]):
        return None
    ts = pd.Timestamp(result.iloc[0]["m"])
    return ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")


def read_latest(spec: SourceSLA) -> pd.Timestamp | None:
    """The most recent observation timestamp for *spec* from the processed lake."""
    if spec.kind is FreshnessKind.FRED_RELEASE:
        return _latest_fred_timestamp(spec.fred_series)  # type: ignore[arg-type]
    if spec.dataset == "text_documents":
        return _latest_doc_timestamp(spec.doc_source)  # type: ignore[arg-type]
    ts = latest_timestamp(spec.dataset)
    return pd.Timestamp(ts) if ts is not None else None


def monitor(
    now: pd.Timestamp | dt.datetime | str | None = None,
    specs: Sequence[SourceSLA] = SOURCE_SLAS,
) -> list[FeedStatus]:
    """Evaluate every monitored feed against its SLA at instant *now*.

    Reads the real processed lake (``read_latest``) and runs the pure
    ``evaluate_feed`` core per feed. Returns the per-feed statuses in SLA order.
    """
    now_ts = _normalize_now(now)
    return [evaluate_feed(spec, read_latest(spec), now_ts) for spec in specs]


# ─── CLI ───────────────────────────────────────────────────────────────────────

_STATE_GLYPH = {
    FeedState.FRESH: "OK",
    FeedState.STALE: "STALE",
    FeedState.MISSING: "MISS",
}


def format_report(statuses: Sequence[FeedStatus], now: pd.Timestamp) -> str:
    """Render the per-feed status table for stdout/stderr."""
    width = max((len(s.name) for s in statuses), default=4)
    lines = [f"Freshness monitor @ {now.isoformat()}"]
    for s in statuses:
        latest = s.latest.date().isoformat() if s.latest is not None else "—"
        lines.append(
            f"  [{_STATE_GLYPH[s.state]:>5}] {s.name:<{width}}  "
            f"latest={latest:<12} {s.detail}"
        )
    n_alert = sum(1 for s in statuses if s.is_alert)
    verdict = "ALL FRESH" if n_alert == 0 else f"{n_alert} FEED(S) NEED ATTENTION"
    lines.append(f"  → {verdict}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry: print the status table; exit non-zero on any non-fresh feed."""
    parser = argparse.ArgumentParser(
        description="Monitor per-source data freshness against the pinned C1 SLA table.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Evaluate as of this UTC instant (ISO-8601); defaults to current time. "
        "Useful for testing and replaying a past day.",
    )
    args = parser.parse_args(argv)

    now_ts = _normalize_now(args.now)
    statuses = monitor(now=now_ts)
    report = format_report(statuses, now_ts)

    alerts = [s for s in statuses if s.is_alert]
    # Fresh report to stdout; if anything is stale/missing, also echo to stderr
    # so cron's default mail-on-stderr surfaces it.
    print(report)
    if alerts:
        print(report, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
