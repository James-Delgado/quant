/**
 * Thin static data client.
 *
 * Fetches the M1 export JSON copied to `public/data/` (see scripts/copy-export.mjs).
 * No business logic — it fetches, parses, and types. When E2 lands the FastAPI
 * service, only `baseUrl` / the fetch target changes; callers and types stay put
 * (DECISIONS #3, PRD §4.2).
 */
import type {
  CatalogView,
  ConditionsView,
  DataStatusView,
  ExportManifest,
  LedgerView,
  MarketSnapshot,
  PortfolioView,
  ProvenanceView,
  StrategyCard,
  StrategyDetail,
} from "@/types/viewmodels";

// `import.meta.env.BASE_URL` is "./" (see vite.config base), so data resolves
// relative to the served document — works in dev, static build, and opened dist.
const DATA_BASE = `${import.meta.env.BASE_URL}data/`;

export class DataFetchError extends Error {
  constructor(
    public readonly file: string,
    public readonly status: number,
  ) {
    super(`Failed to load ${file} (HTTP ${status})`);
    this.name = "DataFetchError";
  }
}

async function getJSON<T>(file: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${DATA_BASE}${file}`, { signal });
  if (!res.ok) throw new DataFetchError(file, res.status);
  return (await res.json()) as T;
}

export const dataClient = {
  strategies: (signal?: AbortSignal) =>
    getJSON<StrategyCard[]>("strategies.json", signal),
  portfolio: (signal?: AbortSignal) =>
    getJSON<PortfolioView>("portfolio.json", signal),
  strategy: (id: string, signal?: AbortSignal) =>
    getJSON<StrategyDetail>(`strategy/${id}.json`, signal),
  conditions: (signal?: AbortSignal) =>
    getJSON<ConditionsView>("conditions.json", signal),
  provenance: (run: string, signal?: AbortSignal) =>
    getJSON<ProvenanceView>(`provenance/${run}.json`, signal),
  catalog: (signal?: AbortSignal) =>
    getJSON<CatalogView>("catalog.json", signal),
  ledger: (signal?: AbortSignal) => getJSON<LedgerView>("ledger.json", signal),
  dataStatus: (signal?: AbortSignal) =>
    getJSON<DataStatusView>("data_status.json", signal),
  market: (signal?: AbortSignal) =>
    getJSON<MarketSnapshot>("market.json", signal),
  // Freshness side artifact (E1-M1-EXPORT-FRESHNESS-STAMP). Leading underscore;
  // copy-export.mjs copies it into public/data alongside the payloads.
  manifest: (signal?: AbortSignal) =>
    getJSON<ExportManifest>("_manifest.json", signal),
};

export type DataClient = typeof dataClient;
