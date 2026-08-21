"""Frozen view-model dataclasses returned by the console readers.

These are plain data-transfer objects (PRD §8 "Data contract"). They are
immutable (``frozen=True``) so a reader's output cannot be mutated by a caller
before export, and they serialise to JSON via :func:`dataclasses.asdict` in
:mod:`quant.console.export`. Field names are the export-JSON contract and are
drift-checked against :mod:`quant.console.schemas`.

No method on these types computes anything — all computation lives in
:mod:`quant.console.readers`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── Shared primitives ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TimePoint:
    """A single ``(date, value)`` sample in a chart-ready series."""

    date: str  # ISO-8601 date (YYYY-MM-DD)
    value: float


@dataclass(frozen=True)
class Histogram:
    """A return-distribution histogram: ``len(counts) == len(bin_edges) - 1``."""

    bin_edges: list[float]
    counts: list[int]


# ── Strategies panel ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StrategyMetrics:
    """Headline performance metrics for a strategy (annualised where relevant)."""

    sharpe: float
    sortino: float
    calmar: float | None  # undefined (None) when max drawdown is zero
    total_return: float
    annualized_return: float
    max_drawdown: float


@dataclass(frozen=True)
class StrategyCard:
    """Roster-row / Overview-table summary of one strategy (arm)."""

    id: str
    name: str
    mode: str  # "research" until live execution (E3) lights it up
    sharpe: float
    total_return: float
    max_drawdown: float
    status: str  # ledger verdict: passed | gate_failed | inconclusive | unknown
    driver: str  # plain-language one-liner ("why")
    sparkline: list[float]  # downsampled equity curve (growth of 1)
    # SPY buy-and-hold growth-of-1 over the SAME OOS span, downsampled to the
    # same point count so it overlays index-for-index on the Overview hero
    # (E1-M3-OVERVIEW-BENCHMARK). Cost-net (one buy-and-hold round trip through
    # the same simulator the arms trade) so it is parity-comparable with the
    # strategy equity above (E1-M3-BENCHMARK-COST-NAME). Empty when the benchmark
    # price is unavailable or does not fully cover the span — an honest "no
    # overlay", never faked (§9).
    benchmark_sparkline: list[float]
    # Identity of the benchmark above (e.g. "SPY"), so the UI legend names
    # whatever the service layer actually computed instead of a frontend constant
    # that could silently go stale if the pinned symbol changes
    # (E1-M3-BENCHMARK-COST-NAME). ``None`` when no overlay was drawn.
    benchmark_name: str | None
    n_folds: int
    oos_start: str | None
    oos_end: str | None
    config_hash: str | None


@dataclass(frozen=True)
class StrategyDetail:
    """Full per-strategy detail view (figures + chart-ready series)."""

    id: str
    name: str
    description: str
    mode: str
    metrics: StrategyMetrics
    figures: dict[str, int]  # n_symbols, n_folds, n_oos_bars
    equity: list[TimePoint]
    # SPY buy-and-hold growth-of-1 over the SAME OOS span, sampled at the identical
    # positions/dates as ``equity`` so the two curves overlay index-for-index on the
    # detail equity chart (E1-STRATEGY-DETAIL-BENCHMARK). Cost-net via the same
    # simulator the arms trade — the detail-resolution sibling of
    # ``StrategyCard.benchmark_sparkline`` (both derive from ``_benchmark_growth``).
    # Empty when the benchmark price is unavailable or does not fully cover the span
    # — an honest "no overlay", never faked (§9).
    benchmark_equity: list[TimePoint]
    drawdown: list[TimePoint]
    rolling_sharpe: list[TimePoint]
    return_hist: Histogram
    condition_link: str  # route to the Conditions panel
    why: str
    config_hash: str | None
    commit: str | None
    commit_url: str | None


# ── Strategy Portfolio panel (C6 registry) ───────────────────────────────────


@dataclass(frozen=True)
class PortfolioStrategy:
    """One deployable strategy from the C6 registry, as the console renders it.

    The deployment-side counterpart to :class:`StrategyCard` (which is a
    *research* arm). Fields mirror the serializable view-model C6-M1 exposes
    (``strategy_registry.strategy_view_models``); the console wraps them in a
    frozen DTO so the export is schema-checked like every other panel. ``status``
    is ``"enabled"`` (in-use) or ``"idle"``; ``allocation_pct`` is the
    equal-weight capital share (0 for idle strategies). No live P&L is carried —
    realized per-strategy performance is E3 (live monitoring), deliberately
    absent here (DECISIONS #5/#7).
    """

    id: str
    display_name: str
    description: str
    model_ref: str
    target_ref: str
    universe: list[str]
    cadence: str
    broker: str
    status: str  # "enabled" (in-use) | "idle"
    allocation_pct: float
    provenance: str
    provenance_summary: str


@dataclass(frozen=True)
class PortfolioView:
    """The strategy portfolio: enabled (in-use) + idle strategies + counts."""

    strategies: list[PortfolioStrategy]
    n_enabled: int
    n_idle: int


# ── Conditions panel ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConditionAxis:
    """A live-computable attribution axis and its ordered condition labels."""

    name: str  # market-level axis, e.g. "volatility" | "rates"
    conditions: list[str]


@dataclass(frozen=True)
class ConditionStat:
    """Equal-weight aggregate Sharpe under one condition of one axis."""

    axis: str
    condition: str
    sharpe: float
    n_bars: int


@dataclass(frozen=True)
class ConditionHeatmap:
    """Strategy × condition Sharpe grid (``values[i][j]`` may be ``None``)."""

    strategies: list[str]
    conditions: list[str]
    values: list[list[float | None]]


@dataclass(frozen=True)
class StressWindow:
    """Named historical episode and the equal-weight aggregate Sharpe within it."""

    name: str
    start: str
    end: str
    sharpe: float | None
    n_bars: int


@dataclass(frozen=True)
class ConditionsView:
    axes: list[ConditionAxis]
    by_condition: list[ConditionStat]
    heatmap: ConditionHeatmap
    stress_windows: list[StressWindow]


# ── Provenance panel ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunConfigView:
    """The subset of a run's config the operator needs to judge it."""

    model: str
    label_horizon: int | None
    train_window: int | None
    test_window: int | None
    step: int | None
    embargo: int | None
    # Hyperparameter-search budget (RandomizedSearchCV inside each training
    # window). Both ``None`` for models with no search (e.g. ARIMA); the GBM arms
    # carry ``n_iter`` candidate draws × ``inner_folds`` inner CV splits.
    n_iter: int | None
    inner_folds: int | None
    initial_capital: float | None
    commission_per_share: float | None
    slippage_bps: float | None


@dataclass(frozen=True)
class ControlStatus:
    """A leakage control or self-test rendered as quiet enforced-status."""

    name: str
    status: str  # "enforced" | "passing" | ...
    detail: str | None = None


@dataclass(frozen=True)
class ProvenanceView:
    run: str
    name: str
    commit: str | None
    commit_url: str | None
    started_at: str | None
    finished_at: str | None
    n_symbols: int | None
    n_folds: int | None
    config: RunConfigView
    leakage_controls: list[ControlStatus]
    self_tests: list[ControlStatus]
    lineage: list[str]  # data sources, one per line


# ── Feature Catalog panel ────────────────────────────────────────────────────


@dataclass(frozen=True)
class FeatureCard:
    """One catalog feature: registry fields + optional monitoring stats.

    Monitoring stats (``coverage``/``mean``/``std``/``stability``/``distribution``)
    are ``None`` when no feature monitor is wired in (E1-M1 ships the registry;
    the live lake-backed monitor is a documented follow-up). ``oos_status`` is the
    catalog's ``attribution_status`` (METHODOLOGY §14).
    """

    name: str
    group: str  # FeatureRecord.family
    source: str
    formula: str
    point_in_time_rule: str
    lookback_bars: int
    publication_lag_days: int
    ablation_status: str
    oos_status: str
    glossary_ref: str
    coverage: float | None = None
    mean: float | None = None
    std: float | None = None
    stability: str | None = None  # stable | drifting | stale
    distribution: list[int] | None = None


@dataclass(frozen=True)
class CatalogSummary:
    registered: int
    stable: int
    drifting: int
    stale: int
    mean_coverage: float | None


@dataclass(frozen=True)
class CatalogView:
    summary: CatalogSummary
    features: list[FeatureCard]


# ── Trial Registry panel ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class LedgerRun:
    """One ledger trial, UI-named (no internal file paths surfaced)."""

    id: str
    project: str  # ledger "prd" field
    milestone: str
    comparisons: int
    verdict: str
    commit: str | None  # short config_hash / git sha
    commit_url: str | None
    started_at: str
    completed_at: str


@dataclass(frozen=True)
class LedgerView:
    n_trials: int  # cumulative_trial_count (sum of n_comparisons)
    n_entries: int
    luck_bar: float  # expected max Sharpe under the null at N trials
    best: float | None  # best aggregate Sharpe observed across runs
    runs: list[LedgerRun]


# ── Data & Market panel ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class FeedStatus:
    feed: str
    last_timestamp: str | None
    age_days: float | None
    status: str  # fresh | stale | missing


@dataclass(frozen=True)
class SlaFeedStatus:
    """One ingestor judged against the pinned C1 SLA table (E4-M1).

    The serialized form of a C1-M3 ``monitor_freshness.FeedStatus`` — the same
    verdict ``GET /health`` and the cron monitor report, so the panel can never
    disagree with them about what "fresh" means (METHODOLOGY §6). Dates are ISO
    calendar dates — the granularity the SLA verdict is made at.
    """

    feed: str  # SLA feed name, e.g. "tiingo", "fred:DGS10"
    state: str  # fresh | stale | missing
    latest: str | None  # newest observation date, or None when missing
    required_date: str | None  # oldest date that would still count as fresh
    detail: str


@dataclass(frozen=True)
class LakeGapReport:
    """Missing-session report for one gap-checked lake dataset (E4-M1).

    ``n_gaps`` is ``None`` when the dataset could not be checked (unreadable /
    never written) — the honest degrade, distinct from a verified 0
    (METHODOLOGY §9). ``gap_dates`` lists at most the most recent
    ``health.MAX_GAP_DATES_SURFACED`` gaps; the count is always exact.
    """

    feed: str  # UI label of the dataset checked
    window_start: str | None  # first observed date (ISO), or None unchecked
    window_end: str | None  # last observed date (ISO), or None unchecked
    n_gaps: int | None  # exact missing-session count, or None = could not check
    gap_dates: list[str] = field(default_factory=list)  # most recent gaps, capped


@dataclass(frozen=True)
class DataStatusView:
    asof: str
    feeds: list[FeedStatus]
    # E4-M1 additions: per-ingestor SLA verdicts + per-dataset gap reports.
    # Defaulted so the pre-E4 constructor shape keeps working; the export
    # schema derives from this dataclass, so data_status.json and the API
    # route carry the new fields by construction (METHODOLOGY §6).
    sla: list[SlaFeedStatus] = field(default_factory=list)
    gaps: list[LakeGapReport] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # degrade states, stated honestly


@dataclass(frozen=True)
class AlertItem:
    """One breach alert (E4-M2). Vocabulary + thresholds pinned in ``alerts.py``."""

    kind: str  # staleness | gap | drift | regime_change
    severity: str  # warning | critical
    subject: str  # feed / dataset / axis the breach concerns, e.g. "tiingo"
    message: str


@dataclass(frozen=True)
class AlertsView:
    """The evaluated alert set at one instant (E4-M2).

    ``alerts`` is sorted critical-first (then kind/subject) so every consumer —
    export JSON, API route, log channel — presents the same order. ``notes``
    carries could-not-check degrades, stated honestly (METHODOLOGY §9): an
    empty ``alerts`` list with a non-empty ``notes`` list is *not* an all-clear.
    """

    asof: str
    alerts: list[AlertItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MarketSnapshot:
    """Live market environment (E4-M3 extends the E1 value-only snapshot).

    The regime labels are produced by the SAME condition machinery the
    Conditions panel uses (``readers._vol_labels`` / ``_trend_labels`` /
    ``_rates_labels`` with their pinned thresholds), taken at the latest
    labellable date — so "the current regime" can never disagree between
    surfaces (METHODOLOGY §6). Every E4-M3 field is nullable + defaulted: a
    missing input degrades that field to ``None`` with an explanatory note,
    never a fabricated value (§9).
    """

    asof: str | None
    vix: float | None
    ten_year: float | None
    fed_funds: float | None
    # ── E4-M3: curve, breadth, and live regime labels ────────────────────────
    two_year: float | None = None  # latest DGS2 yield (%)
    spread_2s10s: float | None = None  # 10Y − 2Y at their last common date (pp)
    breadth_above_ma200: float | None = None  # fraction of universe above MA200
    breadth_n_symbols: int | None = None  # symbols with enough history to judge
    vol_regime: str | None = None  # low_vol | mid_vol | high_vol
    trend_regime: str | None = None  # uptrend | downtrend
    rates_regime: str | None = None  # rates_falling | rates_steady | rates_rising
    notes: list[str] = field(default_factory=list)  # deferred metrics, stated honestly


# ── Export manifest (freshness stamp; E1-M1-EXPORT-FRESHNESS-STAMP) ───────────
#
# The payload export (build_export) is byte-idempotent — no embedded timestamp —
# so re-running over unchanged artifacts is a no-op diff. The manifest is the
# DELIBERATE exception: it carries the export-run time and per-source artifact
# mtimes so the UI can show a freshness stamp (PRD §12 risk table). It is written
# as a side artifact (export/_manifest.json) OUTSIDE build_export, so it never
# enters the deterministic payload set. ``source`` is a friendly label, never a
# filesystem path (DECISIONS #5/#7: no internal paths in the UI).


@dataclass(frozen=True)
class ManifestSource:
    source: str  # friendly label, e.g. "Trial Registry" — never a file path
    modified_at: str | None  # ISO-8601 UTC artifact mtime, or None when absent


@dataclass(frozen=True)
class ExportManifest:
    generated_at: str  # ISO-8601 UTC — when write_export last ran
    sources: list[ManifestSource]
