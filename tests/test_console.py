"""Unit tests for the console service layer (E1-M1).

Every test runs on synthetic fixtures written to ``tmp_path`` — no dependency on
the real (gitignored) ``data/`` tree, so the suite is CI-safe. Coverage target
is ≥80% on ``src/quant/console`` (METHODOLOGY §15/§16).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from quant.console import export, readers, schemas
from quant.console import viewmodels as vm
from quant.console.sources import ConsoleSources, FeedSpec, read_oos_returns

# 40-hex git-sha-like strings (link-eligible) and a 64-hex content hash (not).
_GIT_SHA_A = "a" * 40
_CONTENT_HASH = "c" * 64


def _returns(seed: int, start: str = "2006-01-01", periods: int = 4500) -> pd.Series:
    """Synthetic daily returns on a tz-aware (NY) business-day index."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start=start, periods=periods, freq="B", tz="America/New_York")
    return pd.Series(rng.normal(0.0003, 0.01, size=periods), index=idx, name="oos_returns")


def _write_checkpoint(
    root: Path,
    arm: str,
    *,
    seed: int,
    config_hash: str,
    git_sha: str = _GIT_SHA_A,
    smoke: bool = False,
    sharpe: float = 0.4,
) -> None:
    arm_dir = root / "phase4a" / arm
    arm_dir.mkdir(parents=True, exist_ok=True)
    returns = _returns(seed)
    returns.to_frame().to_parquet(arm_dir / "oos_returns.parquet")
    meta = {
        "arm": arm,
        "smoke": smoke,
        "git_sha": git_sha,
        "config_hash": config_hash,
        "started_at": "2026-06-13T18:14:19.566111+00:00",
        "finished_at": "2026-06-13T18:31:02.567072+00:00",
        "n_symbols_in_panel": 33,
        "symbols": ["AAPL", "MSFT"],
        "n_oos_bars": len(returns),
        "n_folds": 87,
        "oos_start": "2006-01-02 20:00:00-04:00",
        "oos_end": "2023-12-29 20:00:00-04:00",
        "aggregate_sharpe": sharpe,
        "aggregate_max_dd": -0.5,
        "run_config": {
            "arm": arm,
            "label_horizon": 1,
            "feature_columns": ["ret_1d", "DGS10", "sentiment_score", "xs_rank_vol_21d"],
            "walk_forward": {"train_window": 504, "test_window": 63, "step": 63, "embargo": 3},
            "sim_kwargs": {
                "initial_capital": 100000.0,
                "commission_per_share": 0.005,
                "slippage_bps": 5.0,
            },
            "model_params": {"type": "ARIMABaseline", "order": [1, 0, 0]},
        },
    }
    (arm_dir / "metadata.json").write_text(json.dumps(meta))


def _write_ledger(path: Path) -> None:
    entries = [
        {
            "id": "ledger-2026-06-13-0001",
            "prd": "phase-4a",
            "milestone": "M6",
            "agent": "human",
            "preregistration": "docs/PHASE_4A_REPORT.md",
            "config_hash": _GIT_SHA_A,  # 40-hex → link-eligible
            "n_comparisons": 4,
            "started_at": "2026-06-13T18:14:19Z",
            "completed_at": "2026-06-13T18:31:02Z",
            "verdict": "inconclusive",
            "artifacts": ["data/phase4a/arima/"],
            "notes": "control arm",
        },
        {
            "id": "ledger-2026-06-13-0002",
            "prd": "phase-4a",
            "milestone": "M6",
            "agent": "human",
            "preregistration": "docs/PHASE_4A_REPORT.md",
            "config_hash": _CONTENT_HASH,  # 64-hex → not a git sha
            "n_comparisons": 3,
            "started_at": "2026-06-13T18:32:10Z",
            "completed_at": "2026-06-13T18:56:17Z",
            "verdict": "gate_failed",
            "artifacts": ["data/phase4a/signed/"],
            "notes": "gbm arm",
        },
    ]
    path.write_text(yaml.safe_dump(entries))


def _write_catalog(path: Path) -> None:
    catalog = {
        "features": [
            {
                "name": "ret_1d",
                "family": "price",
                "source": "alpaca_ohlcv",
                "formula": "close.pct_change(1)",
                "lookback_bars": 1,
                "publication_lag_days": 0,
                "point_in_time_rule": "uses only closes <= t",
                "added_phase": "2",
                "glossary_ref": "docs/concepts/feature-glossary.md#ret_1d",
                "ablation_status": "untested",
                "attribution_status": "both",
                "regime_notes": None,
                "depends_on": [],
            },
            {
                "name": "DGS10",
                "family": "macro",
                "source": "fred",
                "formula": "fred(DGS10)",
                "lookback_bars": 0,
                "publication_lag_days": 1,
                "point_in_time_rule": "lagged 1 business day",
                "added_phase": "2",
                "glossary_ref": "docs/concepts/feature-glossary.md#dgs10",
                "ablation_status": "tested_no_edge",
                "attribution_status": "none",
                "regime_notes": None,
                "depends_on": [],
            },
        ]
    }
    path.write_text(yaml.safe_dump(catalog))


@pytest.fixture
def sources(tmp_path: Path) -> ConsoleSources:
    data_root = tmp_path / "data"
    data_root.mkdir()
    _write_checkpoint(data_root, "arima", seed=1, config_hash=_GIT_SHA_A, sharpe=0.42)
    _write_checkpoint(data_root, "signed", seed=2, config_hash=_CONTENT_HASH, sharpe=-0.33)
    _write_checkpoint(
        data_root, "smoke_arima", seed=3, config_hash="d" * 40, smoke=True
    )  # excluded
    ledger_path = data_root / "ledger.yaml"
    _write_ledger(ledger_path)
    catalog_path = tmp_path / "catalog.yaml"
    _write_catalog(catalog_path)

    fixed_now = dt.datetime(2026, 6, 28, tzinfo=dt.timezone.utc)
    feed_ages = {
        "equity_bars_daily": dt.datetime(2026, 6, 27, tzinfo=dt.timezone.utc),  # fresh
        "macro_fred": dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),  # stale
        # text_documents intentionally absent → "missing"
    }

    def fake_latest(dataset: str, ts_col: str = "timestamp") -> dt.datetime | None:
        return feed_ages.get(dataset)

    def fake_market(series_id: str) -> float | None:
        return {"VIXCLS": 15.4, "DGS10": 4.47, "DFF": 3.62}.get(series_id)

    def fake_monitor(name: str) -> dict | None:
        return {
            "ret_1d": {"coverage": 0.99, "mean": 0.0, "std": 0.01, "stability": "stable"},
            "DGS10": {"coverage": 0.80, "mean": 4.0, "std": 0.5, "stability": "drifting"},
        }.get(name)

    return ConsoleSources(
        data_root=data_root,
        ledger_path=ledger_path,
        catalog_path=catalog_path,
        strategy_roots=(data_root / "phase4a",),
        feeds=(
            FeedSpec("equity_bars_daily", "Daily equity bars", "timestamp"),
            FeedSpec("macro_fred", "FRED macro series", "timestamp"),
            FeedSpec("text_documents", "Filings & news", "published_at"),
        ),
        latest_timestamp_fn=fake_latest,
        market_value_fn=fake_market,
        feature_monitor_fn=fake_monitor,
        now_fn=lambda: fixed_now,
    )


# ── read_oos_returns ─────────────────────────────────────────────────────────


def test_read_oos_returns_drops_timezone(tmp_path: Path):
    s = _returns(7, periods=10)
    p = tmp_path / "r.parquet"
    s.to_frame().to_parquet(p)
    out = read_oos_returns(p)
    assert out.index.tz is None
    assert len(out) == 10
    assert out.dtype == float


# ── load_strategies ──────────────────────────────────────────────────────────


def test_load_strategies_sorted_and_excludes_smoke(sources):
    cards = readers.load_strategies(sources)
    assert [c.id for c in cards] == ["arima", "signed"]  # smoke excluded, sorted
    arima = next(c for c in cards if c.id == "arima")
    assert arima.name == "ARIMA(1,0,0) control"
    assert arima.status == "inconclusive"  # joined from ledger by config_hash
    assert arima.mode == "research"
    assert len(arima.sparkline) == readers.SPARKLINE_POINTS
    assert "control arm" in arima.driver.lower()


def test_load_strategies_verdict_from_ledger(sources):
    signed = next(c for c in readers.load_strategies(sources) if c.id == "signed")
    assert signed.status == "gate_failed"
    assert "gate failed" in signed.driver.lower()


# ── load_strategy ────────────────────────────────────────────────────────────


def test_load_strategy_detail(sources):
    detail = readers.load_strategy("arima", sources)
    assert detail is not None
    assert detail.figures["n_symbols"] == 33
    assert detail.figures["n_folds"] == 87
    assert len(detail.equity) <= readers.SERIES_POINTS
    assert detail.equity[0].date <= detail.equity[-1].date
    assert len(detail.return_hist.counts) == len(detail.return_hist.bin_edges) - 1
    assert detail.commit_url.endswith(_GIT_SHA_A)
    assert detail.condition_link == "/conditions"


def test_load_strategy_unknown_returns_none(sources):
    assert readers.load_strategy("does_not_exist", sources) is None


def test_calmar_none_when_no_drawdown():
    flat = pd.Series([0.01, 0.01, 0.01])  # monotonic up → max_drawdown == 0
    metrics = readers._strategy_metrics(flat)
    assert metrics.calmar is None


# ── load_conditions ──────────────────────────────────────────────────────────


def test_load_conditions_shape(sources):
    cond = readers.load_conditions(sources)
    assert [a.name for a in cond.axes] == ["volatility", "trend"]
    assert len(cond.by_condition) == 5  # 3 vol + 2 trend
    assert cond.heatmap.strategies == ["arima", "signed"]
    assert cond.heatmap.conditions == ["low_vol", "mid_vol", "high_vol", "uptrend", "downtrend"]
    assert len(cond.heatmap.values) == 2
    assert all(len(row) == 5 for row in cond.heatmap.values)
    names = {w.name for w in cond.stress_windows}
    assert "COVID crash" in names


def test_conditions_empty_when_no_strategies(tmp_path):
    empty = ConsoleSources(
        data_root=tmp_path,
        ledger_path=tmp_path / "ledger.yaml",
        catalog_path=tmp_path / "catalog.yaml",
        strategy_roots=(tmp_path / "phase4a",),
    )
    cond = readers.load_conditions(empty)
    assert cond.heatmap.strategies == []
    assert all(w.sharpe is None for w in cond.stress_windows)


# ── load_provenance ──────────────────────────────────────────────────────────


def test_load_provenance(sources):
    prov = readers.load_provenance("arima", sources)
    assert prov is not None
    assert prov.config.model == "ARIMABaseline"
    assert prov.config.train_window == 504
    assert len(prov.leakage_controls) == 6
    assert all(c.status == "enforced" for c in prov.leakage_controls)
    assert len(prov.self_tests) == 2
    assert "FRED macro series (publication-lag corrected)" in prov.lineage
    assert "SEC EDGAR + RSS → FinBERT sentiment" in prov.lineage


def test_load_provenance_unknown_returns_none(sources):
    assert readers.load_provenance("nope", sources) is None


# ── load_catalog ─────────────────────────────────────────────────────────────


def test_load_catalog_with_monitor(sources):
    cat = readers.load_catalog(sources)
    assert cat.summary.registered == 2
    assert cat.summary.stable == 1
    assert cat.summary.drifting == 1
    assert cat.summary.mean_coverage == pytest.approx((0.99 + 0.80) / 2)
    by_name = {f.name: f for f in cat.features}
    assert by_name["ret_1d"].oos_status == "both"
    assert by_name["DGS10"].stability == "drifting"


def test_load_catalog_without_monitor(sources):
    bare = ConsoleSources(
        data_root=sources.data_root,
        ledger_path=sources.ledger_path,
        catalog_path=sources.catalog_path,
        strategy_roots=sources.strategy_roots,
    )
    cat = readers.load_catalog(bare)
    assert cat.summary.mean_coverage is None
    assert all(f.coverage is None for f in cat.features)


# ── load_ledger ──────────────────────────────────────────────────────────────


def test_load_ledger(sources):
    led = readers.load_ledger(sources)
    assert led.n_entries == 2
    assert led.n_trials == 7  # 4 + 3
    assert led.luck_bar == pytest.approx(readers.expected_max_sharpe(7))
    assert led.best == pytest.approx(0.42)  # max checkpoint aggregate_sharpe
    by_id = {r.id: r for r in led.runs}
    assert by_id["ledger-2026-06-13-0001"].commit_url.endswith(_GIT_SHA_A)
    # 64-hex content hash is not link-eligible
    assert by_id["ledger-2026-06-13-0002"].commit_url is None


# ── data_status ──────────────────────────────────────────────────────────────


def test_data_status(sources):
    ds = readers.data_status(sources)
    assert ds.asof == "2026-06-28"
    by_feed = {f.feed: f for f in ds.feeds}
    assert by_feed["Daily equity bars"].status == "fresh"
    assert by_feed["FRED macro series"].status == "stale"
    assert by_feed["Filings & news"].status == "missing"
    assert by_feed["Filings & news"].last_timestamp is None


# ── market_snapshot ──────────────────────────────────────────────────────────


def test_market_snapshot(sources):
    mk = readers.market_snapshot(sources)
    assert mk.vix == 15.4
    assert mk.ten_year == 4.47
    assert mk.fed_funds == 3.62
    assert any("2s10s" in n for n in mk.notes)


def test_market_snapshot_no_source(sources):
    bare = ConsoleSources(
        data_root=sources.data_root,
        ledger_path=sources.ledger_path,
        catalog_path=sources.catalog_path,
        strategy_roots=sources.strategy_roots,
    )
    mk = readers.market_snapshot(bare)
    assert mk.vix is None
    assert any("not configured" in n for n in mk.notes)


# ── export ───────────────────────────────────────────────────────────────────


def test_build_export_validates_against_schemas(sources):
    exp = export.build_export(sources)
    problems = export.validate_export(exp)
    assert problems == {}, problems
    assert "strategies.json" in exp
    assert "strategy/arima.json" in exp
    assert "provenance/arima.json" in exp


def test_export_idempotent(sources, tmp_path):
    out1, out2 = tmp_path / "o1", tmp_path / "o2"
    export.write_export(out1, sources)
    export.write_export(out2, sources)
    files1 = sorted(p.relative_to(out1) for p in out1.rglob("*.json"))
    files2 = sorted(p.relative_to(out2) for p in out2.rglob("*.json"))
    # 6 top-level + 2 strategy detail + 2 provenance (2 strategies in fixture).
    assert files1 == files2 and len(files1) == 10
    for rel in files1:
        assert (out1 / rel).read_bytes() == (out2 / rel).read_bytes()


def test_export_rejects_invalid_payload(sources, tmp_path, monkeypatch):
    # A schema-violating payload must make write_export fail fast.
    bad = {"strategies.json": "not-an-array"}
    monkeypatch.setattr(export, "build_export", lambda s=None: bad)
    with pytest.raises(ValueError, match="schema validation"):
        export.write_export(tmp_path / "bad", sources)


def test_validate_export_flags_unregistered_path():
    problems = export.validate_export({"mystery.json": {}})
    assert "mystery.json" in problems


# ── schema validator ─────────────────────────────────────────────────────────


def test_validator_flags_missing_key_and_wrong_type():
    schema = schemas.schema_for(vm.FeedStatus)
    errors = schemas.validate({"feed": 123, "status": "fresh"}, schema, name="f")
    assert any("expected string" in e for e in errors)
    assert any("required key missing" in e for e in errors)


def test_validator_accepts_nullable():
    schema = schemas.schema_for(vm.FeedStatus)
    ok = {"feed": "x", "last_timestamp": None, "age_days": None, "status": "missing"}
    assert schemas.validate(ok, schema, name="f") == []


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_cli_export(monkeypatch, sources, tmp_path, capsys):
    from quant.console import __main__ as cli

    monkeypatch.setattr(ConsoleSources, "default", classmethod(lambda cls: sources))
    rc = cli.main(["export", "--out", str(tmp_path / "cli")])
    assert rc == 0
    assert "Wrote 10 export files" in capsys.readouterr().out


# ── production sources wiring ─────────────────────────────────────────────────


def test_default_sources_constructs():
    src = ConsoleSources.default()
    assert src.repo_url.endswith("/quant")
    assert src.commit_url("abc123").endswith("/commit/abc123")
    assert src.commit_url(None) is None
    assert src.now().tzinfo is not None
    assert src.strategy_roots[0].name == "phase4a"
