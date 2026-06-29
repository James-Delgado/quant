"""Tests for the C1-M3 data-freshness monitor and its G3 gate.

Covers:
  * The pure SLA-evaluation core (``evaluate_feed``) per source kind, in both
    the fresh and stale directions, plus the missing-feed case.
  * The **G3 gate** (``freshness_gate_report``): on synthetic fresh/stale
    fixtures derived from the pinned SLA table, ``(false_stale, missed_stale)``
    must be ``(0, 0)`` (C1 PRD G3). A deliberately-broken predicate must FAIL the
    gate (the gate is not vacuously satisfiable).
  * The **drift contract** (METHODOLOGY §6): the in-code SLA constants match the
    frozen C1-M1 values, FRED reuses the engineering lags, and the monitored set
    is exactly the C1-M1 table (no phantom / unregistered feed).
  * The lake-reading + CLI wiring on a synthetic processed lake (fresh ⇒ exit 0,
    stale ⇒ exit 1).

All thresholds are read from the module constants — never re-hardcoded here — so
the tests fail if a pinned SLA value drifts from the frozen contract.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant.features.engineering import FRED_PUBLICATION_LAGS, _FRED_SERIES
from quant.storage import lake
from quant.utils.calendar import last_trading_day, trading_days

# Load the monitor script as a module without making ``scripts/`` a package
# (setuptools.packages.find is scoped to ``src/`` — see pyproject.toml and the
# identical pattern in tests/test_phase4a_runner.py). importlib gives a real
# module object, equivalent to ``import scripts.monitor_freshness as mf``.
_MONITOR_PATH = Path(__file__).resolve().parent.parent / "scripts" / "monitor_freshness.py"
_spec = importlib.util.spec_from_file_location("monitor_freshness", _MONITOR_PATH)
assert _spec is not None and _spec.loader is not None
mf = importlib.util.module_from_spec(_spec)
sys.modules["monitor_freshness"] = mf
_spec.loader.exec_module(mf)

EDGAR_MAX_STALE_TRADING_DAYS = mf.EDGAR_MAX_STALE_TRADING_DAYS
FRED_GRACE_BDAYS = mf.FRED_GRACE_BDAYS
RSS_MAX_STALE_CALENDAR_DAYS = mf.RSS_MAX_STALE_CALENDAR_DAYS
SOURCE_SLAS = mf.SOURCE_SLAS
FeedState = mf.FeedState
FreshnessKind = mf.FreshnessKind
evaluate_feed = mf.evaluate_feed
format_report = mf.format_report
freshness_gate_report = mf.freshness_gate_report
main = mf.main
monitor = mf.monitor

# A fixed reference "now": a Wednesday well clear of US market holidays so the
# trading-day arithmetic is unambiguous. 2026-06-24 is a Wednesday.
NOW = pd.Timestamp("2026-06-24T23:30:00Z")


def _spec(name: str) -> mf.SourceSLA:
    return next(s for s in SOURCE_SLAS if s.name == name)


# ─── Pure-core: price-deadline feeds (Alpaca, Tiingo) ──────────────────────────


def test_alpaca_fresh_when_today_bar_present_after_deadline():
    spec = _spec("alpaca")
    required = mf._required_date(spec, NOW)
    # latest bar exactly on the required session ⇒ fresh.
    status = evaluate_feed(spec, pd.Timestamp(required, tz="UTC"), NOW)
    assert status.state is FeedState.FRESH
    # Required date is the last trading day (deadline 23:00 UTC has passed @ 23:30).
    assert required == last_trading_day(NOW.date())


def test_alpaca_stale_when_latest_bar_predates_required_session():
    spec = _spec("alpaca")
    required = mf._required_date(spec, NOW)
    prior = mf._nth_trading_day_before(required - dt.timedelta(days=1), 0)
    status = evaluate_feed(spec, pd.Timestamp(prior, tz="UTC"), NOW)
    assert status.state is FeedState.STALE
    assert status.required_date == required


def test_alpaca_does_not_demand_today_bar_before_its_deadline():
    spec = _spec("alpaca")
    # 18:00 UTC on the same Wednesday: the 23:00 UTC deadline for T has NOT passed,
    # so the monitor must require only the PRIOR session, not today's bar.
    morning = pd.Timestamp("2026-06-24T18:00:00Z")
    required = mf._required_date(spec, morning)
    today = last_trading_day(morning.date())
    assert required < today
    # Having only yesterday's bar at 18:00 UTC is therefore still fresh.
    status = evaluate_feed(spec, pd.Timestamp(required, tz="UTC"), morning)
    assert status.state is FeedState.FRESH


def test_tiingo_required_date_lags_alpaca_by_a_session():
    # Tiingo's T+1 12:00 UTC deadline means at 23:30 UTC on T it cannot yet
    # require T's bar (only available T+1), so its required date is older than
    # Alpaca's (which is due 23:00 UTC on T).
    tiingo_req = mf._required_date(_spec("tiingo"), NOW)
    alpaca_req = mf._required_date(_spec("alpaca"), NOW)
    assert tiingo_req < alpaca_req


def test_tiingo_fresh_after_next_day_noon():
    spec = _spec("tiingo")
    # 13:00 UTC on Thursday: Wednesday's (T) bar is now due (T+1 12:00 UTC passed).
    thursday_pm = pd.Timestamp("2026-06-25T13:00:00Z")
    required = mf._required_date(spec, thursday_pm)
    assert required == dt.date(2026, 6, 24)  # Wednesday
    status = evaluate_feed(spec, pd.Timestamp(required, tz="UTC"), thursday_pm)
    assert status.state is FeedState.FRESH


# ─── Pure-core: FRED per-series release window ─────────────────────────────────


@pytest.mark.parametrize("series", list(_FRED_SERIES))
def test_fred_fresh_at_required_boundary(series: str):
    spec = _spec(f"fred:{series}")
    required = mf._required_date(spec, NOW)
    # The required date is lag + grace business days before now.
    lag = FRED_PUBLICATION_LAGS[series]
    expected = pd.Timestamp(
        np.busday_offset(NOW.date(), -(lag + FRED_GRACE_BDAYS), roll="backward")
    ).date()
    assert required == expected
    assert evaluate_feed(spec, pd.Timestamp(required, tz="UTC"), NOW).state is FeedState.FRESH


@pytest.mark.parametrize("series", list(_FRED_SERIES))
def test_fred_stale_one_business_day_past_window(series: str):
    spec = _spec(f"fred:{series}")
    required = mf._required_date(spec, NOW)
    one_bday_older = pd.Timestamp(
        np.busday_offset(required, -1, roll="backward")
    ).date()
    status = evaluate_feed(spec, pd.Timestamp(one_bday_older, tz="UTC"), NOW)
    assert status.state is FeedState.STALE


# ─── Pure-core: liveness feeds (EDGAR trading-day, RSS calendar-day) ───────────


def test_edgar_fresh_within_one_trading_day():
    spec = _spec("edgar")
    required = mf._required_date(spec, NOW)
    assert required == mf._nth_trading_day_before(NOW.date(), EDGAR_MAX_STALE_TRADING_DAYS)
    assert evaluate_feed(spec, pd.Timestamp(required, tz="UTC"), NOW).state is FeedState.FRESH


def test_edgar_stale_when_no_scan_in_two_sessions():
    spec = _spec("edgar")
    required = mf._required_date(spec, NOW)
    older = mf._nth_trading_day_before(required - dt.timedelta(days=1), 0)
    assert evaluate_feed(spec, pd.Timestamp(older, tz="UTC"), NOW).state is FeedState.STALE


def test_rss_fresh_within_one_calendar_day():
    spec = _spec("rss")
    required = NOW.date() - dt.timedelta(days=RSS_MAX_STALE_CALENDAR_DAYS)
    assert mf._required_date(spec, NOW) == required
    assert evaluate_feed(spec, pd.Timestamp(required, tz="UTC"), NOW).state is FeedState.FRESH


def test_rss_stale_when_item_older_than_one_calendar_day():
    spec = _spec("rss")
    older = NOW.date() - dt.timedelta(days=RSS_MAX_STALE_CALENDAR_DAYS + 1)
    assert evaluate_feed(spec, pd.Timestamp(older, tz="UTC"), NOW).state is FeedState.STALE


# ─── Missing feed ──────────────────────────────────────────────────────────────


def test_missing_feed_when_no_observation():
    spec = _spec("alpaca")
    status = evaluate_feed(spec, None, NOW)
    assert status.state is FeedState.MISSING
    assert status.is_alert
    assert status.latest is None


# ─── As-of normalization ───────────────────────────────────────────────────────


def test_naive_latest_and_now_are_treated_as_utc():
    spec = _spec("rss")
    # Naive inputs must be accepted and interpreted as UTC (no tz error).
    naive_now = dt.datetime(2026, 6, 24, 23, 30)
    fresh_date = naive_now.date() - dt.timedelta(days=RSS_MAX_STALE_CALENDAR_DAYS)
    status = evaluate_feed(spec, dt.datetime.combine(fresh_date, dt.time()), naive_now)
    assert status.state is FeedState.FRESH


# ─── G3 gate (the merge-relevant correctness predicate) ────────────────────────


def _fresh_fixture(spec: mf.SourceSLA) -> tuple[mf.SourceSLA, pd.Timestamp, pd.Timestamp]:
    required = mf._required_date(spec, NOW)
    return (spec, pd.Timestamp(required, tz="UTC"), NOW)


def _stale_fixture(spec: mf.SourceSLA) -> tuple[mf.SourceSLA, pd.Timestamp, pd.Timestamp]:
    required = mf._required_date(spec, NOW)
    if spec.kind is FreshnessKind.FRED_RELEASE:
        older = pd.Timestamp(np.busday_offset(required, -1, roll="backward")).date()
    elif spec.kind is FreshnessKind.LIVENESS_CALENDAR:
        older = required - dt.timedelta(days=1)
    else:  # price deadline + trading-day liveness step back one session
        older = mf._nth_trading_day_before(required - dt.timedelta(days=1), 0)
    return (spec, pd.Timestamp(older, tz="UTC"), NOW)


def test_g3_gate_passes_zero_zero_on_pinned_table():
    fresh = [_fresh_fixture(s) for s in SOURCE_SLAS]
    stale = [_stale_fixture(s) for s in SOURCE_SLAS]
    result = freshness_gate_report(fresh, stale)
    assert (result.false_stale_count, result.missed_stale_count) == (0, 0)
    assert result.passed
    assert result.n_fresh_cases == len(SOURCE_SLAS)
    assert result.n_stale_cases == len(SOURCE_SLAS)


def test_g3_gate_fails_when_a_stale_feed_is_missed():
    # Feed genuinely-fresh values in the *stale* bucket → every "stale" case is
    # actually fresh, so the gate must count missed-stale and fail.
    fresh = [_fresh_fixture(s) for s in SOURCE_SLAS]
    not_actually_stale = [_fresh_fixture(s) for s in SOURCE_SLAS]
    result = freshness_gate_report(fresh, not_actually_stale)
    assert result.missed_stale_count == len(SOURCE_SLAS)
    assert not result.passed


def test_g3_gate_fails_when_a_fresh_feed_false_alarms():
    # Stale values placed in the *fresh* bucket → every "fresh" case false-alarms.
    actually_stale = [_stale_fixture(s) for s in SOURCE_SLAS]
    stale = [_stale_fixture(s) for s in SOURCE_SLAS]
    result = freshness_gate_report(actually_stale, stale)
    assert result.false_stale_count == len(SOURCE_SLAS)
    assert not result.passed


# ─── Drift contract: code SLA constants ⇔ frozen C1-M1 values (METHODOLOGY §6) ─


def test_monitored_set_is_exactly_the_c1m1_table():
    names = {s.name for s in SOURCE_SLAS}
    expected = {"alpaca", "tiingo", "edgar", "rss"} | {f"fred:{s}" for s in _FRED_SERIES}
    assert names == expected, "monitored feeds drifted from the C1-M1 SLA table"


def test_pinned_sla_constants_match_frozen_contract():
    # The frozen C1-M1 values, asserted literally so a silent edit to the module
    # constants (or this list) trips the drift test in both directions.
    assert mf.ALPACA_DEADLINE_HOUR_UTC == 23
    assert mf.ALPACA_DEADLINE_DAY_OFFSET == 0
    assert mf.TIINGO_DEADLINE_HOUR_UTC == 12
    assert mf.TIINGO_DEADLINE_DAY_OFFSET == 1
    assert EDGAR_MAX_STALE_TRADING_DAYS == 1
    assert RSS_MAX_STALE_CALENDAR_DAYS == 1


def test_fred_feeds_reuse_engineering_lags_no_new_values():
    # FRED parity lever: the monitor must not introduce its own lag values.
    for series in _FRED_SERIES:
        spec = _spec(f"fred:{series}")
        assert spec.fred_series == series
        assert series in FRED_PUBLICATION_LAGS


# ─── Drift contract: code SLA constants ⇔ the DOCS (METHODOLOGY §6) ─────────────
# The asserts above pin code against literal values (trip CI on a *code* edit).
# These pin code against the human-readable SLA tables in the C1-M1 contract doc
# and the C1-M3 runner doc — the gap C1-M3-SLA-DOC-DRIFT closes: without them the
# prose and the constants could silently diverge. Every expected substring is
# built from the *live* module constant, so the contract bites in both directions
# (a code edit OR a doc edit that breaks lock-step makes the substring absent).

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SLA_CONTRACT_DOC = "docs/concepts/data-freshness-slas.md"
_MONITOR_RUNNER_DOC = "docs/concepts/freshness-monitor.md"


def _read_doc(rel_path: str) -> str:
    return (_REPO_ROOT / rel_path).read_text(encoding="utf-8")


def _table_row(doc_text: str, label: str) -> str:
    """The first Markdown table row (a ``|``-delimited line) containing *label*.

    Raises if no such row exists, so dropping a documented feed fails the test
    rather than passing vacuously.
    """
    for line in doc_text.splitlines():
        if line.lstrip().startswith("|") and label in line:
            return line
    raise AssertionError(f"no SLA table row containing {label!r}")


def _available_by_phrase(hour_utc: int, day_offset: int) -> str:
    """The doc's "available by" wording for a price deadline, from the constants.

    ``day_offset == 0`` ⇒ same session ("on trading day T"); ``> 0`` ⇒ "on T+N".
    """
    day = "on trading day T" if day_offset == 0 else f"on T+{day_offset}"
    return f"{hour_utc}:00 UTC {day}"


def test_c1m1_contract_doc_matches_code_constants():
    # docs/concepts/data-freshness-slas.md "Pinned per-source freshness SLA" table
    # is the frozen C1-M1 contract the monitor reproduces in code. Assert each row
    # encodes the live constant's value.
    doc = _read_doc(_SLA_CONTRACT_DOC)

    alpaca = _table_row(doc, "Alpaca IEX daily bar")
    assert (
        _available_by_phrase(mf.ALPACA_DEADLINE_HOUR_UTC, mf.ALPACA_DEADLINE_DAY_OFFSET)
        in alpaca
    ), "Alpaca SLA prose drifted from ALPACA_DEADLINE_* constants"

    tiingo = _table_row(doc, "Tiingo adjusted EOD")
    assert (
        _available_by_phrase(mf.TIINGO_DEADLINE_HOUR_UTC, mf.TIINGO_DEADLINE_DAY_OFFSET)
        in tiingo
    ), "Tiingo SLA prose drifted from TIINGO_DEADLINE_* constants"

    edgar = _table_row(doc, "EDGAR 8-K")
    assert f"{mf.EDGAR_MAX_STALE_TRADING_DAYS} trading day" in edgar, (
        "EDGAR liveness prose drifted from EDGAR_MAX_STALE_TRADING_DAYS"
    )

    rss = _table_row(doc, "| RSS ")
    assert f"{mf.RSS_MAX_STALE_CALENDAR_DAYS} calendar day" in rss, (
        "RSS liveness prose drifted from RSS_MAX_STALE_CALENDAR_DAYS"
    )

    # FRED reuses the pinned engineering dict by name — the contract states no
    # literal lag value, so assert the doc names the parity lever the code reads.
    fred = _table_row(doc, "FRED (per series)")
    assert "FRED_PUBLICATION_LAGS" in fred, (
        "FRED SLA row no longer references the FRED_PUBLICATION_LAGS parity lever"
    )


def test_monitor_runner_doc_matches_code_constants():
    # docs/concepts/freshness-monitor.md "Source-of-truth constant" column writes
    # NAME=value tokens. Build each token from the live constant and require it.
    doc = _read_doc(_MONITOR_RUNNER_DOC)

    for name in (
        "ALPACA_DEADLINE_HOUR_UTC",
        "TIINGO_DEADLINE_HOUR_UTC",
        "FRED_GRACE_BDAYS",
        "EDGAR_MAX_STALE_TRADING_DAYS",
        "RSS_MAX_STALE_CALENDAR_DAYS",
    ):
        token = f"{name}={getattr(mf, name)}"
        assert token in doc, f"{token!r} not documented in {_MONITOR_RUNNER_DOC}"

    # The day-offset constants are written abbreviated (…DAY_OFFSET=) per feed row.
    alpaca = _table_row(doc, "`alpaca`")
    assert f"DAY_OFFSET={mf.ALPACA_DEADLINE_DAY_OFFSET}" in alpaca
    tiingo = _table_row(doc, "`tiingo`")
    assert f"DAY_OFFSET={mf.TIINGO_DEADLINE_DAY_OFFSET}" in tiingo

    # FRED row names the engineering dict rather than a literal lag.
    assert "FRED_PUBLICATION_LAGS" in doc


def test_doc_drift_contract_is_not_vacuous(monkeypatch):
    # The contract must bite: with a constant diverged from the docs, the value
    # the doc-parse tests require to be present must be ABSENT. Mirrors the G3
    # "gate not vacuously satisfiable" tests above.
    contract = _read_doc(_SLA_CONTRACT_DOC)
    runner = _read_doc(_MONITOR_RUNNER_DOC)

    monkeypatch.setattr(mf, "EDGAR_MAX_STALE_TRADING_DAYS", 99, raising=True)
    assert f"{mf.EDGAR_MAX_STALE_TRADING_DAYS} trading day" not in contract
    assert f"EDGAR_MAX_STALE_TRADING_DAYS={mf.EDGAR_MAX_STALE_TRADING_DAYS}" not in runner

    monkeypatch.setattr(mf, "TIINGO_DEADLINE_HOUR_UTC", 7, raising=True)
    assert (
        _available_by_phrase(mf.TIINGO_DEADLINE_HOUR_UTC, mf.TIINGO_DEADLINE_DAY_OFFSET)
        not in contract
    )


# ─── Lake-reading + CLI wiring on a synthetic processed lake ───────────────────


def _write_prices(dataset: str, latest_date: dt.date) -> None:
    dates = pd.to_datetime([latest_date - dt.timedelta(days=2), latest_date], utc=True)
    df = pd.DataFrame(
        {
            "symbol": "AAPL",
            "timestamp": dates,
            "open": [1.0, 2.0],
            "high": [1.0, 2.0],
            "low": [1.0, 2.0],
            "close": [1.0, 2.0],
            "adjClose": [1.0, 2.0],
            "volume": [10.0, 20.0],
            "ingested_at": pd.Timestamp("2026-01-01", tz="UTC"),
        }
    )
    df["year"] = df["timestamp"].dt.year
    df["month"] = df["timestamp"].dt.month
    lake.write_processed(df, dataset=dataset, partition_cols=["year", "month"])


def _write_fred(latest_date: dt.date) -> None:
    rows = []
    for series in _FRED_SERIES:
        for d in (latest_date - dt.timedelta(days=3), latest_date):
            rows.append(
                {"series_id": series, "timestamp": pd.Timestamp(d, tz="UTC"), "value": 1.0}
            )
    df = pd.DataFrame(rows)
    df["ingested_at"] = pd.Timestamp("2026-01-01", tz="UTC")
    df["year"] = df["timestamp"].dt.year
    df["month"] = df["timestamp"].dt.month
    lake.write_processed(df, dataset="macro_fred", partition_cols=["year", "month"])


def _write_docs(edgar_date: dt.date, rss_date: dt.date) -> None:
    df = pd.DataFrame(
        {
            "document_id": ["e1", "r1"],
            "source": ["edgar", "rss_reuters"],
            "symbol": ["AAPL", "AAPL"],
            "published_at": [
                pd.Timestamp(edgar_date, tz="UTC"),
                pd.Timestamp(rss_date, tz="UTC"),
            ],
            "ingested_at": pd.Timestamp("2026-01-01", tz="UTC"),
            "text": ["a", "b"],
        }
    )
    df["year"] = df["published_at"].dt.year
    df["month"] = df["published_at"].dt.month
    lake.write_processed(df, dataset="text_documents", partition_cols=["year", "month"])


@pytest.fixture()
def all_fresh_lake(lake_root):
    """A processed lake where every feed is within SLA as of ``NOW``."""
    last_session = last_trading_day(NOW.date())
    _write_prices("equity_bars_daily", last_session)
    # Tiingo's required date is older than today (T+1 deadline); last session is fresh.
    _write_prices("equity_eod_tiingo", mf._required_date(_spec("tiingo"), NOW))
    _write_fred(mf._required_date(_spec("fred:DGS10"), NOW))
    _write_docs(
        edgar_date=mf._required_date(_spec("edgar"), NOW),
        rss_date=NOW.date(),
    )
    return lake_root


def test_monitor_all_fresh_exits_zero(all_fresh_lake):
    statuses = monitor(now=NOW)
    assert {s.name for s in statuses} == {s.name for s in SOURCE_SLAS}
    assert all(s.state is FeedState.FRESH for s in statuses), [
        (s.name, s.state.value, s.detail) for s in statuses
    ]
    assert main(["--now", NOW.isoformat()]) == 0


def test_monitor_flags_stale_feed_and_exits_one(all_fresh_lake):
    # Re-write Alpaca with a stale bar (a week before the required session).
    stale_anchor = mf._required_date(_spec("alpaca"), NOW) - dt.timedelta(days=7)
    stale_session = trading_days(stale_anchor - dt.timedelta(days=5), stale_anchor)[-1]
    _write_prices("equity_bars_daily", stale_session)
    statuses = {s.name: s for s in monitor(now=NOW)}
    assert statuses["alpaca"].state is FeedState.STALE
    assert main(["--now", NOW.isoformat()]) == 1


def test_monitor_missing_dataset_is_alerted(lake_root):
    # Empty lake: every feed reads as missing and the CLI exits non-zero.
    statuses = monitor(now=NOW)
    assert all(s.state is FeedState.MISSING for s in statuses)
    assert main(["--now", NOW.isoformat()]) == 1


def test_format_report_marks_alerts():
    fresh = evaluate_feed(_spec("rss"), pd.Timestamp(NOW.date(), tz="UTC"), NOW)
    stale = evaluate_feed(
        _spec("rss"), pd.Timestamp(NOW.date() - dt.timedelta(days=5), tz="UTC"), NOW
    )
    report = format_report([fresh, stale], NOW)
    assert "ALL FRESH" not in report
    assert "NEED ATTENTION" in report
