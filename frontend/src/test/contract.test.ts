import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Contract / drift smoke (METHODOLOGY §6). The TS view-model interfaces in
 * `@/types/viewmodels` are a hand-mirror of the Python export contract. This
 * test reads the REAL synced export (public/data, populated by `pretest`
 * sync-data) and asserts each top-level shape carries the keys the frontend
 * relies on. A Python-side contract change that drops/renames a field surfaces
 * here as a failing test rather than as silently-undefined data at runtime.
 *
 * It skips (not fails) when the export has not been generated — a fresh
 * checkout without `python -m quant.console export` should still build green.
 */
const DATA = path.resolve(__dirname, "../../public/data");

function load(file: string): unknown | null {
  const p = path.join(DATA, file);
  if (!existsSync(p)) return null;
  return JSON.parse(readFileSync(p, "utf8"));
}

function hasKeys(obj: unknown, keys: string[]): boolean {
  if (obj === null || typeof obj !== "object") return false;
  return keys.every((k) => k in (obj as Record<string, unknown>));
}

const STRATEGY_KEYS = [
  "id",
  "name",
  "mode",
  "sharpe",
  "total_return",
  "status",
  "driver",
  "sparkline",
  "benchmark_sparkline",
];
const PORTFOLIO_KEYS = ["strategies", "n_enabled", "n_idle"];
const PORTFOLIO_STRATEGY_KEYS = [
  "id",
  "display_name",
  "description",
  "model_ref",
  "target_ref",
  "universe",
  "status",
  "allocation_pct",
  "provenance",
  "provenance_summary",
];
const DATA_STATUS_KEYS = ["asof", "feeds"];
const FEED_KEYS = ["feed", "last_timestamp", "age_days", "status"];
const MARKET_KEYS = ["asof", "vix", "ten_year", "fed_funds", "notes"];
const LEDGER_KEYS = ["n_trials", "n_entries", "luck_bar", "best", "runs"];
const CATALOG_KEYS = ["summary", "features"];
const CATALOG_FEATURE_KEYS = [
  "name",
  "group",
  "coverage",
  "mean",
  "std",
  "stability",
  "distribution",
  "ablation_status",
  "oos_status",
];
const LEDGER_RUN_KEYS = [
  "id",
  "project",
  "milestone",
  "comparisons",
  "verdict",
  "commit",
  "commit_url",
];
const PROVENANCE_KEYS = [
  "run",
  "name",
  "commit",
  "commit_url",
  "config",
  "leakage_controls",
  "self_tests",
  "lineage",
];
const MANIFEST_KEYS = ["generated_at", "sources"];
const MANIFEST_SOURCE_KEYS = ["source", "modified_at"];

describe("export contract (TS mirror vs real Python export)", () => {
  const strategies = load("strategies.json");
  it.skipIf(strategies === null)("strategies rows match StrategyCard", () => {
    expect(Array.isArray(strategies)).toBe(true);
    for (const row of strategies as unknown[]) {
      expect(hasKeys(row, STRATEGY_KEYS)).toBe(true);
    }
  });

  const portfolio = load("portfolio.json");
  it.skipIf(portfolio === null)("portfolio matches PortfolioView", () => {
    expect(hasKeys(portfolio, PORTFOLIO_KEYS)).toBe(true);
    for (const s of (portfolio as { strategies: unknown[] }).strategies) {
      expect(hasKeys(s, PORTFOLIO_STRATEGY_KEYS)).toBe(true);
    }
  });

  const status = load("data_status.json");
  it.skipIf(status === null)("data_status matches DataStatusView", () => {
    expect(hasKeys(status, DATA_STATUS_KEYS)).toBe(true);
    for (const feed of (status as { feeds: unknown[] }).feeds) {
      expect(hasKeys(feed, FEED_KEYS)).toBe(true);
    }
  });

  const market = load("market.json");
  it.skipIf(market === null)("market matches MarketSnapshot", () => {
    expect(hasKeys(market, MARKET_KEYS)).toBe(true);
  });

  const ledger = load("ledger.json");
  it.skipIf(ledger === null)("ledger matches LedgerView", () => {
    expect(hasKeys(ledger, LEDGER_KEYS)).toBe(true);
  });

  const catalog = load("catalog.json");
  it.skipIf(catalog === null)("catalog matches CatalogView", () => {
    expect(hasKeys(catalog, CATALOG_KEYS)).toBe(true);
    for (const f of (catalog as { features: unknown[] }).features) {
      expect(hasKeys(f, CATALOG_FEATURE_KEYS)).toBe(true);
    }
  });

  it.skipIf(ledger === null)("ledger runs match LedgerRun", () => {
    for (const run of (ledger as { runs: unknown[] }).runs) {
      expect(hasKeys(run, LEDGER_RUN_KEYS)).toBe(true);
    }
  });

  const provenance = load("provenance/arima.json");
  it.skipIf(provenance === null)("provenance matches ProvenanceView", () => {
    expect(hasKeys(provenance, PROVENANCE_KEYS)).toBe(true);
  });

  const manifest = load("_manifest.json");
  it.skipIf(manifest === null)("manifest matches ExportManifest", () => {
    expect(hasKeys(manifest, MANIFEST_KEYS)).toBe(true);
    for (const s of (manifest as { sources: unknown[] }).sources) {
      expect(hasKeys(s, MANIFEST_SOURCE_KEYS)).toBe(true);
    }
  });
});
