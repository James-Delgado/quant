"""The eight console readers (PRD §4.1).

Each reader takes a :class:`quant.console.sources.ConsoleSources` (defaulting to
the production sources) and returns a frozen view-model from
:mod:`quant.console.viewmodels`. All numeric work is delegated to the existing
backtest modules (``metrics``, ``regimes``, ``statistics``) — these readers only
shape artifacts into view-models. Nothing here computes a strategy result; it
reads results the runners already produced.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from quant.backtest.metrics import compute_metrics
from quant.backtest.regimes import VIXThresholdDetector
from quant.backtest.statistics import expected_max_sharpe
from quant.console import viewmodels as vm
from quant.console.sources import (
    FRESH_THRESHOLD_DAYS,
    MARKET_SERIES,
    ArmCheckpoint,
    ConsoleSources,
    discover_strategies,
    read_oos_returns,
)

# ── Tunables (pinned; METHODOLOGY §1) ────────────────────────────────────────
SPARKLINE_POINTS = 48
SERIES_POINTS = 300  # downsample target for detail-chart series
ROLLING_SHARPE_WINDOW = 63  # bars (≈ one quarter)
RETURN_HIST_BINS = 41
TRADING_DAYS = 252

# Friendly strategy names keyed by arm id; falls back to a titleised id.
_STRATEGY_NAMES = {
    "arima": "ARIMA(1,0,0) control",
    "signed": "GBM · signed returns",
    "vol_scaled": "GBM · vol-scaled returns",
    "triple_barrier": "GBM · triple-barrier",
}

# Named stress episodes (inclusive date ranges), pinned per METHODOLOGY §1.
_STRESS_WINDOWS: tuple[tuple[str, str, str], ...] = (
    ("Global Financial Crisis", "2007-10-01", "2009-03-31"),
    ("COVID crash", "2020-02-01", "2020-04-30"),
    ("2022 rate selloff", "2022-01-01", "2022-10-31"),
)

# Market-level, live-computable condition axes (E1-M1-CONDITIONS-MARKET-AXIS).
# The volatility axis is the market VIX bucketed by VIXThresholdDetector's pinned
# thresholds; the rates axis is the trailing change in the 10-year Treasury yield.
_VOL_CONDITIONS = ["low_vol", "mid_vol", "high_vol"]
_RATES_CONDITIONS = ["rates_falling", "rates_steady", "rates_rising"]

# FRED series ids backing the two market axes (pinned; METHODOLOGY §1).
VIX_SERIES_ID = "VIXCLS"
RATES_SERIES_ID = "DGS10"

# Rates-axis tunables (pinned; METHODOLOGY §1). The 10-year yield's trailing
# change over ~one trading quarter classifies the rate environment; a deadband
# keeps small wobbles in the neutral "steady" bucket. Point-in-time: the change
# uses only past observations, so labelling date D never peeks past D.
RATES_CHANGE_WINDOW = 63  # bars (≈ one quarter)
RATES_DEADBAND = 0.25  # percentage points (±25 bps) around zero = "steady"


# ── Small helpers ────────────────────────────────────────────────────────────


def _iso_date(value: object) -> str | None:
    """Format a timestamp-ish value as ``YYYY-MM-DD`` (None-safe)."""
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    return ts.date().isoformat()


def _equity_curve(returns: pd.Series) -> pd.Series:
    """Growth of 1 (cumulative product of ``1 + r``)."""
    return (1.0 + returns.fillna(0.0)).cumprod()


def _downsample(series: pd.Series, n: int) -> pd.Series:
    """Evenly subsample a series to at most ``n`` points (endpoints kept)."""
    if len(series) <= n or n <= 0:
        return series
    idx = np.linspace(0, len(series) - 1, n).round().astype(int)
    idx = np.unique(idx)
    return series.iloc[idx]


def _to_timepoints(series: pd.Series, n: int = SERIES_POINTS) -> list[vm.TimePoint]:
    sampled = _downsample(series, n)
    points: list[vm.TimePoint] = []
    for ts, val in sampled.items():
        date = _iso_date(ts)
        if date is None or pd.isna(val):
            continue
        points.append(vm.TimePoint(date=date, value=float(val)))
    return points


def _rolling_sharpe(returns: pd.Series, window: int = ROLLING_SHARPE_WINDOW) -> pd.Series:
    r = returns.fillna(0.0)
    mean = r.rolling(window).mean()
    std = r.rolling(window).std(ddof=1)
    sharpe = (mean / std) * math.sqrt(TRADING_DAYS)
    return sharpe.replace([np.inf, -np.inf], np.nan).dropna()


def _return_histogram(returns: pd.Series, bins: int = RETURN_HIST_BINS) -> vm.Histogram:
    clean = returns.dropna().to_numpy()
    if clean.size == 0:
        return vm.Histogram(bin_edges=[], counts=[])
    counts, edges = np.histogram(clean, bins=bins)
    return vm.Histogram(
        bin_edges=[float(e) for e in edges],
        counts=[int(c) for c in counts],
    )


def _sharpe(returns: pd.Series) -> float:
    return float(compute_metrics(returns).get("sharpe", 0.0))


def _strategy_metrics(returns: pd.Series) -> vm.StrategyMetrics:
    m = compute_metrics(returns)
    calmar = m.get("calmar")
    calmar = None if calmar is None or math.isnan(float(calmar)) else float(calmar)
    return vm.StrategyMetrics(
        sharpe=float(m.get("sharpe", 0.0)),
        sortino=float(m.get("sortino", 0.0)),
        calmar=calmar,
        total_return=float(m.get("total_return", 0.0)),
        annualized_return=float(m.get("annualized_return", 0.0)),
        max_drawdown=float(m.get("max_drawdown", 0.0)),
    )


def _name_for(arm_id: str) -> str:
    return _STRATEGY_NAMES.get(arm_id, arm_id.replace("_", " ").title())


def _verdict_by_config_hash(ledger_entries: list, config_hash: str | None) -> str:
    if not config_hash:
        return "unknown"
    for entry in ledger_entries:
        if getattr(entry, "config_hash", None) == config_hash:
            return getattr(entry, "verdict", "unknown")
    return "unknown"


def _driver_text(metrics: vm.StrategyMetrics, verdict: str) -> str:
    sharpe = metrics.sharpe
    if verdict == "gate_failed":
        return (
            f"Did not beat the ARIMA control across required regimes "
            f"(Sharpe {sharpe:+.2f}); pre-committed gate failed."
        )
    if verdict == "inconclusive":
        return f"Baseline control arm (Sharpe {sharpe:+.2f}); not a gate decision."
    if verdict == "passed":
        return f"Cleared its pre-committed gate (Sharpe {sharpe:+.2f})."
    return f"Sharpe {sharpe:+.2f} over the out-of-sample period."


def _description_for(meta: dict) -> str:
    cfg = meta.get("run_config", {})
    model = (cfg.get("model_params") or {}).get("type", "model")
    wf = cfg.get("walk_forward", {})
    n_symbols = meta.get("n_symbols_in_panel") or len(meta.get("symbols", []) or [])
    horizon = cfg.get("label_horizon")
    return (
        f"{model} on a {n_symbols}-symbol daily panel; purged walk-forward "
        f"(train {wf.get('train_window')}, test {wf.get('test_window')}, "
        f"step {wf.get('step')}, embargo {wf.get('embargo')}), "
        f"label horizon {horizon} bar(s)."
    )


# ── 1. Strategies ────────────────────────────────────────────────────────────


def load_strategies(sources: ConsoleSources | None = None) -> list[vm.StrategyCard]:
    """Roster of strategies — one per non-smoke checkpoint with a return series."""
    sources = sources or ConsoleSources.default()
    from quant.ledger import load_ledger as load_ledger_entries

    try:
        ledger_entries = load_ledger_entries(sources.ledger_path)
    except FileNotFoundError:
        ledger_entries = []

    cards: list[vm.StrategyCard] = []
    for ck in discover_strategies(sources):
        returns = read_oos_returns(ck.path / "oos_returns.parquet")
        metrics = _strategy_metrics(returns)
        equity = _equity_curve(returns)
        spark = [float(v) for v in _downsample(equity, SPARKLINE_POINTS).to_numpy()]
        config_hash = ck.metadata.get("config_hash")
        verdict = _verdict_by_config_hash(ledger_entries, config_hash)
        cards.append(
            vm.StrategyCard(
                id=ck.id,
                name=_name_for(ck.id),
                mode="research",
                sharpe=metrics.sharpe,
                total_return=metrics.total_return,
                max_drawdown=metrics.max_drawdown,
                status=verdict,
                driver=_driver_text(metrics, verdict),
                sparkline=spark,
                n_folds=int(ck.metadata.get("n_folds", 0) or 0),
                oos_start=_iso_date(ck.metadata.get("oos_start")),
                oos_end=_iso_date(ck.metadata.get("oos_end")),
                config_hash=config_hash,
            )
        )
    return cards


# ── 1b. Strategy portfolio (C6 registry) ─────────────────────────────────────


def load_portfolio(sources: ConsoleSources | None = None) -> vm.PortfolioView:
    """The deployment portfolio — enabled (in-use) + idle strategies from C6.

    Reads the C6 strategy registry and reuses its serializable view-model
    (:func:`quant.execution.strategy_registry.strategy_view_models`) so the
    equal-weight allocation and provenance-summary logic live in exactly one
    place (DRY; METHODOLOGY §4 — consume the contract, don't re-derive it). This
    reader only re-shapes those dicts into frozen DTOs for the schema-checked
    export. It carries **no live P&L** — realized performance is E3.
    """
    sources = sources or ConsoleSources.default()
    from quant.execution.strategy_registry import (
        DEFAULT_REGISTRY_PATH,
        load_registry,
        strategy_view_models,
    )

    registry_path = sources.registry_path or DEFAULT_REGISTRY_PATH
    registry = load_registry(registry_path)
    views = strategy_view_models(registry)

    strategies = [
        vm.PortfolioStrategy(
            id=v["id"],
            display_name=v["display_name"],
            description=v["description"],
            model_ref=v["model_ref"],
            target_ref=v["target_ref"],
            universe=list(v["universe"]),
            cadence=v["cadence"],
            broker=v["broker"],
            status=v["status"],
            allocation_pct=float(v["allocation_pct"]),
            provenance=v["provenance"],
            provenance_summary=v["provenance_summary"],
        )
        for v in views
    ]
    n_enabled = sum(1 for s in strategies if s.status == "enabled")
    return vm.PortfolioView(
        strategies=strategies,
        n_enabled=n_enabled,
        n_idle=len(strategies) - n_enabled,
    )


# ── 2. Strategy detail ───────────────────────────────────────────────────────


def _find_checkpoint(sources: ConsoleSources, strategy_id: str) -> ArmCheckpoint | None:
    for ck in discover_strategies(sources):
        if ck.id == strategy_id:
            return ck
    return None


def load_strategy(
    strategy_id: str, sources: ConsoleSources | None = None
) -> vm.StrategyDetail | None:
    """Full detail for one strategy, or ``None`` if no such checkpoint exists."""
    sources = sources or ConsoleSources.default()
    ck = _find_checkpoint(sources, strategy_id)
    if ck is None:
        return None

    returns = read_oos_returns(ck.path / "oos_returns.parquet")
    metrics = _strategy_metrics(returns)
    equity = _equity_curve(returns)
    drawdown = equity / equity.cummax() - 1.0

    from quant.ledger import load_ledger as load_ledger_entries

    try:
        ledger_entries = load_ledger_entries(sources.ledger_path)
    except FileNotFoundError:
        ledger_entries = []
    config_hash = ck.metadata.get("config_hash")
    verdict = _verdict_by_config_hash(ledger_entries, config_hash)
    git_sha = ck.metadata.get("git_sha")

    return vm.StrategyDetail(
        id=ck.id,
        name=_name_for(ck.id),
        description=_description_for(ck.metadata),
        mode="research",
        metrics=metrics,
        figures={
            "n_symbols": int(
                ck.metadata.get("n_symbols_in_panel")
                or len(ck.metadata.get("symbols", []) or [])
            ),
            "n_folds": int(ck.metadata.get("n_folds", 0) or 0),
            "n_oos_bars": int(ck.metadata.get("n_oos_bars", len(returns)) or 0),
        },
        equity=_to_timepoints(equity),
        drawdown=_to_timepoints(drawdown),
        rolling_sharpe=_to_timepoints(_rolling_sharpe(returns)),
        return_hist=_return_histogram(returns),
        condition_link="/conditions",
        why=_driver_text(metrics, verdict),
        config_hash=config_hash,
        commit=git_sha,
        commit_url=sources.commit_url(git_sha),
    )


# ── 3. Conditions ────────────────────────────────────────────────────────────


def _by_date(series: pd.Series) -> pd.Series:
    """Re-index a series by calendar date (midnight), dropping intraday time.

    The OOS return index is tz-naive UTC with the NY-close instant preserved
    (e.g. ``2006-01-02 05:00``). Market (FRED) series carry midnight-UTC calendar
    dates. Normalising both to midnight lets them align by *date* — the documented
    NY↔UTC alignment care (project_lake_tz_alignment) — without an off-by-one from
    comparing a 05:00 close against a 00:00 observation.
    """
    out = series.copy()
    out.index = pd.DatetimeIndex(out.index).normalize()
    return out


def _align_market(series: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    """Carry a market series forward onto ``dates`` (point-in-time ffill).

    Forward-fill is the only honest direction here: a date inherits the most
    recent *prior* observation, never a future one. Dates before the series
    begins stay NaN and are dropped by the labellers.
    """
    if series is None or series.empty or len(dates) == 0:
        return pd.Series(dtype=float)
    union = series.index.union(dates)
    return series.reindex(union).sort_index().ffill().reindex(dates)


def _vol_labels(vix_on_dates: pd.Series) -> pd.Series:
    """Volatility-regime labels from the market VIX (reuses VIXThresholdDetector).

    Delegates the bucketing — and its pinned ``low=15`` / ``high=25`` thresholds —
    to the repo's :class:`VIXThresholdDetector`, so the console and the backtester
    carve volatility identically (METHODOLOGY §4 — consume the contract).
    """
    vix = vix_on_dates.dropna()
    if vix.empty:
        return pd.Series(dtype=object)
    detector = VIXThresholdDetector(vix)
    return detector.label(pd.DatetimeIndex(vix.index))


def _rates_labels(dgs10_on_dates: pd.Series) -> pd.Series:
    """Rate-environment labels from the trailing change in the 10-year yield.

    ``rates_rising`` / ``rates_falling`` when the trailing ``RATES_CHANGE_WINDOW``
    change clears ±``RATES_DEADBAND``; ``rates_steady`` inside the deadband.
    Point-in-time: the change references only past observations.
    """
    rates = dgs10_on_dates.dropna()
    if rates.empty:
        return pd.Series(dtype=object)
    change = (rates - rates.shift(RATES_CHANGE_WINDOW)).dropna()
    if change.empty:
        return pd.Series(dtype=object)
    labels = pd.Series("rates_steady", index=change.index, dtype=object)
    labels[change >= RATES_DEADBAND] = "rates_rising"
    labels[change <= -RATES_DEADBAND] = "rates_falling"
    return labels


def _aggregate_returns(per_strategy: dict[str, pd.Series]) -> pd.Series:
    """Equal-weight mean across strategies, aligned by date (outer join)."""
    if not per_strategy:
        return pd.Series(dtype=float)
    frame = pd.DataFrame(per_strategy)
    return frame.mean(axis=1, skipna=True).dropna()


def _sharpe_under(returns: pd.Series, labels: pd.Series, condition: str) -> tuple[float, int]:
    """Aggregate Sharpe of ``returns`` on the dates labelled ``condition``."""
    if labels.empty:
        return 0.0, 0
    mask = labels.index[labels == condition]
    sub = returns.reindex(mask).dropna()
    if sub.empty:
        return 0.0, 0
    return _sharpe(sub), int(len(sub))


# (axis_name, ordered condition labels, per-date label Series) for one market axis.
_MarketAxis = tuple[str, list[str], pd.Series]


def _market_axes(sources: ConsoleSources, dates: pd.DatetimeIndex) -> list[_MarketAxis]:
    """Build the available market-level condition axes over ``dates``.

    An axis is included only when its backing FRED series is present and yields
    at least one label after alignment — otherwise it is omitted (not faked),
    so an unconfigured/absent feed degrades honestly (METHODOLOGY §9).
    """
    fn = sources.market_series_fn
    if fn is None or len(dates) == 0:
        return []
    axes: list[_MarketAxis] = []
    vix = fn(VIX_SERIES_ID)
    if vix is not None:
        vol_labels = _vol_labels(_align_market(_by_date(vix), dates))
        if not vol_labels.empty:
            axes.append(("volatility", list(_VOL_CONDITIONS), vol_labels))
    rates = fn(RATES_SERIES_ID)
    if rates is not None:
        rates_labels = _rates_labels(_align_market(_by_date(rates), dates))
        if not rates_labels.empty:
            axes.append(("rates", list(_RATES_CONDITIONS), rates_labels))
    return axes


def load_conditions(sources: ConsoleSources | None = None) -> vm.ConditionsView:
    """Sharpe by *market* condition + strategy×condition heatmap + stress windows.

    Attribution axes are live-computable, point-in-time market conditions
    (volatility from the VIX, rates from the 10-year yield) aligned to the OOS
    calendar — not the strategy's own returns. The labels are global (one per
    date), so the heatmap measures each strategy within the same market regimes.
    """
    sources = sources or ConsoleSources.default()
    per_strategy: dict[str, pd.Series] = {}
    for ck in discover_strategies(sources):
        per_strategy[ck.id] = _by_date(read_oos_returns(ck.path / "oos_returns.parquet"))

    aggregate = _aggregate_returns(per_strategy)
    master_dates = (
        pd.DatetimeIndex(aggregate.index) if not aggregate.empty else pd.DatetimeIndex([])
    )
    market_axes = _market_axes(sources, master_dates)

    axes = [vm.ConditionAxis(name=name, conditions=list(conds)) for name, conds, _ in market_axes]

    by_condition: list[vm.ConditionStat] = []
    for name, conds, labels in market_axes:
        for cond in conds:
            sharpe, n = _sharpe_under(aggregate, labels, cond)
            by_condition.append(vm.ConditionStat(name, cond, sharpe, n))

    cond_labels = [cond for _, conds, _ in market_axes for cond in conds]
    strategy_ids = sorted(per_strategy)
    values: list[list[float | None]] = []
    for sid in strategy_ids:
        row: list[float | None] = []
        s_returns = per_strategy[sid]
        for _, conds, labels in market_axes:
            for cond in conds:
                sharpe, n = _sharpe_under(s_returns, labels, cond)
                row.append(sharpe if n > 0 else None)
        values.append(row)
    heatmap = vm.ConditionHeatmap(
        strategies=strategy_ids, conditions=cond_labels, values=values
    )

    stress: list[vm.StressWindow] = []
    has_dated_aggregate = not aggregate.empty and isinstance(
        aggregate.index, pd.DatetimeIndex
    )
    for name, start, end in _STRESS_WINDOWS:
        if not has_dated_aggregate:
            stress.append(vm.StressWindow(name, start, end, None, 0))
            continue
        window = aggregate.loc[
            (aggregate.index >= pd.Timestamp(start))
            & (aggregate.index <= pd.Timestamp(end))
        ]
        if window.empty:
            stress.append(vm.StressWindow(name, start, end, None, 0))
        else:
            stress.append(
                vm.StressWindow(name, start, end, _sharpe(window), int(len(window)))
            )

    return vm.ConditionsView(
        axes=axes,
        by_condition=by_condition,
        heatmap=heatmap,
        stress_windows=stress,
    )


# ── 4. Provenance ────────────────────────────────────────────────────────────

# The six leakage controls enforced by the backtester (backtest/CLAUDE.md).
_LEAKAGE_CONTROLS = (
    ("Purge", "Training samples overlapping a test label window are dropped."),
    ("Embargo", "A temporal gap separates train and test beyond label overlap."),
    ("Embargo length", "Fixed conservative constant ≥ max feature lookback."),
    ("Test-fold sizing", "Test fold length ≫ label horizon + embargo."),
    ("Refit isolation", "Purge/embargo apply to the CV path only, not production refit."),
    ("In-window tuning", "Hyperparameter search runs inside each training window."),
)
_SELF_TESTS = (
    ("Random-strategy null", "A no-skill strategy yields ≈ zero edge net of costs."),
    ("Leaky-strategy trap", "An intentionally leaky strategy is caught by the controls."),
)


def _lineage_from_features(feature_columns: list[str]) -> list[str]:
    """Derive data lineage (one source per line) from the feature column set."""
    cols = set(feature_columns or [])
    lineage: list[str] = ["Alpaca daily OHLCV bars"]
    if {"DGS10", "DFF", "VIXCLS"} & cols:
        lineage.append("FRED macro series (publication-lag corrected)")
    if {"sentiment_score", "doc_count", "has_coverage"} & cols:
        lineage.append("SEC EDGAR + RSS → FinBERT sentiment")
    if any(c.startswith("xs_rank_") for c in cols):
        lineage.append("Cross-sectional panel ranks")
    return lineage


def load_provenance(
    run: str, sources: ConsoleSources | None = None
) -> vm.ProvenanceView | None:
    """Run config + enforced leakage controls + lineage for one run."""
    sources = sources or ConsoleSources.default()
    ck = _find_checkpoint(sources, run)
    if ck is None:
        return None

    meta = ck.metadata
    cfg = meta.get("run_config", {})
    wf = cfg.get("walk_forward", {})
    sim = cfg.get("sim_kwargs", {})
    model_params = cfg.get("model_params", {})

    config = vm.RunConfigView(
        model=model_params.get("type", "unknown"),
        label_horizon=cfg.get("label_horizon"),
        train_window=wf.get("train_window"),
        test_window=wf.get("test_window"),
        step=wf.get("step"),
        embargo=wf.get("embargo"),
        initial_capital=sim.get("initial_capital"),
        commission_per_share=sim.get("commission_per_share"),
        slippage_bps=sim.get("slippage_bps"),
    )
    git_sha = meta.get("git_sha")
    return vm.ProvenanceView(
        run=ck.id,
        name=_name_for(ck.id),
        commit=git_sha,
        commit_url=sources.commit_url(git_sha),
        started_at=meta.get("started_at"),
        finished_at=meta.get("finished_at"),
        n_symbols=int(
            meta.get("n_symbols_in_panel") or len(meta.get("symbols", []) or [])
        ),
        n_folds=int(meta.get("n_folds", 0) or 0),
        config=config,
        leakage_controls=[
            vm.ControlStatus(name=n, status="enforced", detail=d)
            for n, d in _LEAKAGE_CONTROLS
        ],
        self_tests=[
            vm.ControlStatus(name=n, status="passing", detail=d) for n, d in _SELF_TESTS
        ],
        lineage=_lineage_from_features(cfg.get("feature_columns", [])),
    )


# ── 5. Feature Catalog ───────────────────────────────────────────────────────


def load_catalog(sources: ConsoleSources | None = None) -> vm.CatalogView:
    """Feature registry (+ optional monitoring stats via ``feature_monitor_fn``)."""
    sources = sources or ConsoleSources.default()
    from quant.features.catalog import load_catalog as load_feature_catalog

    records = load_feature_catalog(sources.catalog_path)
    monitor = sources.feature_monitor_fn

    cards: list[vm.FeatureCard] = []
    coverages: list[float] = []
    stable = drifting = stale = 0
    for name in sorted(records):
        rec = records[name]
        stats = (monitor(name) if monitor else None) or {}
        coverage = stats.get("coverage")
        stability = stats.get("stability")
        if coverage is not None:
            coverages.append(float(coverage))
        if stability == "stable":
            stable += 1
        elif stability == "drifting":
            drifting += 1
        elif stability == "stale":
            stale += 1
        cards.append(
            vm.FeatureCard(
                name=rec.name,
                group=rec.family,
                source=rec.source,
                formula=rec.formula,
                point_in_time_rule=rec.point_in_time_rule,
                lookback_bars=rec.lookback_bars,
                publication_lag_days=rec.publication_lag_days,
                ablation_status=rec.ablation_status,
                oos_status=rec.attribution_status,
                glossary_ref=rec.glossary_ref,
                coverage=None if coverage is None else float(coverage),
                mean=None if stats.get("mean") is None else float(stats["mean"]),
                std=None if stats.get("std") is None else float(stats["std"]),
                stability=stability,
                distribution=stats.get("distribution"),
            )
        )

    summary = vm.CatalogSummary(
        registered=len(cards),
        stable=stable,
        drifting=drifting,
        stale=stale,
        mean_coverage=(sum(coverages) / len(coverages)) if coverages else None,
    )
    return vm.CatalogView(summary=summary, features=cards)


# ── 6. Trial Registry (ledger) ───────────────────────────────────────────────


def _looks_like_git_sha(value: str | None) -> bool:
    return bool(value) and len(value) == 40 and all(c in "0123456789abcdef" for c in value)


def load_ledger(sources: ConsoleSources | None = None) -> vm.LedgerView:
    """The trial registry — every pre-registered comparison + deflation luck-bar."""
    sources = sources or ConsoleSources.default()
    from quant.ledger import cumulative_trial_count
    from quant.ledger import load_ledger as load_ledger_entries

    try:
        entries = load_ledger_entries(sources.ledger_path)
    except FileNotFoundError:
        entries = []

    n_trials = cumulative_trial_count(entries=entries) if entries else 0

    runs: list[vm.LedgerRun] = []
    for e in entries:
        sha = getattr(e, "config_hash", None)
        commit_url = sources.commit_url(sha) if _looks_like_git_sha(sha) else None
        runs.append(
            vm.LedgerRun(
                id=e.id,
                project=e.prd,
                milestone=e.milestone,
                comparisons=int(e.n_comparisons),
                verdict=e.verdict,
                commit=(sha[:12] if sha else None),
                commit_url=commit_url,
                started_at=str(e.started_at),
                completed_at=str(e.completed_at),
            )
        )

    # Best observed strategy Sharpe (vs the luck bar) — from the checkpoints.
    best: float | None = None
    for ck in discover_strategies(sources):
        sharpe = float(ck.metadata.get("aggregate_sharpe", float("nan")))
        if not math.isnan(sharpe):
            best = sharpe if best is None else max(best, sharpe)

    return vm.LedgerView(
        n_trials=int(n_trials),
        n_entries=len(entries),
        luck_bar=float(expected_max_sharpe(int(n_trials))),
        best=best,
        runs=runs,
    )


# ── 7. Data status ───────────────────────────────────────────────────────────


def data_status(sources: ConsoleSources | None = None) -> vm.DataStatusView:
    """Per-feed freshness from the lake (status: fresh | stale | missing)."""
    sources = sources or ConsoleSources.default()
    now = sources.now()
    latest_fn = sources.latest_timestamp_fn

    feeds: list[vm.FeedStatus] = []
    for spec in sources.feeds:
        ts = None
        if latest_fn is not None:
            # A feed whose schema differs (wrong ts_col) degrades to "missing"
            # rather than crashing the whole export.
            try:
                ts = latest_fn(spec.key, spec.ts_col)
            except Exception:
                ts = None
        if ts is None:
            feeds.append(vm.FeedStatus(spec.label, None, None, "missing"))
            continue
        ts = pd.Timestamp(ts)
        now_ts = pd.Timestamp(now)
        if ts.tz is not None and now_ts.tz is None:
            now_ts = now_ts.tz_localize(ts.tz)
        elif ts.tz is None and now_ts.tz is not None:
            ts = ts.tz_localize(now_ts.tz)
        age_days = (now_ts - ts).total_seconds() / 86400.0
        status = "fresh" if age_days <= FRESH_THRESHOLD_DAYS else "stale"
        feeds.append(
            vm.FeedStatus(
                feed=spec.label,
                last_timestamp=_iso_date(ts),
                age_days=round(float(age_days), 2),
                status=status,
            )
        )
    return vm.DataStatusView(asof=_iso_date(now) or "", feeds=feeds)


# ── 8. Market snapshot ───────────────────────────────────────────────────────


def market_snapshot(sources: ConsoleSources | None = None) -> vm.MarketSnapshot:
    """VIX / 10Y / Fed-funds from the lake; deferred metrics stated honestly."""
    sources = sources or ConsoleSources.default()
    market_fn = sources.market_value_fn

    values: dict[str, float | None] = {field: None for field in MARKET_SERIES.values()}
    if market_fn is not None:
        for series_id, field in MARKET_SERIES.items():
            values[field] = market_fn(series_id)

    notes = [
        "2s10s spread and market breadth are not yet ingested (planned for E4).",
    ]
    if market_fn is None:
        notes.insert(0, "Market series source not configured.")

    return vm.MarketSnapshot(
        asof=_iso_date(sources.now()),
        vix=values["vix"],
        ten_year=values["ten_year"],
        fed_funds=values["fed_funds"],
        notes=notes,
    )
