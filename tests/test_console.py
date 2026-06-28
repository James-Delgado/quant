"""Unit tests for the console service layer (E1-M1).

Every test runs on synthetic fixtures written to ``tmp_path`` — no dependency on
the real (gitignored) ``data/`` tree, so the suite is CI-safe. Coverage target
is ≥80% on ``src/quant/console`` (METHODOLOGY §15/§16).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from quant.console import export, readers, schemas
from quant.console import sources as sources_mod
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


# Synthetic market series matching the OOS calendar (naive business days — the
# date set the NY-close return index normalises onto). VIX spans the 15/25
# thresholds and the 10-year wanders enough to exercise every rates bucket.
_OOS_DATES = pd.date_range("2006-01-01", periods=4500, freq="B")


def _synthetic_market() -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(2024)
    n = len(_OOS_DATES)
    vix = 20.0 + 7.0 * np.sin(np.linspace(0, 24 * np.pi, n)) + rng.normal(0, 2.0, n)
    vix = np.clip(vix, 9.0, 60.0)
    dgs10 = np.clip(3.0 + np.cumsum(rng.normal(0, 0.03, n)), 0.5, 7.0)
    return (
        pd.Series(vix, index=_OOS_DATES, name="VIXCLS"),
        pd.Series(dgs10, index=_OOS_DATES, name="DGS10"),
    )


_VIX_SERIES, _DGS10_SERIES = _synthetic_market()


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


def _write_registry(path: Path) -> None:
    """Synthetic C6 strategy registry: one enabled (in-use) + one idle entry.

    Exercises both ``status`` branches and the 1/N equal-weight allocation
    (one enabled → 100%). Refs are not resolved by ``load_portfolio`` (it never
    calls the G1 drift gate), so the values only need to satisfy the StrategySpec
    schema.
    """
    registry = {
        "strategies": [
            {
                "id": "arima_placeholder",
                "display_name": "ARIMA(1,0,0) Placeholder",
                "description": "Infrastructure placeholder. Makes no edge claim.",
                "model_ref": "arima_baseline",
                "feature_set_ref": [],
                "target_ref": "next_bar_return",
                "universe": ["SPY", "QQQ", "IWM"],
                "decision_rule": "sign",
                "cadence": "daily",
                "broker": "alpaca_paper",
                "enabled": True,
                "provenance": "placeholder",
                "created_at": "2026-06-28T16:57:08Z",
                "enabled_at": "2026-06-28T16:57:08Z",
            },
            {
                "id": "gbm_idle",
                "display_name": "GBM (idle)",
                "description": "Disabled GBM strategy, not yet deployed.",
                "model_ref": "gbm",
                "feature_set_ref": ["ret_1d"],
                "target_ref": "next_bar_return",
                "universe": ["SPY"],
                "decision_rule": "sign",
                "cadence": "daily",
                "broker": "alpaca_paper",
                "enabled": False,
                "provenance": "",
                "created_at": "2026-06-28T16:57:08Z",
            },
        ]
    }
    path.write_text(yaml.safe_dump(registry))


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
    registry_path = tmp_path / "strategy_registry.yaml"
    _write_registry(registry_path)

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

    def fake_market_series(series_id: str) -> pd.Series | None:
        return {"VIXCLS": _VIX_SERIES, "DGS10": _DGS10_SERIES}.get(series_id)

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
        registry_path=registry_path,
        feeds=(
            FeedSpec("equity_bars_daily", "Daily equity bars", "timestamp"),
            FeedSpec("macro_fred", "FRED macro series", "timestamp"),
            FeedSpec("text_documents", "Filings & news", "published_at"),
        ),
        latest_timestamp_fn=fake_latest,
        market_value_fn=fake_market,
        market_series_fn=fake_market_series,
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


# ── load_portfolio (C6 registry) ─────────────────────────────────────────────


def test_load_portfolio_status_and_allocation(sources):
    view = readers.load_portfolio(sources)
    assert view.n_enabled == 1 and view.n_idle == 1
    by_id = {s.id: s for s in view.strategies}

    enabled = by_id["arima_placeholder"]
    assert enabled.status == "enabled"
    assert enabled.allocation_pct == 100.0  # 1/N with one enabled strategy
    assert enabled.universe == ["SPY", "QQQ", "IWM"]
    assert enabled.model_ref == "arima_baseline"
    assert "Placeholder" in enabled.provenance_summary

    idle = by_id["gbm_idle"]
    assert idle.status == "idle"
    assert idle.allocation_pct == 0.0  # idle strategies hold no capital


def test_load_portfolio_falls_back_to_default_registry(tmp_path):
    """A None registry_path resolves the committed default (production wiring)."""
    bare = ConsoleSources(
        data_root=tmp_path,
        ledger_path=tmp_path / "ledger.yaml",
        catalog_path=tmp_path / "catalog.yaml",
        strategy_roots=(tmp_path / "phase4a",),
        registry_path=None,
    )
    view = readers.load_portfolio(bare)
    # The seeded real registry has at least the enabled ARIMA placeholder.
    assert any(s.status == "enabled" for s in view.strategies)
    assert view.n_enabled + view.n_idle == len(view.strategies)


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
    # Market-level axes: VIX volatility + 10-year rates (the equity-trend proxy
    # is retired — E1-M1-CONDITIONS-MARKET-AXIS).
    assert [a.name for a in cond.axes] == ["volatility", "rates"]
    assert len(cond.by_condition) == 6  # 3 vol + 3 rates
    assert cond.heatmap.strategies == ["arima", "signed"]
    assert cond.heatmap.conditions == [
        "low_vol",
        "mid_vol",
        "high_vol",
        "rates_falling",
        "rates_steady",
        "rates_rising",
    ]
    assert len(cond.heatmap.values) == 2
    assert all(len(row) == 6 for row in cond.heatmap.values)
    names = {w.name for w in cond.stress_windows}
    assert "COVID crash" in names
    # Both market axes surface at least one populated bucket on the fixture.
    populated = {c.condition for c in cond.by_condition if c.n_bars > 0}
    assert populated & set(readers._VOL_CONDITIONS)
    assert populated & set(readers._RATES_CONDITIONS)


def test_vol_labels_reuse_vix_thresholds():
    dates = pd.date_range("2020-01-01", periods=5, freq="D")
    vix = pd.Series([10.0, 15.0, 20.0, 25.0, 40.0], index=dates)
    labels = readers._vol_labels(vix)
    # VIXThresholdDetector: <=15 → low, >=25 → high, else mid (boundaries inclusive).
    assert list(labels) == ["low_vol", "low_vol", "mid_vol", "high_vol", "high_vol"]


def test_rates_labels_classify_direction():
    n = readers.RATES_CHANGE_WINDOW
    dates = pd.date_range("2020-01-01", periods=n + 3, freq="B")
    rising = pd.Series(np.linspace(2.0, 5.0, n + 3), index=dates)
    assert set(readers._rates_labels(rising).iloc[-3:]) == {"rates_rising"}
    falling = pd.Series(np.linspace(5.0, 2.0, n + 3), index=dates)
    assert set(readers._rates_labels(falling).iloc[-3:]) == {"rates_falling"}
    flat = pd.Series(np.full(n + 3, 3.0), index=dates)
    assert set(readers._rates_labels(flat)) == {"rates_steady"}


def test_align_market_forward_fills_point_in_time():
    series = pd.Series([1.0, 2.0], index=pd.to_datetime(["2020-01-01", "2020-01-05"]))
    dates = pd.DatetimeIndex(pd.to_datetime(["2020-01-01", "2020-01-03", "2020-01-05", "2020-01-06"]))
    out = readers._align_market(readers._by_date(series), dates)
    assert list(out) == [1.0, 1.0, 2.0, 2.0]  # carries the last prior obs forward


def test_conditions_degrade_without_market_series(sources):
    """No market series → axes omitted (not faked); stress windows still render."""
    bare = dataclasses.replace(sources, market_series_fn=None)
    cond = readers.load_conditions(bare)
    assert cond.axes == []
    assert cond.by_condition == []
    assert cond.heatmap.conditions == []
    assert cond.heatmap.strategies == ["arima", "signed"]
    assert any(w.sharpe is not None for w in cond.stress_windows)


def test_conditions_empty_when_no_strategies(tmp_path):
    empty = ConsoleSources(
        data_root=tmp_path,
        ledger_path=tmp_path / "ledger.yaml",
        catalog_path=tmp_path / "catalog.yaml",
        strategy_roots=(tmp_path / "phase4a",),
    )
    cond = readers.load_conditions(empty)
    assert cond.heatmap.strategies == []
    assert cond.axes == []
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


# ── feature monitor (E1-M1-FEATURE-MONITOR) ──────────────────────────────────


def _feature_panel() -> pd.DataFrame:
    """Synthetic pooled feature panel: 2 symbols × 60 business days, stacked.

    Columns exercise every stability branch with a small ``recent_bars`` so the
    panel stays tiny: ``stable_feat`` (flat-ish), ``drift_feat`` (recent window
    shifted far off baseline), ``stale_feat`` (stops updating before the tail),
    and ``empty_feat`` (all-NaN).
    """
    rng = np.random.default_rng(7)
    dates = pd.date_range("2020-01-01", periods=60, freq="B")
    frames = []
    for _sym in ("AAA", "BBB"):
        stable = rng.normal(0.0, 0.01, size=60)
        drift = rng.normal(0.0, 0.01, size=60)
        drift[-10:] += 5.0  # recent window jumps far off the baseline
        stale = np.full(60, 1.0)
        stale[40:] = np.nan  # last 20 dates missing → behind the tail
        empty = np.full(60, np.nan)
        frames.append(
            pd.DataFrame(
                {
                    "stable_feat": stable,
                    "drift_feat": drift,
                    "stale_feat": stale,
                    "empty_feat": empty,
                },
                index=dates,
            )
        )
    return pd.concat(frames, axis=0).sort_index()


def _monitor(panel_fn):
    return sources_mod.build_feature_monitor(
        panel_fn, hist_bins=5, recent_bars=10, drift_z_threshold=1.0, stale_bars=5
    )


def test_feature_monitor_classifies_each_stability_branch():
    monitor = _monitor(_feature_panel)
    assert monitor("stable_feat")["stability"] == "stable"
    assert monitor("drift_feat")["stability"] == "drifting"
    assert monitor("stale_feat")["stability"] == "stale"

    empty = monitor("empty_feat")
    assert empty["stability"] == "stale"
    assert empty["coverage"] == 0.0
    assert empty["mean"] is None and empty["std"] is None
    assert empty["distribution"] is None


def test_feature_monitor_reports_coverage_mean_std_distribution():
    stats = _monitor(_feature_panel)("stable_feat")
    assert stats["coverage"] == pytest.approx(1.0)  # dense column
    assert stats["mean"] == pytest.approx(0.0, abs=0.02)
    assert stats["std"] > 0.0
    assert len(stats["distribution"]) == 5  # hist_bins
    assert sum(stats["distribution"]) == 120  # 2 symbols × 60 rows

    # A column that stops updating still reports partial coverage honestly.
    stale = _monitor(_feature_panel)("stale_feat")
    assert stale["coverage"] == pytest.approx(40 / 60)


def test_feature_monitor_unmonitored_feature_returns_none():
    assert _monitor(_feature_panel)("not_in_panel") is None


def test_feature_monitor_memoizes_panel_build():
    calls = {"n": 0}

    def counting_panel():
        calls["n"] += 1
        return _feature_panel()

    monitor = _monitor(counting_panel)
    monitor("stable_feat")
    monitor("drift_feat")
    monitor("missing")
    assert calls["n"] == 1  # panel built exactly once, then cached


def test_feature_monitor_degrades_when_panel_fails():
    def boom():
        raise RuntimeError("lake unavailable")

    monitor = _monitor(boom)
    assert monitor("stable_feat") is None  # honest degrade, no fabricated stats


def test_feature_monitor_none_panel_is_empty():
    assert sources_mod.build_feature_monitor(lambda: None)("ret_1d") is None
    assert sources_mod.build_feature_monitor(None)("ret_1d") is None


def test_panel_from_features_normalizes_timezone_and_aligns_by_date():
    # NY-close stamps must collapse onto the same calendar date as a naive frame.
    ny = pd.DataFrame(
        {"f": [1.0, 2.0]},
        index=pd.DatetimeIndex(
            ["2020-01-02 16:00", "2020-01-03 16:00"], tz="America/New_York"
        ),
    )
    naive = pd.DataFrame(
        {"f": [3.0, 4.0]},
        index=pd.DatetimeIndex(["2020-01-02", "2020-01-03"]),
    )
    panel = sources_mod._panel_from_features({"X": ny, "Y": naive})
    assert panel.index.tz is None
    assert (panel.index == panel.index.normalize()).all()
    # Both symbols' 2020-01-02 rows landed on the same date key.
    assert (panel.index == pd.Timestamp("2020-01-02")).sum() == 2
    assert sources_mod._panel_from_features({}) is None  # no frames → None


class _FakeStorageCatalog:
    """Minimal stand-in for ``quant.storage.catalog`` (query + table)."""

    def __init__(self, df=None, raises=False):
        self._df = df
        self._raises = raises

    def table(self, name: str) -> str:
        return f"'lake/{name}/*.parquet'"

    def query(self, sql: str) -> pd.DataFrame:
        if self._raises:
            raise RuntimeError("duckdb unavailable")
        return self._df if self._df is not None else pd.DataFrame()


def _eq_rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["AAA", "AAA"],
            "timestamp": pd.to_datetime(["2020-01-02", "2020-01-03"]),
            "open": [1.0, 1.1],
            "high": [1.2, 1.3],
            "low": [0.9, 1.0],
            "adjClose": [1.05, 1.15],
            "volume": [100.0, 110.0],
        }
    )


def test_load_prices_for_panel_renames_and_skips_empty_symbols():
    prices = sources_mod._load_prices_for_panel(
        _FakeStorageCatalog(_eq_rows()), ["AAA", "BBB"]
    )
    assert set(prices) == {"AAA"}  # BBB has no rows → skipped
    assert "close" in prices["AAA"].columns and "adjClose" not in prices["AAA"].columns
    assert len(prices["AAA"]) == 2


def test_load_prices_for_panel_degrades_to_empty():
    assert sources_mod._load_prices_for_panel(_FakeStorageCatalog(), []) == {}
    assert sources_mod._load_prices_for_panel(_FakeStorageCatalog(pd.DataFrame()), ["AAA"]) == {}
    assert sources_mod._load_prices_for_panel(_FakeStorageCatalog(raises=True), ["AAA"]) == {}


def test_load_feature_panel_returns_none_without_lake(monkeypatch):
    # No usable bars → None, without ever touching build_features.
    monkeypatch.setattr(sources_mod, "_load_prices_for_panel", lambda *a, **k: {})
    assert sources_mod._load_feature_panel() is None


def test_feature_monitor_feeds_load_catalog(tmp_path):
    catalog_path = tmp_path / "catalog.yaml"
    _write_catalog(catalog_path)  # registers ret_1d + DGS10
    panel = pd.DataFrame(
        {
            "ret_1d": np.linspace(-0.05, 0.05, 80),
            "DGS10": [np.nan] * 30 + list(np.linspace(3.0, 4.0, 50)),
        },
        index=pd.date_range("2020-01-01", periods=80, freq="B"),
    )
    src = ConsoleSources(
        data_root=tmp_path,
        ledger_path=tmp_path / "ledger.yaml",
        catalog_path=catalog_path,
        strategy_roots=(tmp_path,),
        feature_monitor_fn=sources_mod.build_feature_monitor(
            lambda: panel, recent_bars=10, stale_bars=5
        ),
    )
    cat = readers.load_catalog(src)
    by_name = {f.name: f for f in cat.features}
    assert by_name["ret_1d"].coverage == pytest.approx(1.0)
    assert by_name["DGS10"].coverage == pytest.approx(50 / 80)
    assert cat.summary.mean_coverage == pytest.approx((1.0 + 50 / 80) / 2)
    assert by_name["ret_1d"].distribution is not None


# ── load_ledger ──────────────────────────────────────────────────────────────


def test_load_ledger(sources):
    led = readers.load_ledger(sources)
    assert led.n_entries == 2
    assert led.n_trials == 7  # 4 + 3
    assert led.luck_bar == pytest.approx(readers.expected_max_sharpe(7))
    assert led.best == pytest.approx(0.42)  # max checkpoint aggregate_sharpe
    by_id = {r.id: r for r in led.runs}
    # Entry 0001's config_hash IS a 40-hex git SHA → resolves directly.
    row1 = by_id["ledger-2026-06-13-0001"]
    assert row1.commit_url.endswith(_GIT_SHA_A)
    assert row1.commit == _GIT_SHA_A[:12]
    # Entry 0002's config_hash is a 64-hex CONTENT hash; the join surfaces the
    # `signed` checkpoint's git_sha so the row now carries a resolvable link
    # (E1-M1-LEDGER-COMMIT-LINKS) — the short hash matches the link target.
    row2 = by_id["ledger-2026-06-13-0002"]
    assert row2.commit_url.endswith(_GIT_SHA_A)
    assert row2.commit == _GIT_SHA_A[:12]


def test_checkpoint_git_sha_index_maps_content_hash_to_commit(sources):
    index = sources_mod.checkpoint_git_sha_index(sources)
    # The content-hash `signed` run maps to its checkpoint's 40-hex git SHA;
    # the smoke run is included by the scan but is harmless (its hash matches
    # no ledger entry). A 40-hex config_hash checkpoint (arima) self-maps.
    assert index[_CONTENT_HASH] == _GIT_SHA_A


def test_checkpoint_git_sha_index_missing_root_is_empty(tmp_path):
    bare = ConsoleSources(
        data_root=tmp_path / "absent",
        ledger_path=tmp_path / "ledger.yaml",
        catalog_path=tmp_path / "catalog.yaml",
        strategy_roots=(tmp_path / "absent" / "phase4a",),
    )
    assert sources_mod.checkpoint_git_sha_index(bare) == {}


def test_load_ledger_honest_degrade_without_git_sha(tmp_path):
    """A content-hash run whose checkpoint recorded no git_sha shows no link."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    # Checkpoint with git_sha=None (mirrors the C2 audit run) — no link possible.
    _write_checkpoint(
        data_root, "audit", seed=9, config_hash=_CONTENT_HASH, git_sha=None
    )
    ledger_path = data_root / "ledger.yaml"
    ledger_path.write_text(
        yaml.safe_dump(
            [
                {
                    "id": "ledger-2026-06-28-0001",
                    "prd": "c2",
                    "milestone": "C2-M3",
                    "agent": "human",
                    "preregistration": "x",
                    "config_hash": _CONTENT_HASH,  # 64-hex, checkpoint has no git_sha
                    "n_comparisons": 0,
                    "started_at": "2026-06-28T15:31:00Z",
                    "completed_at": "2026-06-28T15:31:01Z",
                    "verdict": "gate_passed",
                    "artifacts": ["data/phase4a/audit/"],
                    "notes": "audit",
                },
                {
                    "id": "ledger-2026-06-28-0002",
                    "prd": "b1",
                    "milestone": "B1-M3",
                    "agent": "human",
                    "preregistration": "x",
                    "config_hash": "f" * 64,  # 64-hex matching no checkpoint
                    "n_comparisons": 1,
                    "started_at": "2026-06-28T16:00:00Z",
                    "completed_at": "2026-06-28T16:10:00Z",
                    "verdict": "gate_failed",
                    "artifacts": ["data/b1/missing/"],
                    "notes": "no checkpoint",
                },
            ]
        )
    )
    src = ConsoleSources(
        data_root=data_root,
        ledger_path=ledger_path,
        catalog_path=tmp_path / "catalog.yaml",
        strategy_roots=(data_root / "phase4a",),
    )
    by_id = {r.id: r for r in readers.load_ledger(src).runs}
    # git_sha: null → honest "—", not a fabricated/broken link.
    assert by_id["ledger-2026-06-28-0001"].commit_url is None
    assert by_id["ledger-2026-06-28-0001"].commit is None
    # No matching checkpoint at all → also no link.
    assert by_id["ledger-2026-06-28-0002"].commit_url is None
    assert by_id["ledger-2026-06-28-0002"].commit is None


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
    assert "portfolio.json" in exp
    assert "strategy/arima.json" in exp
    assert "provenance/arima.json" in exp


def test_export_idempotent(sources, tmp_path):
    out1, out2 = tmp_path / "o1", tmp_path / "o2"
    export.write_export(out1, sources)
    export.write_export(out2, sources)
    files1 = sorted(p.relative_to(out1) for p in out1.rglob("*.json"))
    files2 = sorted(p.relative_to(out2) for p in out2.rglob("*.json"))
    # 7 top-level + 2 strategy detail + 2 provenance (2 strategies in fixture).
    assert files1 == files2 and len(files1) == 11
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
    assert "Wrote 11 export files" in capsys.readouterr().out


# ── production sources wiring ─────────────────────────────────────────────────


def test_default_sources_constructs():
    src = ConsoleSources.default()
    assert src.repo_url.endswith("/quant")
    assert src.commit_url("abc123").endswith("/commit/abc123")
    assert src.commit_url(None) is None
    assert src.now().tzinfo is not None
    assert src.strategy_roots[0].name == "phase4a"
    assert src.registry_path is not None
    assert src.registry_path.name == "strategy_registry.yaml"


# ── feedback: capture payload + issue construction (E1-M6) ───────────────────

import types  # noqa: E402
from urllib.parse import parse_qs, urlparse  # noqa: E402

from quant.console import feedback  # noqa: E402


def _report(**overrides) -> feedback.FeedbackReport:
    base = dict(
        title="Sparkline renders off-by-one",
        type="bug",
        severity="high",
        description="The Overview sparkline starts a day late.",
        panel="Overview",
        build_sha="abc1234",
        timestamp="2026-06-28T18:30:00Z",
        app_version="0.0.0",
    )
    base.update(overrides)
    return feedback.FeedbackReport(**base)


def test_feedback_report_validates_enums():
    with pytest.raises(ValueError, match="title must not be empty"):
        _report(title="   ")
    with pytest.raises(ValueError, match="type"):
        _report(type="feature")
    with pytest.raises(ValueError, match="severity"):
        _report(severity="critical")


def test_issue_title_trimmed():
    assert feedback.issue_title(_report(title="  spacey  ")) == "spacey"


def test_issue_body_carries_payload_and_context():
    body = feedback.issue_body(_report())
    # user fields
    assert "bug" in body and "high" in body
    assert "sparkline starts a day late" in body
    # auto-captured context
    assert "Panel: Overview" in body
    assert "Build: abc1234" in body
    assert "App version: 0.0.0" in body
    assert "Reported: 2026-06-28T18:30:00Z" in body


def test_issue_url_is_prefilled_and_labeled():
    url = feedback.issue_url(_report())
    parsed = urlparse(url)
    assert parsed.path.endswith("/issues/new")
    qs = parse_qs(parsed.query)
    assert qs["labels"] == [feedback.FEEDBACK_LABEL]
    assert qs["title"] == ["Sparkline renders off-by-one"]
    assert "Panel: Overview" in qs["body"][0]


def test_issue_url_respects_repo_override():
    url = feedback.issue_url(_report(), repo_url="https://github.com/acme/widgets")
    assert url.startswith("https://github.com/acme/widgets/issues/new?")


# ── feedback: GitHub read (injectable, degrades without gh) ───────────────────


def test_fetch_issue_via_gh_raises_when_gh_missing(monkeypatch):
    monkeypatch.setattr(feedback.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="gh.*not found"):
        feedback.fetch_issue_via_gh(7)


def test_fetch_issue_via_gh_parses_json(monkeypatch):
    monkeypatch.setattr(feedback.shutil, "which", lambda _: "/usr/bin/gh")
    payload = {"number": 7, "title": "t", "body": "b", "url": "u", "state": "OPEN"}

    def fake_runner(cmd, capture_output, text):
        assert cmd[:3] == ["gh", "issue", "view"]
        return types.SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    out = feedback.fetch_issue_via_gh(7, runner=fake_runner)
    assert out["number"] == 7


def test_fetch_issue_via_gh_raises_on_nonzero(monkeypatch):
    monkeypatch.setattr(feedback.shutil, "which", lambda _: "/usr/bin/gh")

    def fail_runner(cmd, capture_output, text):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="not authenticated")

    with pytest.raises(RuntimeError, match="not authenticated"):
        feedback.fetch_issue_via_gh(7, runner=fail_runner)


# ── feedback: issue → task transform + YAML append ───────────────────────────

_SEED_PRIORITIES = """\
# Living priorities — a header comment that a YAML round-trip would destroy.
version: 1
last_updated: 2026-06-01

schema:
  task_status: [ready, blocked, in_progress, done, skipped]
  complexity: [small, medium, large]

tasks:

  - id: SEED-1
    rank: 7
    title: "seed task"
    project: E
    sub_project: E1
    status: done
    completed_at: 2026-06-01
    depends_on: []
    blocks: []
    est_complexity: small
"""

_FAKE_ISSUE = {
    "number": 42,
    "title": 'Sparkline off-by-one on "Overview"',
    "body": "Type: bug\nThe sparkline starts a day late.",
    "url": "https://github.com/James-Delgado/quant/issues/42",
    "state": "OPEN",
}


def test_build_task_record_shape():
    task = feedback.build_task_record(_FAKE_ISSUE, rank=8)
    assert task.id == "FEEDBACK-42"
    assert task.rank == 8
    assert task.status == "ready"
    assert task.issue_url.endswith("/issues/42")


def test_build_task_record_url_fallback():
    task = feedback.build_task_record({"number": 5, "title": "t"}, rank=1)
    assert task.id == "FEEDBACK-5"
    assert task.issue_url.endswith("/issues/5")  # constructed when url absent


def test_format_task_block_parses_as_yaml():
    task = feedback.build_task_record(_FAKE_ISSUE, rank=8)
    block = feedback.format_task_block(task)
    # The block must be a valid one-item tasks list on its own.
    parsed = yaml.safe_load("tasks:\n" + block)["tasks"]
    assert len(parsed) == 1
    rec = parsed[0]
    assert rec["id"] == "FEEDBACK-42"
    assert rec["title"] == 'Sparkline off-by-one on "Overview"'  # quotes survive
    assert rec["status"] == "ready"
    assert rec["references"]["issue"].endswith("/issues/42")
    assert "Promoted from feedback issue #42" in rec["notes"]


def test_append_task_preserves_comments_and_bumps_last_updated(tmp_path):
    path = tmp_path / "PRIORITIES.yaml"
    path.write_text(_SEED_PRIORITIES)
    task = feedback.build_task_record(_FAKE_ISSUE, rank=8)
    feedback.append_task_to_priorities(path, feedback.format_task_block(task), today="2026-06-28")
    text = path.read_text()
    assert "header comment that a YAML round-trip would destroy" in text  # preserved
    assert "last_updated: 2026-06-28" in text  # bumped
    data = yaml.safe_load(text)
    ids = [t["id"] for t in data["tasks"]]
    assert ids == ["SEED-1", "FEEDBACK-42"]


def test_promote_end_to_end(tmp_path):
    path = tmp_path / "PRIORITIES.yaml"
    path.write_text(_SEED_PRIORITIES)
    task = feedback.promote(
        42,
        priorities_path=path,
        issue_fetcher=lambda n: _FAKE_ISSUE,
        today="2026-06-28",
    )
    assert task.id == "FEEDBACK-42"
    assert task.rank == 8  # max existing rank (7) + 1
    data = yaml.safe_load(path.read_text())
    assert data["tasks"][-1]["id"] == "FEEDBACK-42"


def test_promote_idempotency_guard(tmp_path):
    path = tmp_path / "PRIORITIES.yaml"
    path.write_text(_SEED_PRIORITIES)
    fetch = lambda n: _FAKE_ISSUE  # noqa: E731
    feedback.promote(42, priorities_path=path, issue_fetcher=fetch, today="2026-06-28")
    with pytest.raises(ValueError, match="already exists"):
        feedback.promote(42, priorities_path=path, issue_fetcher=fetch, today="2026-06-28")


def test_promoted_task_passes_priorities_drift_checks(tmp_path):
    """The appended task must keep the file valid under tests/test_priorities.py."""
    import test_priorities as tp

    path = tmp_path / "PRIORITIES.yaml"
    path.write_text(_SEED_PRIORITIES)
    feedback.promote(42, priorities_path=path, issue_fetcher=lambda n: _FAKE_ISSUE)
    tp.validate_priorities(tp.load_priorities(path))


def test_cli_feedback_promote(monkeypatch, tmp_path, capsys):
    from quant.console import __main__ as cli

    path = tmp_path / "PRIORITIES.yaml"
    path.write_text(_SEED_PRIORITIES)
    monkeypatch.setattr(feedback, "fetch_issue_via_gh", lambda n: _FAKE_ISSUE)
    rc = cli.main(["feedback", "promote", "42", "--priorities", str(path)])
    assert rc == 0
    assert "FEEDBACK-42" in capsys.readouterr().out
    assert yaml.safe_load(path.read_text())["tasks"][-1]["id"] == "FEEDBACK-42"
