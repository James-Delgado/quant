/**
 * TypeScript mirror of the export-JSON contract.
 *
 * Source of truth is the Python service layer: `src/quant/console/viewmodels.py`
 * (the frozen dataclasses) and `schemas.py` (the documented schema). These
 * interfaces are a hand-maintained mirror — the seam between the two layers.
 * `dataClient` smoke tests assert the real export parses into these shapes, so
 * a contract drift surfaces as a failing test (METHODOLOGY §6).
 *
 * The frontend RENDERS these; it never computes them (DECISIONS #1).
 */

export interface TimePoint {
  date: string; // ISO-8601 YYYY-MM-DD
  value: number;
}

export interface Histogram {
  bin_edges: number[];
  counts: number[];
}

// ── Strategies ──────────────────────────────────────────────────────────────

export interface StrategyCard {
  id: string;
  name: string;
  mode: string;
  sharpe: number;
  total_return: number;
  max_drawdown: number;
  status: string;
  driver: string;
  sparkline: number[];
  n_folds: number;
  oos_start: string | null;
  oos_end: string | null;
  config_hash: string | null;
}

export interface StrategyMetrics {
  sharpe: number;
  sortino: number;
  calmar: number | null;
  total_return: number;
  annualized_return: number;
  max_drawdown: number;
}

export interface StrategyDetail {
  id: string;
  name: string;
  description: string;
  mode: string;
  metrics: StrategyMetrics;
  figures: Record<string, number>;
  equity: TimePoint[];
  drawdown: TimePoint[];
  rolling_sharpe: TimePoint[];
  return_hist: Histogram;
  condition_link: string;
  why: string;
  config_hash: string | null;
  commit: string | null;
  commit_url: string | null;
}

// ── Conditions ──────────────────────────────────────────────────────────────

export interface ConditionAxis {
  name: string;
  conditions: string[];
}
export interface ConditionStat {
  axis: string;
  condition: string;
  sharpe: number;
  n_bars: number;
}
export interface ConditionHeatmap {
  strategies: string[];
  conditions: string[];
  values: (number | null)[][];
}
export interface StressWindow {
  name: string;
  start: string;
  end: string;
  sharpe: number | null;
  n_bars: number;
}
export interface ConditionsView {
  axes: ConditionAxis[];
  by_condition: ConditionStat[];
  heatmap: ConditionHeatmap;
  stress_windows: StressWindow[];
}

// ── Provenance ──────────────────────────────────────────────────────────────

export interface RunConfigView {
  model: string;
  label_horizon: number | null;
  train_window: number | null;
  test_window: number | null;
  step: number | null;
  embargo: number | null;
  initial_capital: number | null;
  commission_per_share: number | null;
  slippage_bps: number | null;
}
export interface ControlStatus {
  name: string;
  status: string;
  detail: string | null;
}
export interface ProvenanceView {
  run: string;
  name: string;
  commit: string | null;
  commit_url: string | null;
  started_at: string | null;
  finished_at: string | null;
  n_symbols: number | null;
  n_folds: number | null;
  config: RunConfigView;
  leakage_controls: ControlStatus[];
  self_tests: ControlStatus[];
  lineage: string[];
}

// ── Feature Catalog ─────────────────────────────────────────────────────────

export interface FeatureCard {
  name: string;
  group: string;
  source: string;
  formula: string;
  point_in_time_rule: string;
  lookback_bars: number;
  publication_lag_days: number;
  ablation_status: string;
  oos_status: string;
  glossary_ref: string;
  coverage: number | null;
  mean: number | null;
  std: number | null;
  stability: string | null;
  distribution: number[] | null;
}
export interface CatalogSummary {
  registered: number;
  stable: number;
  drifting: number;
  stale: number;
  mean_coverage: number | null;
}
export interface CatalogView {
  summary: CatalogSummary;
  features: FeatureCard[];
}

// ── Trial Registry ──────────────────────────────────────────────────────────

export interface LedgerRun {
  id: string;
  project: string;
  milestone: string;
  comparisons: number;
  verdict: string;
  commit: string | null;
  commit_url: string | null;
  started_at: string;
  completed_at: string;
}
export interface LedgerView {
  n_trials: number;
  n_entries: number;
  luck_bar: number;
  best: number | null;
  runs: LedgerRun[];
}

// ── Data & Market ───────────────────────────────────────────────────────────

export interface FeedStatus {
  feed: string;
  last_timestamp: string | null;
  age_days: number | null;
  status: string;
}
export interface DataStatusView {
  asof: string;
  feeds: FeedStatus[];
}
export interface MarketSnapshot {
  asof: string | null;
  vix: number | null;
  ten_year: number | null;
  fed_funds: number | null;
  notes: string[];
}
