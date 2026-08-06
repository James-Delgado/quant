"""Live per-source data-availability measurement (C1-M1-MEASURE).

The C1-M1 freshness SLA table (``docs/concepts/data-freshness-slas.md``) was
pinned from desk research, not live observation — a declared deviation
(METHODOLOGY §9). This script closes the measurement side of that deviation:
run on a frequent schedule (cron, every 30 minutes), it probes each
**publisher API directly** (not the lake — the lake only advances when the
batch flow runs) and appends one row per feed to an append-only CSV log. After
enough sessions accrue, ``--report`` reduces the log to per-session
first-availability times and SLA margins, from which the follow-up verdict
task (``C1-M1-MEASURE-VERDICT``) confirms — or proposes tightening — the
pinned SLA values via the C1-M1 update protocol.

Design — the SLA predicate is REUSED, never re-stated
-----------------------------------------------------
Every SLA value comes from ``monitor_freshness.SOURCE_SLAS`` and staleness is
judged by ``monitor_freshness.evaluate_feed`` — the same drift-contracted
constants and predicate the C1-M3 monitor enforces (METHODOLOGY §6). A poll
where the *publisher itself* evaluates STALE is direct evidence the pinned SLA
is not achievable; zero such polls across the accrual window confirms it. The
probe set is built *from* ``SOURCE_SLAS`` (``build_probes``), so a feed added
to the monitor without a probe fails loudly rather than silently going
unmeasured.

Pre-committed verdict rule (METHODOLOGY §1 — pinned before any data accrued)
----------------------------------------------------------------------------
For the two deadline feeds (alpaca, tiingo), per session ``T``:

* **on_time** — the session's data was visible at a poll at/before its SLA
  deadline (exact: presence observed in-window). Only polls at/after the
  session's *settle floor* (``PRICE_SETTLE_FLOOR_HOUR_UTC``) attest: a daily
  bar fetched mid-session is a partial bar, not the settled bar the SLA
  promises.
* **miss**    — a poll at/after the deadline still showed the data absent
  (exact: absence observed past-deadline proves the deadline was missed).
* **uncovered** — neither (polls straddled the deadline; no inference is made
  across poll gaps — machine-asleep windows are censoring, not evidence).

A verdict needs **≥ MIN_SESSIONS_FOR_SLA_VERDICT informative sessions** per
feed. A tightening proposal may move a deadline no earlier than the
worst-case observed first-seen time **+ TIGHTEN_BUFFER_HOURS**, rounded up to
the hour — and is only ever a *proposal*: any SLA change goes through the
C1-M1 update protocol (PRD revision + ledger entry), never an in-flight edit.

FRED sessions are day-granular (observed publication lag in business days vs
the pinned ``lag + grace``). EDGAR/RSS have liveness SLAs with no "available
by" time to confirm or tighten; their rows are recorded for quiet-gap context
only — and the EDGAR probe covers a 3-filer subset, not the universe, so its
liveness stats are informational, never verdict input.

Operational notes
-----------------
* Probes are deliberately plain functions (no Prefect) — the cron entry must
  stay light and dependency-free.
* Probe *errors* (network, credentials) are logged with ``status=error`` and
  excluded from all inference: a failed probe is not evidence of absence.
* The log (``data/freshness/availability_log.csv``) is append-only and
  gitignored like other run checkpoints; it is one-shot live evidence, so do
  not delete it before the verdict task has consumed it.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from quant.config import settings
from quant.features.engineering import FRED_PUBLICATION_LAGS
from quant.ingest.rss import _parse_pubdate
from quant.utils.calendar import trading_days

# ``scripts/`` is not a package (setuptools is scoped to src/); the monitor is
# imported via a path insert so its drift-contracted SLA constants stay the
# single source of truth here too.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
import monitor_freshness as mf  # noqa: E402

# ─── Pinned probe + verdict constants (METHODOLOGY §1) ─────────────────────────

# Where the append-only measurement log accrues.
LOG_PATH_DEFAULT: Path = settings.data_root / "freshness" / "availability_log.csv"
LOG_COLUMNS: tuple[str, ...] = (
    "measured_at",
    "source",
    "latest_available",
    "status",
    "detail",
)

# One liquid probe symbol answers "has the publisher released today's data?".
# Per-symbol completeness is an ingestion concern, not an availability one.
PROBE_EQUITY_SYMBOL: str = "SPY"
PROBE_EQUITY_LOOKBACK_DAYS: int = 10
PROBE_FRED_LOOKBACK_DAYS: int = 45
# EDGAR liveness probe: three of the most active universe filers (informational
# only — a 3-filer subset cannot stand in for the whole-universe liveness the
# monitor evaluates against the lake).
PROBE_EDGAR_CIKS: dict[str, str] = {
    "AAPL": "320193",
    "MSFT": "789019",
    "NVDA": "1045810",
}
PROBE_EDGAR_FORMS: frozenset[str] = frozenset({"8-K", "10-K", "10-Q"})

# Verdict rule constants — pinned BEFORE any measurement accrued.
MIN_SESSIONS_FOR_SLA_VERDICT: int = 10  # ≈ 2 trading weeks
TIGHTEN_BUFFER_HOURS: int = 2
# Settle floor: querying a daily-bar API *during* session T returns a PARTIAL
# bar for T (observed live: Alpaca serves today's bar mid-session), so a poll
# before T's market close cannot attest that T's *settled* bar is available.
# 21:00 UTC is the 16:00 ET close in EST — the conservative bound across DST.
# Corollary: the alpaca tightening floor is 21:00 + buffer = its current 23:00
# SLA, so alpaca can only be confirmed, never tightened, by this instrument.
PRICE_SETTLE_FLOOR_HOUR_UTC: int = 21

_SPEC_BY_NAME: dict[str, mf.SourceSLA] = {s.name: s for s in mf.SOURCE_SLAS}
_DEADLINE_FEEDS: tuple[str, ...] = ("alpaca", "tiingo")


# ─── Probe record ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProbeRecord:
    """One feed's publisher-side observation at one poll instant."""

    measured_at: pd.Timestamp
    source: str
    latest_available: pd.Timestamp | None
    status: str  # "ok" | "error"
    detail: str


def _to_utc(ts: pd.Timestamp) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")


# ─── Publisher probes (thin, network-touching) ─────────────────────────────────


def probe_alpaca() -> pd.Timestamp | None:
    """Latest daily-bar timestamp Alpaca's IEX feed serves for the probe symbol."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_secret_key)
    request = StockBarsRequest(
        symbol_or_symbols=[PROBE_EQUITY_SYMBOL],
        timeframe=TimeFrame.Day,
        start=dt.datetime.now(dt.timezone.utc)
        - dt.timedelta(days=PROBE_EQUITY_LOOKBACK_DAYS),
        feed=DataFeed.IEX,
    )
    barset = client.get_stock_bars(request)
    if not barset.data:
        return None
    return _to_utc(barset.df.reset_index()["timestamp"].max())


def probe_tiingo() -> pd.Timestamp | None:
    """Latest adjusted-EOD date Tiingo serves for the probe symbol."""
    from tiingo import TiingoClient

    client = TiingoClient({"api_key": settings.tiingo_api_key})
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
        days=PROBE_EQUITY_LOOKBACK_DAYS
    )
    sdf = client.get_dataframe(
        PROBE_EQUITY_SYMBOL, frequency="daily", startDate=start.strftime("%Y-%m-%d")
    )
    if sdf.empty:
        return None
    return _to_utc(pd.Timestamp(sdf.index.max()))


def probe_fred_series(series_id: str) -> pd.Timestamp | None:
    """Latest non-NaN observation date FRED serves for one series."""
    from fredapi import Fred

    fred = Fred(api_key=settings.fred_api_key)
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
        days=PROBE_FRED_LOOKBACK_DAYS
    )
    s = fred.get_series(series_id, observation_start=start.strftime("%Y-%m-%d"))
    if s is None:
        return None
    s = s.dropna()
    if s.empty:
        return None
    return _to_utc(pd.Timestamp(s.index.max()))


def _probe_headers() -> dict[str, str]:
    return {
        "User-Agent": settings.edgar_user_agent or "quant-availability-probe/1.0",
        "Accept": "application/json",
    }


def probe_edgar() -> pd.Timestamp | None:
    """Latest relevant filing date across the pinned 3-filer probe subset."""
    latest: pd.Timestamp | None = None
    with httpx.Client(headers=_probe_headers(), timeout=20.0) as client:
        for cik in PROBE_EDGAR_CIKS.values():
            time.sleep(0.11)  # SEC politeness, same throttle as ingest/edgar.py
            resp = client.get(
                f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
            )
            resp.raise_for_status()
            recent = resp.json().get("filings", {}).get("recent", {})
            for form, date_str in zip(
                recent.get("form", []), recent.get("filingDate", [])
            ):
                if form not in PROBE_EDGAR_FORMS:
                    continue
                ts = pd.Timestamp(date_str, tz="UTC")
                if latest is None or ts > latest:
                    latest = ts
    return latest


def probe_rss() -> pd.Timestamp | None:
    """Newest parseable ``pubDate`` across all configured RSS feeds."""
    latest: pd.Timestamp | None = None
    with httpx.Client(
        headers=_probe_headers(), timeout=20.0, follow_redirects=True
    ) as client:
        for feed_url in settings.rss_feed_urls:
            try:
                resp = client.get(feed_url)
                resp.raise_for_status()
                xml = resp.text
            except Exception:  # one bad feed must not abort the probe (mirror ingest)
                continue
            for item_xml in re.findall(r"<item>(.*?)</item>", xml, re.DOTALL):
                m = re.search(r"<pubDate[^>]*>(.*?)</pubDate>", item_xml, re.DOTALL)
                ts = _parse_pubdate(m.group(1).strip()) if m else None
                if ts is not None and (latest is None or ts > latest):
                    latest = ts
    return latest


def build_probes() -> dict[str, Callable[[], pd.Timestamp | None]]:
    """One probe per monitored feed, keyed by the SOURCE_SLAS name.

    Built *from* ``SOURCE_SLAS`` so the probe set cannot silently drift from
    the monitored set: an SLA feed without a probe raises here (and the drift
    test asserts name parity in both directions).
    """
    probes: dict[str, Callable[[], pd.Timestamp | None]] = {}
    for spec in mf.SOURCE_SLAS:
        if spec.kind is mf.FreshnessKind.FRED_RELEASE:
            assert spec.fred_series is not None
            probes[spec.name] = partial(probe_fred_series, spec.fred_series)
        elif spec.name == "alpaca":
            probes[spec.name] = probe_alpaca
        elif spec.name == "tiingo":
            probes[spec.name] = probe_tiingo
        elif spec.name == "edgar":
            probes[spec.name] = probe_edgar
        elif spec.name == "rss":
            probes[spec.name] = probe_rss
        else:
            raise KeyError(f"no availability probe registered for SLA feed {spec.name!r}")
    return probes


def run_probes(
    now: pd.Timestamp | None = None,
    probes: dict[str, Callable[[], pd.Timestamp | None]] | None = None,
) -> list[ProbeRecord]:
    """Run every probe once; errors become ``status=error`` rows, never raises."""
    measured_at = _to_utc(now) if now is not None else pd.Timestamp.now(tz="UTC")
    probes = probes if probes is not None else build_probes()
    records: list[ProbeRecord] = []
    for name, probe in probes.items():
        try:
            latest = probe()
        except Exception as exc:  # a failed probe is data, not a crash
            records.append(
                ProbeRecord(measured_at, name, None, "error", f"{type(exc).__name__}: {exc}")
            )
            continue
        detail = f"latest={latest.date()}" if latest is not None else "no data returned"
        records.append(ProbeRecord(measured_at, name, latest, "ok", detail))
    return records


# ─── Append-only log I/O ───────────────────────────────────────────────────────


def append_records(records: Sequence[ProbeRecord], path: Path) -> None:
    """Append rows to the CSV log, creating directory + header on first write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(LOG_COLUMNS)
        for r in records:
            writer.writerow(
                [
                    r.measured_at.isoformat(),
                    r.source,
                    r.latest_available.isoformat() if r.latest_available is not None else "",
                    r.status,
                    r.detail,
                ]
            )


def load_log(path: Path) -> pd.DataFrame:
    """Read the log with parsed UTC timestamps; empty frame if it doesn't exist."""
    if not path.exists():
        return pd.DataFrame(columns=list(LOG_COLUMNS))
    df = pd.read_csv(path)
    df["measured_at"] = pd.to_datetime(df["measured_at"], utc=True)
    df["latest_available"] = pd.to_datetime(
        df["latest_available"], utc=True, errors="coerce"
    )
    df["detail"] = df["detail"].fillna("")
    return df


def _ok_rows(log: pd.DataFrame, source: str) -> pd.DataFrame:
    ok = log[(log["source"] == source) & (log["status"] == "ok")].copy()
    return ok.sort_values("measured_at").reset_index(drop=True)


# ─── Pure reduction core (no network, no lake) ─────────────────────────────────


def first_seen_table(log: pd.DataFrame) -> pd.DataFrame:
    """Per (source, observation date): when the log first saw it available.

    ``first_seen_at`` is an *upper bound* on true availability — the data
    became available somewhere in ``(prior_poll_at, first_seen_at]``. Rows
    with ``status=error`` or no returned data never contribute.
    """
    rows: list[dict] = []
    for source in log["source"].dropna().unique():
        ok = _ok_rows(log, source)
        seen = ok[ok["latest_available"].notna()].copy()
        if seen.empty:
            continue
        seen["latest_date"] = seen["latest_available"].dt.date
        for obs_date in sorted(seen["latest_date"].unique()):
            visible = seen[seen["latest_date"] >= obs_date]
            first = visible["measured_at"].min()
            prior = ok[ok["measured_at"] < first]["measured_at"].max()
            rows.append(
                {
                    "source": source,
                    "obs_date": obs_date,
                    "first_seen_at": first,
                    "prior_poll_at": prior if pd.notna(prior) else None,
                }
            )
    return pd.DataFrame(
        rows, columns=["source", "obs_date", "first_seen_at", "prior_poll_at"]
    )


def price_deadline(spec: mf.SourceSLA, session: dt.date) -> pd.Timestamp:
    """The pinned SLA deadline instant for one session of a deadline feed."""
    assert spec.deadline_hour_utc is not None and spec.deadline_day_offset is not None
    return pd.Timestamp(
        session + dt.timedelta(days=spec.deadline_day_offset), tz="UTC"
    ) + pd.Timedelta(hours=spec.deadline_hour_utc)


def price_session_verdicts(log: pd.DataFrame, source: str) -> pd.DataFrame:
    """Exact per-session on_time / miss / uncovered verdicts for a deadline feed.

    Sessions are enumerated from the trading calendar across the log's poll
    window (not just observed bar dates), so a session whose bar never showed
    up still gets a miss verdict once an absent poll past its deadline exists.
    """
    spec = _SPEC_BY_NAME[source]
    ok = _ok_rows(log, source)
    if ok.empty:
        return pd.DataFrame(
            columns=["source", "session", "deadline", "first_seen_at", "verdict", "margin_hours"]
        )
    ok["latest_date"] = ok["latest_available"].dt.date
    # Reach back a few days before the first poll: a poll can carry evidence
    # about a session whose deadline passed just before the window opened.
    sessions = trading_days(
        ok["measured_at"].min().date() - dt.timedelta(days=5),
        ok["measured_at"].max().date(),
    )
    rows: list[dict] = []
    for session in sessions:
        deadline = price_deadline(spec, session)
        # Settle floor: polls during session T see a PARTIAL bar for T — only
        # polls at/after T's market close can attest the settled bar.
        settle_floor = pd.Timestamp(session, tz="UTC") + pd.Timedelta(
            hours=PRICE_SETTLE_FLOOR_HOUR_UTC
        )
        attest = ok[
            ok["latest_available"].notna()
            & (ok["latest_date"] >= session)
            & (ok["measured_at"] >= settle_floor)
        ]
        seen_at = attest["measured_at"].min() if not attest.empty else None
        on_time = seen_at is not None and seen_at <= deadline
        absent_after = ok[
            (ok["measured_at"] >= deadline)
            & (ok["latest_available"].notna())
            & (ok["latest_date"] < session)
        ]
        miss = not absent_after.empty
        if on_time and miss:
            verdict = "conflict"  # publisher data regressed — investigate the log
        elif on_time:
            verdict = "on_time"
        elif miss:
            verdict = "miss"
        else:
            verdict = "uncovered"
        margin = (
            (deadline - seen_at).total_seconds() / 3600.0 if seen_at is not None else None
        )
        rows.append(
            {
                "source": source,
                "session": session,
                "deadline": deadline,
                "first_seen_at": seen_at,
                "verdict": verdict,
                "margin_hours": margin,
            }
        )
    return pd.DataFrame(rows)


def stale_poll_counts(log: pd.DataFrame) -> dict[str, int]:
    """Per feed: polls where the *publisher itself* evaluated STALE.

    Reuses the monitor's ``evaluate_feed`` verbatim — a stale poll here means
    the pinned SLA was not achievable at that instant even with a perfect
    ingest path. MISSING (no data returned) is not counted as stale.
    """
    counts: dict[str, int] = {name: 0 for name in _SPEC_BY_NAME}
    ok = log[(log["status"] == "ok") & log["latest_available"].notna()]
    for row in ok.itertuples():
        spec = _SPEC_BY_NAME.get(row.source)
        if spec is None:
            continue
        state = mf.evaluate_feed(spec, row.latest_available, row.measured_at).state
        if state is mf.FeedState.STALE:
            counts[row.source] += 1
    return counts


def fred_lag_table(log: pd.DataFrame) -> pd.DataFrame:
    """Observed publication lag (business days) per FRED observation.

    ``observed_lag_bdays`` is an upper bound (first-seen censoring); the
    pinned allowance is ``FRED_PUBLICATION_LAGS[series] + FRED_GRACE_BDAYS``.
    """
    fs = first_seen_table(log)
    rows: list[dict] = []
    for row in fs.itertuples():
        spec = _SPEC_BY_NAME.get(row.source)
        if spec is None or spec.kind is not mf.FreshnessKind.FRED_RELEASE:
            continue
        allowed = FRED_PUBLICATION_LAGS[spec.fred_series] + mf.FRED_GRACE_BDAYS
        observed = int(np.busday_count(row.obs_date, row.first_seen_at.date()))
        rows.append(
            {
                "source": row.source,
                "obs_date": row.obs_date,
                "first_seen_date": row.first_seen_at.date(),
                "observed_lag_bdays": observed,
                "allowed_lag_bdays": allowed,
                "within_allowance": observed <= allowed,
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "source",
            "obs_date",
            "first_seen_date",
            "observed_lag_bdays",
            "allowed_lag_bdays",
            "within_allowance",
        ],
    )


@dataclass(frozen=True)
class TightenProposal:
    """A *proposal* to move a deadline earlier — never applied automatically."""

    source: str
    n_on_time_sessions: int
    worst_first_seen_hours: float  # hours after the session's 00:00 UTC
    proposed_day_offset: int
    proposed_hour_utc: int
    current_day_offset: int
    current_hour_utc: int


def tighten_proposal(
    verdicts: pd.DataFrame, source: str, stale_polls: int
) -> TightenProposal | None:
    """Eligible iff ≥ MIN sessions, all on_time, and zero stale polls."""
    if verdicts.empty:
        return None
    informative = verdicts[verdicts["verdict"].isin(["on_time", "miss", "conflict"])]
    on_time = verdicts[verdicts["verdict"] == "on_time"]
    if (
        len(informative) < MIN_SESSIONS_FOR_SLA_VERDICT
        or len(on_time) != len(informative)
        or stale_polls > 0
    ):
        return None
    spec = _SPEC_BY_NAME[source]
    assert spec.deadline_hour_utc is not None and spec.deadline_day_offset is not None
    worst_hours = max(
        (row.first_seen_at - pd.Timestamp(row.session, tz="UTC")).total_seconds()
        / 3600.0
        for row in on_time.itertuples()
    )
    proposed_total = math.ceil(worst_hours + TIGHTEN_BUFFER_HOURS)
    current_total = spec.deadline_day_offset * 24 + spec.deadline_hour_utc
    if proposed_total >= current_total:
        return None  # no headroom — the pinned deadline is already tight
    return TightenProposal(
        source=source,
        n_on_time_sessions=len(on_time),
        worst_first_seen_hours=worst_hours,
        proposed_day_offset=proposed_total // 24,
        proposed_hour_utc=proposed_total % 24,
        current_day_offset=spec.deadline_day_offset,
        current_hour_utc=spec.deadline_hour_utc,
    )


# ─── Report rendering ──────────────────────────────────────────────────────────


def _fmt_deadline(day_offset: int, hour: int) -> str:
    return f"T+{day_offset} {hour:02d}:00 UTC" if day_offset else f"T {hour:02d}:00 UTC"


def availability_report(log: pd.DataFrame) -> str:
    """Render the accrued evidence + verdict-eligibility per feed."""
    lines: list[str] = ["Availability measurement report (C1-M1-MEASURE)"]
    if log.empty:
        lines.append("  (log is empty — no polls recorded yet)")
        return "\n".join(lines)

    n_polls = log["measured_at"].nunique()
    lines.append(
        f"  polls: {n_polls} instants, "
        f"{log['measured_at'].min()} → {log['measured_at'].max()}"
    )
    stale = stale_poll_counts(log)

    for source in _DEADLINE_FEEDS:
        spec = _SPEC_BY_NAME[source]
        verdicts = price_session_verdicts(log, source)
        counts = verdicts["verdict"].value_counts() if not verdicts.empty else {}
        on_time, miss = int(counts.get("on_time", 0)), int(counts.get("miss", 0))
        conflict, uncovered = int(counts.get("conflict", 0)), int(counts.get("uncovered", 0))
        informative = on_time + miss + conflict
        lines.append(
            f"  [{source}] SLA {_fmt_deadline(spec.deadline_day_offset, spec.deadline_hour_utc)} — "
            f"sessions: {on_time} on_time / {miss} miss / {conflict} conflict / "
            f"{uncovered} uncovered; stale polls: {stale[source]}"
        )
        margins = verdicts.loc[verdicts["verdict"] == "on_time", "margin_hours"]
        if not margins.empty:
            lines.append(
                f"           margin vs deadline (h): min {margins.min():.1f} / "
                f"median {margins.median():.1f} / max {margins.max():.1f}"
            )
        eligible = informative >= MIN_SESSIONS_FOR_SLA_VERDICT
        lines.append(
            f"           verdict-eligible: {eligible} "
            f"({informative}/{MIN_SESSIONS_FOR_SLA_VERDICT} informative sessions)"
        )
        proposal = tighten_proposal(verdicts, source, stale[source])
        if proposal is not None:
            lines.append(
                f"           tighten proposal: no earlier than "
                f"{_fmt_deadline(proposal.proposed_day_offset, proposal.proposed_hour_utc)} "
                f"(worst first-seen {proposal.worst_first_seen_hours:.1f}h + "
                f"{TIGHTEN_BUFFER_HOURS}h buffer; current "
                f"{_fmt_deadline(proposal.current_day_offset, proposal.current_hour_utc)}) "
                f"— requires PRD revision + ledger entry to adopt"
            )

    fred = fred_lag_table(log)
    for source in sorted(s.name for s in mf.SOURCE_SLAS if s.fred_series):
        g = fred[fred["source"] == source]
        if g.empty:
            lines.append(f"  [{source}] no observations seen yet; stale polls: {stale[source]}")
            continue
        lines.append(
            f"  [{source}] observed lag (bdays, upper bound): "
            f"min {int(g['observed_lag_bdays'].min())} / "
            f"max {int(g['observed_lag_bdays'].max())} vs allowed "
            f"{int(g['allowed_lag_bdays'].iloc[0])}; "
            f"{int(g['within_allowance'].sum())}/{len(g)} within allowance; "
            f"stale polls: {stale[source]}"
        )

    for source in ("edgar", "rss"):
        ok = _ok_rows(log, source)
        seen = ok[ok["latest_available"].notna()]
        if seen.empty:
            lines.append(f"  [{source}] (liveness, informational) no items seen yet")
            continue
        max_quiet = (seen["measured_at"] - seen["latest_available"]).max()
        lines.append(
            f"  [{source}] (liveness, informational) max observed quiet gap: "
            f"{max_quiet.total_seconds() / 86400.0:.1f} days; stale polls: {stale[source]}"
        )

    n_errors = int((log["status"] == "error").sum())
    lines.append(f"  probe errors in log: {n_errors} (excluded from all inference)")
    return "\n".join(lines)


# ─── CLI ───────────────────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    """Default: one poll (probe + append). ``--report``: reduce the log."""
    parser = argparse.ArgumentParser(
        description="Measure live per-source data availability against the pinned C1 SLAs.",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Reduce the accrued log to first-availability + SLA-margin evidence.",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=LOG_PATH_DEFAULT,
        help=f"Path of the append-only measurement log (default: {LOG_PATH_DEFAULT}).",
    )
    args = parser.parse_args(argv)

    if args.report:
        print(availability_report(load_log(args.log)))
        return 0

    records = run_probes()
    append_records(records, args.log)
    for r in records:
        print(f"  [{r.status:>5}] {r.source:<12} {r.detail}")
    errors = [r for r in records if r.status == "error"]
    for r in errors:
        print(f"probe error: {r.source}: {r.detail}", file=sys.stderr)
    # All probes failing means the measurement loop itself is broken (network,
    # credentials) — that is the one condition worth a non-zero exit.
    return 1 if len(errors) == len(records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
