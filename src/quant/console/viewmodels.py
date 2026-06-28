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
    drawdown: list[TimePoint]
    rolling_sharpe: list[TimePoint]
    return_hist: Histogram
    condition_link: str  # route to the Conditions panel
    why: str
    config_hash: str | None
    commit: str | None
    commit_url: str | None


# ── Conditions panel ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConditionAxis:
    """A live-computable attribution axis and its ordered condition labels."""

    name: str  # "volatility" | "trend"
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
class DataStatusView:
    asof: str
    feeds: list[FeedStatus]


@dataclass(frozen=True)
class MarketSnapshot:
    asof: str | None
    vix: float | None
    ten_year: float | None
    fed_funds: float | None
    notes: list[str] = field(default_factory=list)  # deferred metrics, stated honestly
