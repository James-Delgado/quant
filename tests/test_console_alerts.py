"""Unit tests for the E4-M2 alerting layer (``quant.console.alerts``).

Every producer is exercised on synthetic view-models / series — no lake, no
network — including the PRD §4 M2 acceptance case: a seeded regime transition
raises a regime-change alert, and a breach reaches the pinned channel
(stderr + non-zero CLI exit). Coverage target ≥80% (METHODOLOGY §15/§16).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import io

import pandas as pd
import pytest
from test_console import make_sources

from quant.console import alerts, readers
from quant.console import viewmodels as vm
from quant.console.sources import ConsoleSources
from quant.utils.calendar import trading_days

# NYSE sessions used to build calendar-correct gap / VIX fixtures. The gap
# producers are pure over the report's own window, so the absolute dates are
# arbitrary — only their session arithmetic matters.
_SESSIONS = trading_days(dt.date(2026, 4, 1), dt.date(2026, 8, 14))


def _sla(feed: str, state: str, detail: str = "d") -> vm.SlaFeedStatus:
    return vm.SlaFeedStatus(feed=feed, state=state, latest=None, required_date=None, detail=detail)


def _gap_report(gap_dates: list[dt.date], n_gaps: int | None = None) -> vm.LakeGapReport:
    return vm.LakeGapReport(
        feed="Daily equity bars",
        window_start=_SESSIONS[0].isoformat(),
        window_end=_SESSIONS[-1].isoformat(),
        n_gaps=len(gap_dates) if n_gaps is None else n_gaps,
        gap_dates=[d.isoformat() for d in gap_dates],
    )


def _feature(name: str, stability: str | None) -> vm.FeatureCard:
    return vm.FeatureCard(
        name=name,
        group="price",
        source="s",
        formula="f",
        point_in_time_rule="r",
        lookback_bars=1,
        publication_lag_days=0,
        ablation_status="untested",
        oos_status="none",
        glossary_ref="g",
        stability=stability,
    )


def _vix(values: list[float]) -> pd.Series:
    idx = pd.DatetimeIndex([pd.Timestamp(s) for s in _SESSIONS[-len(values) :]])
    return pd.Series(values, index=idx, name="VIXCLS")


# ── staleness ────────────────────────────────────────────────────────────────


def test_staleness_fresh_feeds_raise_nothing():
    assert alerts.staleness_alerts([_sla("tiingo", "fresh")]) == []


def test_staleness_stale_is_warning_missing_is_critical():
    out = alerts.staleness_alerts(
        [
            _sla("tiingo", "stale", "latest 2026-08-12 < required 2026-08-13"),
            _sla("edgar", "missing", "no observation in lake"),
        ]
    )
    assert [(a.kind, a.severity, a.subject) for a in out] == [
        ("staleness", "warning", "tiingo"),
        ("staleness", "critical", "edgar"),
    ]
    assert "latest 2026-08-12" in out[0].message


def test_staleness_unknown_state_never_invents_an_alert():
    assert alerts.staleness_alerts([_sla("tiingo", "weird")]) == []


# ── gaps ─────────────────────────────────────────────────────────────────────


def test_gap_unchecked_dataset_notes_not_alerts():
    report = vm.LakeGapReport(
        feed="Tiingo adjusted EOD",
        window_start=None,
        window_end=None,
        n_gaps=None,
        gap_dates=[],
    )
    items, notes = alerts.gap_alerts([report])
    assert items == []
    assert notes == ["Gap check unavailable for Tiingo adjusted EOD — not evaluated."]


def test_gap_zero_gaps_is_quiet():
    items, notes = alerts.gap_alerts([_gap_report([])])
    assert items == [] and notes == []


def test_gap_historical_backlog_does_not_alert():
    # A gap 40 sessions back is outside the 21-session materiality window —
    # it stays a panel / audit-task concern (METHODOLOGY §10 fatigue guard).
    items, notes = alerts.gap_alerts([_gap_report([_SESSIONS[-40]])])
    assert items == [] and notes == []


def test_gap_recent_gap_is_warning():
    # Inside the 21-session window but older than the 5-session critical week.
    items, _ = alerts.gap_alerts([_gap_report([_SESSIONS[-10]])])
    assert [(a.kind, a.severity) for a in items] == [("gap", "warning")]
    assert _SESSIONS[-10].isoformat() in items[0].message


def test_gap_current_week_gap_is_critical():
    items, _ = alerts.gap_alerts([_gap_report([_SESSIONS[-3]])])
    assert [(a.kind, a.severity) for a in items] == [("gap", "critical")]


def test_gap_mixed_recent_and_historical_counts_only_recent():
    items, _ = alerts.gap_alerts([_gap_report([_SESSIONS[-40], _SESSIONS[-10]])])
    assert len(items) == 1
    assert "1 missing session(s)" in items[0].message


# ── drift ────────────────────────────────────────────────────────────────────


def test_drift_unmonitored_catalog_notes_not_alerts():
    item, notes = alerts.drift_alert([_feature("ret_1d", None)])
    assert item is None
    assert notes == ["Feature monitor not wired — drift not evaluated."]


def test_drift_all_stable_is_quiet():
    item, notes = alerts.drift_alert([_feature("a", "stable"), _feature("b", "stale")])
    assert item is None and notes == []


def test_drift_minority_drifting_is_one_warning():
    features = [_feature(f"f{i}", "stable") for i in range(9)]
    features.append(_feature("vix_close", "drifting"))
    item, _ = alerts.drift_alert(features)
    assert item is not None
    assert (item.kind, item.severity) == ("drift", "warning")
    assert "1 of 10 monitored features drifting: vix_close" in item.message


def test_drift_systemic_fraction_escalates_to_critical():
    features = [
        _feature("a", "drifting"),
        _feature("b", "drifting"),
        _feature("c", "stable"),
        _feature("d", "stable"),
    ]
    item, _ = alerts.drift_alert(features)
    assert item is not None and item.severity == "critical"  # 2/4 ≥ 0.25


def test_drift_listed_names_capped_count_exact():
    features = [_feature(f"f{i:02d}", "drifting") for i in range(12)]
    item, _ = alerts.drift_alert(features)
    assert item is not None
    assert "12 of 12" in item.message
    assert item.message.endswith("…")
    listed = item.message.split(": ", 1)[1]
    assert listed.count(",") == alerts.MAX_DRIFT_FEATURES_LISTED  # 10 names + "…"


# ── regime change ────────────────────────────────────────────────────────────


def test_regime_missing_or_short_series_notes_not_alerts():
    for series in (None, _vix([20.0])):
        item, notes = alerts.regime_change_alert(series)
        assert item is None
        assert notes == ["VIX series unavailable — regime-change not evaluated."]


def test_regime_no_transition_is_quiet():
    item, notes = alerts.regime_change_alert(_vix([20.0, 21.0]))
    assert item is None and notes == []


def test_regime_seeded_transition_into_high_vol_is_critical():
    # The PRD §4 M2 acceptance case: a seeded low→high transition alerts.
    item, _ = alerts.regime_change_alert(_vix([14.0, 26.0]))
    assert item is not None
    assert (item.kind, item.severity) == ("regime_change", "critical")
    assert "low_vol → high_vol" in item.message
    assert "14.0" in item.message and "26.0" in item.message


def test_regime_transition_out_of_high_vol_is_warning():
    item, _ = alerts.regime_change_alert(_vix([26.0, 20.0]))
    assert item is not None and item.severity == "warning"
    assert "high_vol → mid_vol" in item.message


def test_regime_uses_only_last_two_observations():
    # An old transition (14→26) followed by two stable sessions is NOT alive.
    item, notes = alerts.regime_change_alert(_vix([14.0, 26.0, 27.0, 28.0]))
    assert item is None and notes == []


# ── evaluate_alerts (composition) ────────────────────────────────────────────


def _data_status(sla=(), gaps=()) -> vm.DataStatusView:
    return vm.DataStatusView(asof="2026-08-14", feeds=[], sla=list(sla), gaps=list(gaps))


def _catalog(features=()) -> vm.CatalogView:
    return vm.CatalogView(
        summary=vm.CatalogSummary(
            registered=len(features), stable=0, drifting=0, stale=0, mean_coverage=None
        ),
        features=list(features),
    )


def test_evaluate_alerts_sorts_critical_first_and_notes_degrades():
    ds = _data_status(
        sla=[_sla("tiingo", "stale"), _sla("edgar", "missing")],
        gaps=[_gap_report([_SESSIONS[-10]])],
    )
    items, notes = alerts.evaluate_alerts(ds, _catalog([_feature("a", None)]), None)
    severities = [a.severity for a in items]
    assert severities == sorted(severities, key=lambda s: 0 if s == "critical" else 1)
    assert {a.kind for a in items} == {"staleness", "gap"}
    assert "Feature monitor not wired — drift not evaluated." in notes
    assert "VIX series unavailable — regime-change not evaluated." in notes


def test_evaluate_alerts_empty_sla_carries_honest_note():
    items, notes = alerts.evaluate_alerts(_data_status(), _catalog(), _vix([20.0, 21.0]))
    assert items == []
    assert "SLA verdicts unavailable — staleness not evaluated." in notes


# ── channel + report ─────────────────────────────────────────────────────────


def test_format_report_no_alerts():
    view = vm.AlertsView(asof="2026-08-14", alerts=[], notes=["a note"])
    report = alerts.format_report(view)
    assert "NO ALERTS" in report and "a note" in report


def test_log_channel_emits_to_stream():
    view = vm.AlertsView(
        asof="2026-08-14",
        alerts=[vm.AlertItem("gap", "critical", "Daily equity bars", "1 missing")],
    )
    stream = io.StringIO()
    alerts.LogChannel(stream).emit(view)
    out = stream.getvalue()
    assert "1 ALERT(S) (1 critical)" in out
    assert "CRITICAL" in out and "Daily equity bars" in out


# ── reader + CLI (the chosen channel end-to-end) ─────────────────────────────


@pytest.fixture
def sources(tmp_path) -> ConsoleSources:
    return make_sources(tmp_path)


def test_load_alerts_seeded_regime_transition_raises_alert(sources):
    src = dataclasses.replace(
        sources,
        market_series_fn=lambda sid: _vix([14.0, 26.0]) if sid == "VIXCLS" else None,
    )
    view = readers.load_alerts(src)
    regime = [a for a in view.alerts if a.kind == "regime_change"]
    assert len(regime) == 1 and regime[0].severity == "critical"
    # The fixture monitor marks DGS10 drifting (1 of 2 ≥ 0.25 → critical).
    drift = [a for a in view.alerts if a.kind == "drift"]
    assert len(drift) == 1 and drift[0].severity == "critical"
    # Unconfigured SLA / gap seams degrade to notes, never silence (§9).
    assert any("SLA verdicts unavailable" in n for n in view.notes)
    assert any("Gap check unavailable" in n for n in view.notes)


def test_load_alerts_market_series_failure_degrades(sources):
    def broken(series_id: str):
        raise RuntimeError("boom")

    view = readers.load_alerts(dataclasses.replace(sources, market_series_fn=broken))
    assert any("regime-change not evaluated" in n for n in view.notes)


def test_export_includes_schema_valid_alerts_payload(sources):
    from quant.console import export, schemas

    payload = export.build_export(sources)["alerts.json"]  # already jsonable
    assert schemas.validate(payload, schemas.EXPORT_SCHEMAS["alerts.json"]) == []
    assert payload["asof"] == "2026-06-28"  # the fixture's fixed now()


def test_cli_alerts_exits_nonzero_and_emits_on_breach(monkeypatch, sources, capsys):
    from quant.console import __main__ as cli

    src = dataclasses.replace(
        sources,
        market_series_fn=lambda sid: _vix([14.0, 26.0]) if sid == "VIXCLS" else None,
    )
    monkeypatch.setattr(ConsoleSources, "default", classmethod(lambda cls, **kw: src))
    rc = cli.main(["alerts"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "regime_change" in captured.out
    # The pinned channel: the breach is echoed to stderr for cron mail-on-output.
    assert "regime_change" in captured.err


def test_cli_alerts_exits_zero_when_quiet(monkeypatch, sources, capsys):
    from quant.console import __main__ as cli

    quiet = dataclasses.replace(
        sources,
        market_series_fn=lambda sid: _vix([20.0, 21.0]) if sid == "VIXCLS" else None,
        feature_monitor_fn=lambda name: {"stability": "stable"},
    )
    monkeypatch.setattr(ConsoleSources, "default", classmethod(lambda cls, **kw: quiet))
    rc = cli.main(["alerts"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "NO ALERTS" in captured.out
    assert captured.err == ""


def test_cli_alerts_no_monitor_flag_reaches_sources(monkeypatch, sources):
    from quant.console import __main__ as cli

    captured = {}

    def fake_default(cls, *, feature_monitor=True):
        captured["feature_monitor"] = feature_monitor
        return dataclasses.replace(
            sources,
            market_series_fn=lambda sid: _vix([20.0, 21.0]),
            feature_monitor_fn=None,
        )

    monkeypatch.setattr(ConsoleSources, "default", classmethod(fake_default))
    assert cli.main(["alerts", "--no-monitor"]) == 0
    assert captured["feature_monitor"] is False


def test_cli_alerts_now_override_stamps_asof(monkeypatch, sources, capsys):
    from quant.console import __main__ as cli

    quiet = dataclasses.replace(
        sources,
        market_series_fn=lambda sid: _vix([20.0, 21.0]),
        feature_monitor_fn=lambda name: {"stability": "stable"},
    )
    monkeypatch.setattr(ConsoleSources, "default", classmethod(lambda cls, **kw: quiet))
    assert cli.main(["alerts", "--now", "2026-08-01T12:00:00"]) == 0
    assert "Alerts @ 2026-08-01" in capsys.readouterr().out
