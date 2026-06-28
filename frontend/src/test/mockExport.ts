import { vi } from "vitest";
import type {
  CatalogView,
  ConditionsView,
  DataStatusView,
  LedgerView,
  MarketSnapshot,
  ProvenanceView,
  StrategyCard,
  StrategyDetail,
} from "@/types/viewmodels";

/**
 * Synthetic export fixtures + a fetch stub that routes by filename, mirroring
 * the real `src/quant/console/export/*.json` shapes. Component tests run
 * hermetically against these instead of the synced files.
 */
export const STRATEGIES: StrategyCard[] = [
  {
    id: "arima",
    name: "ARIMA(1,0,0) control",
    mode: "research",
    sharpe: 0.42,
    total_return: 2.72,
    max_drawdown: -0.6,
    status: "candidate",
    driver: "Captures the long trend.",
    sparkline: [1, 1.1, 1.3, 1.8, 2.7],
    n_folds: 87,
    oos_start: "2004-06-20",
    oos_end: "2026-03-30",
    config_hash: "f3b7533",
  },
  {
    id: "signed",
    name: "GBM · signed returns",
    mode: "research",
    sharpe: -0.34,
    total_return: -0.72,
    max_drawdown: -0.75,
    status: "underperforms",
    driver: "Learns crisis mean-reversion; fights the trend.",
    sparkline: [1, 0.95, 0.9, 0.8, 0.27],
    n_folds: 87,
    oos_start: "2004-06-20",
    oos_end: "2026-03-30",
    config_hash: "90e7cb4",
  },
];

function detail(id: string, name: string, sharpe: number, why: string): StrategyDetail {
  return {
    id,
    name,
    description: `${name} — synthetic detail for tests.`,
    mode: "research",
    metrics: {
      sharpe,
      sortino: sharpe,
      calmar: sharpe,
      total_return: sharpe > 0 ? 2.72 : -0.72,
      annualized_return: sharpe > 0 ? 0.06 : -0.06,
      max_drawdown: -0.7,
    },
    figures: { n_folds: 87, n_oos_bars: 5394, n_symbols: 33 },
    equity: [
      { date: "2004-06-21", value: 1.0 },
      { date: "2010-01-01", value: sharpe > 0 ? 1.6 : 0.9 },
      { date: "2026-03-30", value: sharpe > 0 ? 2.7 : 0.3 },
    ],
    drawdown: [
      { date: "2004-06-21", value: 0 },
      { date: "2009-03-01", value: -0.3 },
      { date: "2026-03-30", value: -0.1 },
    ],
    rolling_sharpe: [
      { date: "2005-06-21", value: 0.5 },
      { date: "2015-06-21", value: -0.2 },
      { date: "2026-03-30", value: 0.1 },
    ],
    return_hist: { bin_edges: [-0.02, -0.01, 0, 0.01, 0.02], counts: [3, 20, 25, 5] },
    condition_link: "/conditions",
    why,
    config_hash: id,
    commit: "397f68a",
    commit_url: "https://github.com/James-Delgado/quant/commit/397f68a",
  };
}

export const STRATEGY_DETAIL: Record<string, StrategyDetail> = {
  arima: detail("arima", "ARIMA(1,0,0) control", 0.42, "It stays aligned with the long trend."),
  signed: detail("signed", "GBM · signed returns", -0.34, "It fights the trend in up-markets."),
};

export const CONDITIONS: ConditionsView = {
  axes: [
    { name: "volatility", conditions: ["low_vol", "mid_vol", "high_vol"] },
    { name: "trend", conditions: ["uptrend", "downtrend"] },
  ],
  by_condition: [
    { axis: "volatility", condition: "low_vol", sharpe: -1.23, n_bars: 1792 },
    { axis: "volatility", condition: "high_vol", sharpe: 0.36, n_bars: 1792 },
    { axis: "trend", condition: "uptrend", sharpe: 1.14, n_bars: 2795 },
    { axis: "trend", condition: "downtrend", sharpe: -1.63, n_bars: 2400 },
  ],
  heatmap: {
    strategies: ["arima", "signed"],
    conditions: ["low_vol", "high_vol", "uptrend", "downtrend"],
    values: [
      [1.45, 0.15, 1.64, -1.01],
      [-0.96, -0.24, 1.72, -1.33],
    ],
  },
  stress_windows: [
    { name: "Global Financial Crisis", start: "2007-10-01", end: "2009-03-31", sharpe: 0.28, n_bars: 372 },
    { name: "COVID crash", start: "2020-02-01", end: "2020-04-30", sharpe: -3.29, n_bars: 61 },
  ],
};

export const DATA_STATUS: DataStatusView = {
  asof: "2026-06-28",
  feeds: [
    { feed: "Daily equity bars", last_timestamp: "2026-06-05", age_days: 23.5, status: "stale" },
    { feed: "FRED macro series", last_timestamp: "2026-06-04", age_days: 24.7, status: "stale" },
  ],
};

export const MARKET: MarketSnapshot = {
  asof: "2026-06-28",
  vix: 15.4,
  ten_year: 4.47,
  fed_funds: 3.62,
  notes: ["2s10s spread and market breadth are not yet ingested (planned for E4)."],
};

// Monitoring stats are null on purpose: the lake-backed feature monitor
// (E1-M1-FEATURE-MONITOR) is not wired, so the panel renders a pending state.
export const CATALOG: CatalogView = {
  summary: { registered: 2, stable: 0, drifting: 0, stale: 0, mean_coverage: null },
  features: [
    {
      name: "ret_1d",
      group: "price",
      source: "alpaca_ohlcv",
      formula: "close.pct_change()",
      point_in_time_rule: "uses only bar-t close",
      lookback_bars: 1,
      publication_lag_days: 0,
      ablation_status: "tested_edge",
      oos_status: "both",
      glossary_ref: "docs/concepts/feature-glossary.md#ret_1d",
      coverage: null,
      mean: null,
      std: null,
      stability: null,
      distribution: null,
    },
    {
      name: "DFF",
      group: "macro",
      source: "fred",
      formula: "FRED DFF series",
      point_in_time_rule: "observation date shifted +1 business day",
      lookback_bars: 0,
      publication_lag_days: 1,
      ablation_status: "tested_no_edge",
      oos_status: "both",
      glossary_ref: "docs/concepts/feature-glossary.md#DFF",
      coverage: null,
      mean: null,
      std: null,
      stability: null,
      distribution: null,
    },
  ],
};

export const LEDGER: LedgerView = {
  n_trials: 75,
  n_entries: 14,
  luck_bar: 0.85,
  best: 0.42,
  runs: [
    {
      id: "ledger-2026-06-13-0001",
      project: "phase-4a",
      milestone: "M4",
      comparisons: 30,
      verdict: "gate_failed",
      commit: "397f68acc56c",
      commit_url: "https://github.com/James-Delgado/quant/commit/397f68acc56c",
      started_at: "2026-06-13 17:55:43+00:00",
      completed_at: "2026-06-13 17:55:43+00:00",
    },
    {
      id: "ledger-2026-06-27-0005",
      project: "b2",
      milestone: "B2-M2",
      comparisons: 1,
      verdict: "gate_failed",
      commit: "4ca7489f1273",
      commit_url: null,
      started_at: "2026-06-27 23:03:46+00:00",
      completed_at: "2026-06-27 23:22:31+00:00",
    },
  ],
};

function provenance(run: string, name: string, model: string, commitUrl: string | null): ProvenanceView {
  return {
    run,
    name,
    commit: commitUrl ? "397f68acc56c5fe1" : "f3b75332527b",
    commit_url: commitUrl,
    started_at: "2026-06-13T18:14:19+00:00",
    finished_at: "2026-06-13T18:31:02+00:00",
    n_symbols: 33,
    n_folds: 87,
    config: {
      model,
      label_horizon: 1,
      train_window: 504,
      test_window: 63,
      step: 63,
      embargo: 3,
      initial_capital: 100000,
      commission_per_share: 0.005,
      slippage_bps: 5,
    },
    leakage_controls: [
      { name: "Purge", status: "enforced", detail: "Label-window overlap removed." },
      { name: "Embargo", status: "enforced", detail: "Serial-correlation gap." },
    ],
    self_tests: [
      { name: "Random-strategy null", status: "passing", detail: "≈ zero edge net of costs." },
      { name: "Leaky-strategy trap", status: "passing", detail: "Leak is detected." },
    ],
    lineage: ["Alpaca daily OHLCV bars", "FRED macro series", "FinBERT sentiment"],
  };
}

export const PROVENANCE: Record<string, ProvenanceView> = {
  arima: provenance(
    "arima",
    "ARIMA(1,0,0) control",
    "ARIMABaseline",
    "https://github.com/James-Delgado/quant/commit/397f68acc56c",
  ),
  signed: provenance("signed", "GBM · signed returns", "GBMModel", null),
};

const ROUTES: Record<string, unknown> = {
  "strategy/arima.json": STRATEGY_DETAIL.arima,
  "strategy/signed.json": STRATEGY_DETAIL.signed,
  "strategies.json": STRATEGIES,
  "conditions.json": CONDITIONS,
  "data_status.json": DATA_STATUS,
  "market.json": MARKET,
  "catalog.json": CATALOG,
  "ledger.json": LEDGER,
  "provenance/arima.json": PROVENANCE.arima,
  "provenance/signed.json": PROVENANCE.signed,
};

/** Install a fetch stub that resolves each export file from the fixtures. */
export function stubExportFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown) => {
      const url = String(input);
      const key = Object.keys(ROUTES).find((k) => url.includes(k));
      if (!key) return { ok: false, status: 404, json: async () => null };
      return { ok: true, status: 200, json: async () => ROUTES[key] };
    }),
  );
}
