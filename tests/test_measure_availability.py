"""Tests for the C1-M1-MEASURE availability-measurement instrument.

Covers:
  * The pinned verdict-rule constants (METHODOLOGY §1) — re-asserted so a
    drive-by edit after data has accrued fails loudly.
  * The probe-registry drift contract (METHODOLOGY §6): the probe set equals
    the monitor's ``SOURCE_SLAS`` feed set in both directions.
  * Append-only log I/O round-trip (header once, error rows, None latest).
  * The pure reduction core: first-seen table (censoring bounds), exact
    per-session on_time / miss / uncovered verdicts, publisher-side stale-poll
    counting via the monitor's own ``evaluate_feed``, FRED observed-lag table.
  * The tightening proposal: eligibility gates (min sessions, zero miss, zero
    stale) and the worst-case + buffer arithmetic.
  * CLI wiring with probes stubbed (no network).

All SLA values are read from ``monitor_freshness`` constants — never
re-hardcoded — so these tests track the frozen C1-M1 contract.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mf = sys.modules.get("monitor_freshness") or _load("monitor_freshness")
ma = _load("measure_availability")


# Fixed, known NYSE trading week (Mon 2026-06-22 … Fri 2026-06-26).
MON, TUE, WED, THU, FRI = (
    dt.date(2026, 6, 22),
    dt.date(2026, 6, 23),
    dt.date(2026, 6, 24),
    dt.date(2026, 6, 25),
    dt.date(2026, 6, 26),
)


def _ts(day: dt.date, hour: int = 0, minute: int = 0) -> pd.Timestamp:
    return pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=hour, minutes=minute)


def _row(
    measured_at: pd.Timestamp,
    source: str,
    latest: pd.Timestamp | None,
    status: str = "ok",
) -> dict:
    return {
        "measured_at": measured_at,
        "source": source,
        "latest_available": latest if latest is not None else pd.NaT,
        "status": status,
        "detail": "",
    }


def _log(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(ma.LOG_COLUMNS))


# ─── Pinned constants ──────────────────────────────────────────────────────────


class TestPinnedConstants:
    def test_verdict_rule_constants(self):
        assert ma.MIN_SESSIONS_FOR_SLA_VERDICT == 10
        assert ma.TIGHTEN_BUFFER_HOURS == 2
        assert ma.PRICE_SETTLE_FLOOR_HOUR_UTC == 21

    def test_probe_targets(self):
        assert ma.PROBE_EQUITY_SYMBOL == "SPY"
        assert set(ma.PROBE_EDGAR_CIKS) == {"AAPL", "MSFT", "NVDA"}
        assert ma.PROBE_EDGAR_FORMS == frozenset({"8-K", "10-K", "10-Q"})

    def test_log_schema(self):
        assert ma.LOG_COLUMNS == (
            "measured_at",
            "source",
            "latest_available",
            "status",
            "detail",
        )


# ─── Probe registry drift contract ─────────────────────────────────────────────


class TestProbeRegistryDrift:
    def test_probe_set_equals_monitored_set(self):
        monitored = {s.name for s in mf.SOURCE_SLAS}
        probed = set(ma.build_probes())
        assert probed == monitored, (
            f"unregistered probes: {probed - monitored}; "
            f"unprobed feeds: {monitored - probed}"
        )

    def test_unknown_feed_raises(self, monkeypatch: pytest.MonkeyPatch):
        phantom = mf.SourceSLA(
            name="phantom",
            dataset="nope",
            kind=mf.FreshnessKind.LIVENESS_CALENDAR,
            max_stale_days=1,
        )
        monkeypatch.setattr(ma.mf, "SOURCE_SLAS", (*mf.SOURCE_SLAS, phantom))
        with pytest.raises(KeyError, match="phantom"):
            ma.build_probes()


# ─── Log I/O ───────────────────────────────────────────────────────────────────


class TestLogIO:
    def test_round_trip(self, tmp_path: Path):
        path = tmp_path / "log.csv"
        records = [
            ma.ProbeRecord(_ts(WED, 22), "alpaca", _ts(WED, 4), "ok", "latest=2026-06-24"),
            ma.ProbeRecord(_ts(WED, 22), "tiingo", None, "ok", "no data returned"),
            ma.ProbeRecord(_ts(WED, 22), "rss", None, "error", "TimeoutError: x"),
        ]
        ma.append_records(records, path)
        log = ma.load_log(path)
        assert len(log) == 3
        assert log["measured_at"].dt.tz is not None
        assert log.loc[0, "latest_available"] == _ts(WED, 4)
        assert pd.isna(log.loc[1, "latest_available"])
        assert log.loc[2, "status"] == "error"

    def test_append_writes_header_once(self, tmp_path: Path):
        path = tmp_path / "log.csv"
        rec = [ma.ProbeRecord(_ts(WED, 22), "alpaca", _ts(WED, 4), "ok", "d")]
        ma.append_records(rec, path)
        ma.append_records(rec, path)
        text = path.read_text()
        assert text.count("measured_at") == 1
        assert len(ma.load_log(path)) == 2

    def test_load_missing_is_empty(self, tmp_path: Path):
        log = ma.load_log(tmp_path / "absent.csv")
        assert log.empty
        assert list(log.columns) == list(ma.LOG_COLUMNS)


# ─── First-seen reduction ──────────────────────────────────────────────────────


class TestFirstSeen:
    def test_first_seen_and_censoring_bound(self):
        log = _log(
            [
                _row(_ts(TUE, 22), "alpaca", _ts(TUE, 4)),   # sees Tue bar
                _row(_ts(WED, 20), "alpaca", _ts(TUE, 4)),   # Wed bar not yet out
                _row(_ts(WED, 22), "alpaca", _ts(WED, 4)),   # Wed bar appears
            ]
        )
        fs = ma.first_seen_table(log)
        wed = fs[fs["obs_date"] == WED].iloc[0]
        assert wed["first_seen_at"] == _ts(WED, 22)
        assert wed["prior_poll_at"] == _ts(WED, 20)
        tue = fs[fs["obs_date"] == TUE].iloc[0]
        assert tue["first_seen_at"] == _ts(TUE, 22)
        assert pd.isna(tue["prior_poll_at"])  # first poll has no censoring bound

    def test_error_rows_never_contribute(self):
        log = _log(
            [
                _row(_ts(WED, 20), "alpaca", _ts(WED, 4), status="error"),
                _row(_ts(WED, 22), "alpaca", _ts(WED, 4)),
            ]
        )
        fs = ma.first_seen_table(log)
        assert fs[fs["obs_date"] == WED].iloc[0]["first_seen_at"] == _ts(WED, 22)


# ─── Per-session verdicts (the pre-committed rule) ─────────────────────────────


class TestSessionVerdicts:
    def test_on_time_alpaca(self):
        # Wed bar visible at 22:00 UTC — one hour before the 23:00 deadline.
        log = _log([_row(_ts(WED, 22), "alpaca", _ts(WED, 4))])
        v = ma.price_session_verdicts(log, "alpaca")
        row = v[v["session"] == WED].iloc[0]
        assert row["verdict"] == "on_time"
        assert row["margin_hours"] == pytest.approx(1.0)

    def test_miss_requires_absence_after_deadline(self):
        # Polls past Wed's 23:00 deadline still show only Tue's bar → miss.
        log = _log(
            [
                _row(_ts(WED, 23, 30), "alpaca", _ts(TUE, 4)),
                _row(_ts(THU, 10), "alpaca", _ts(TUE, 4)),
            ]
        )
        v = ma.price_session_verdicts(log, "alpaca")
        assert v[v["session"] == WED].iloc[0]["verdict"] == "miss"
        # Thursday's own deadline has not passed with an absent poll → uncovered.
        assert v[v["session"] == THU].iloc[0]["verdict"] == "uncovered"

    def test_gap_straddling_deadline_is_uncovered(self):
        # First sighting only after the deadline, and no absent poll after it:
        # no inference is made across the gap.
        log = _log([_row(_ts(THU, 1), "alpaca", _ts(WED, 4))])
        v = ma.price_session_verdicts(log, "alpaca")
        assert v[v["session"] == WED].iloc[0]["verdict"] == "uncovered"
        assert not (v["verdict"] == "on_time").any()
        assert not (v["verdict"] == "miss").any()

    def test_tiingo_t_plus_one_deadline(self):
        # Friday's adjusted bar seen Saturday 09:00 UTC — 3h before T+1 12:00.
        sat = FRI + dt.timedelta(days=1)
        log = _log([_row(_ts(sat, 9), "tiingo", _ts(FRI, 0))])
        v = ma.price_session_verdicts(log, "tiingo")
        row = v[v["session"] == FRI].iloc[0]
        assert row["verdict"] == "on_time"
        assert row["margin_hours"] == pytest.approx(3.0)

    def test_miss_before_log_window_is_caught(self):
        # A poll can carry miss evidence about a session whose deadline passed
        # just before the window opened (the -5d session reach-back).
        log = _log([_row(_ts(THU, 10), "alpaca", _ts(TUE, 4))])
        v = ma.price_session_verdicts(log, "alpaca")
        assert v[v["session"] == WED].iloc[0]["verdict"] == "miss"

    def test_partial_bar_during_session_cannot_attest(self):
        # A mid-session poll returns a PARTIAL bar for that session (observed
        # live on Alpaca) — it must not count as availability of the settled
        # bar. Only the post-close (settle-floor) poll attests.
        log = _log(
            [
                _row(_ts(WED, 17), "alpaca", _ts(WED, 4)),  # partial, mid-session
                _row(_ts(WED, 22), "alpaca", _ts(WED, 4)),  # post-close, settled
            ]
        )
        v = ma.price_session_verdicts(log, "alpaca")
        row = v[v["session"] == WED].iloc[0]
        assert row["verdict"] == "on_time"
        assert row["first_seen_at"] == _ts(WED, 22)  # not the 17:00 partial
        assert row["margin_hours"] == pytest.approx(1.0)

    def test_partial_bar_only_is_uncovered(self):
        # Only a mid-session sighting and nothing after the close: no verdict.
        log = _log([_row(_ts(WED, 17), "alpaca", _ts(WED, 4))])
        v = ma.price_session_verdicts(log, "alpaca")
        assert v[v["session"] == WED].iloc[0]["verdict"] == "uncovered"


# ─── Publisher-side stale polls (monitor predicate reuse) ──────────────────────


class TestStalePolls:
    def test_stale_poll_counted(self):
        # At Wed 23:30 the monitor requires Wed's bar; publisher serves Mon's.
        log = _log([_row(_ts(WED, 23, 30), "alpaca", _ts(MON, 4))])
        assert ma.stale_poll_counts(log)["alpaca"] == 1

    def test_fresh_poll_not_counted(self):
        log = _log([_row(_ts(WED, 23, 30), "alpaca", _ts(WED, 4))])
        counts = ma.stale_poll_counts(log)
        assert counts["alpaca"] == 0
        assert all(v == 0 for v in counts.values())


# ─── FRED observed lag ─────────────────────────────────────────────────────────


class TestFredLag:
    def test_lag_within_allowance(self):
        log = _log(
            [
                _row(_ts(WED, 12), "fred:DGS10", _ts(TUE, 0)),  # Tue obs seen Wed
            ]
        )
        t = ma.fred_lag_table(log)
        row = t.iloc[0]
        assert row["observed_lag_bdays"] == 1
        assert row["allowed_lag_bdays"] == 1 + mf.FRED_GRACE_BDAYS
        assert bool(row["within_allowance"])

    def test_lag_beyond_allowance(self):
        log = _log([_row(_ts(FRI, 12), "fred:DGS10", _ts(MON, 0))])  # Mon obs seen Fri
        row = ma.fred_lag_table(log).iloc[0]
        assert row["observed_lag_bdays"] == 4
        assert not bool(row["within_allowance"])


# ─── Tightening proposal ───────────────────────────────────────────────────────


def _verdicts(
    n_on_time: int, first_seen_hour: int, n_miss: int = 0, source: str = "tiingo"
) -> pd.DataFrame:
    sessions = ma.trading_days(dt.date(2026, 6, 1), dt.date(2026, 7, 10))
    rows = []
    for i in range(n_on_time + n_miss):
        session = sessions[i]
        verdict = "on_time" if i < n_on_time else "miss"
        rows.append(
            {
                "source": source,
                "session": session,
                "deadline": ma.price_deadline(ma._SPEC_BY_NAME[source], session),
                "first_seen_at": _ts(session, first_seen_hour) if verdict == "on_time" else None,
                "verdict": verdict,
                "margin_hours": None,
            }
        )
    return pd.DataFrame(rows)


class TestTightenProposal:
    def test_eligible_proposal_uses_worst_case_plus_buffer(self):
        # Tiingo bars all first seen at T 22:00 UTC (post-settle-floor):
        # worst 22h + 2h buffer = T+1 00:00 — well inside the T+1 12:00 SLA.
        p = ma.tighten_proposal(_verdicts(12, first_seen_hour=22), "tiingo", stale_polls=0)
        assert p is not None
        assert (p.proposed_day_offset, p.proposed_hour_utc) == (1, 0)
        assert p.current_day_offset == mf.TIINGO_DEADLINE_DAY_OFFSET
        assert p.current_hour_utc == mf.TIINGO_DEADLINE_HOUR_UTC

    def test_too_few_sessions(self):
        assert ma.tighten_proposal(_verdicts(5, 22), "tiingo", stale_polls=0) is None

    def test_any_miss_blocks(self):
        assert ma.tighten_proposal(_verdicts(11, 22, n_miss=1), "tiingo", 0) is None

    def test_stale_polls_block(self):
        assert ma.tighten_proposal(_verdicts(12, 22), "tiingo", stale_polls=1) is None

    def test_no_headroom_returns_none(self):
        # Alpaca first seen at the 21:00 settle floor: 21h + 2h = 23:00, which
        # equals the current SLA — the instrument can confirm alpaca but never
        # tighten it (the settle-floor corollary).
        v = _verdicts(12, first_seen_hour=21, source="alpaca")
        assert ma.tighten_proposal(v, "alpaca", stale_polls=0) is None


# ─── Report + CLI ──────────────────────────────────────────────────────────────


class TestReportAndCLI:
    def test_report_on_empty_log(self):
        out = ma.availability_report(ma.load_log(Path("/nonexistent/log.csv")))
        assert "log is empty" in out

    def test_report_renders_all_feed_sections(self):
        log = _log(
            [
                _row(_ts(WED, 22), "alpaca", _ts(WED, 4)),
                _row(_ts(THU, 9), "tiingo", _ts(WED, 0)),
                _row(_ts(WED, 22), "fred:DGS10", _ts(TUE, 0)),
                _row(_ts(WED, 22), "edgar", _ts(MON, 0)),
                _row(_ts(WED, 22), "rss", _ts(WED, 20)),
                _row(_ts(WED, 22), "fred:DFF", None, status="error"),
            ]
        )
        out = ma.availability_report(log)
        for marker in ("[alpaca]", "[tiingo]", "[fred:DGS10]", "[edgar]", "[rss]"):
            assert marker in out
        assert "verdict-eligible: False" in out
        assert "probe errors in log: 1" in out

    def test_cli_probe_appends_and_reports_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ):
        def fake_probes():
            return {
                "alpaca": lambda: _ts(WED, 4),
                "tiingo": lambda: (_ for _ in ()).throw(TimeoutError("boom")),
            }

        monkeypatch.setattr(ma, "build_probes", fake_probes)
        path = tmp_path / "log.csv"
        rc = ma.main(["--log", str(path)])
        assert rc == 0  # one probe failing is data, not a systemic failure
        log = ma.load_log(path)
        assert set(log["source"]) == {"alpaca", "tiingo"}
        assert (log["status"] == "error").sum() == 1
        assert "probe error: tiingo" in capsys.readouterr().err

    def test_cli_all_probes_failing_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            ma,
            "build_probes",
            lambda: {"alpaca": lambda: (_ for _ in ()).throw(OSError("net down"))},
        )
        assert ma.main(["--log", str(tmp_path / "log.csv")]) == 1

    def test_cli_report_mode(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        path = tmp_path / "log.csv"
        ma.append_records(
            [ma.ProbeRecord(_ts(WED, 22), "alpaca", _ts(WED, 4), "ok", "d")], path
        )
        assert ma.main(["--report", "--log", str(path)]) == 0
        assert "Availability measurement report" in capsys.readouterr().out
