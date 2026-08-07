/**
 * Thin data client with a static ↔ api source swap (E2-M4).
 *
 * Static mode (default) fetches the M1 export JSON copied to `public/data/`
 * (see scripts/copy-export.mjs). Api mode points the SAME paths at the E2
 * FastAPI service, which mirrors the export tree under `/data/` by
 * construction — so the swap is a base-URL change only; callers, types, and
 * every fetch path stay put (DECISIONS #3, E2 PRD §4 M4). No business logic
 * here — it fetches, parses, and types.
 *
 * Flag: `VITE_DATA_SOURCE` = `static` (default) | `api`;
 * `VITE_API_BASE` overrides the api-mode service origin;
 * `VITE_CONSOLE_API_TOKEN` (optional) is the bearer token the api-mode
 * feedback submission carries when the server has `CONSOLE_API_TOKEN` set.
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

export type DataSource = "static" | "api";

/** The subset of `import.meta.env` the resolvers read (injectable for tests). */
export interface DataEnv {
  VITE_DATA_SOURCE?: string;
  VITE_API_BASE?: string;
  VITE_CONSOLE_API_TOKEN?: string;
  BASE_URL?: string;
}

/** Matches `python -m quant.console.api`'s localhost-only default bind. */
export const DEFAULT_API_BASE = "http://127.0.0.1:8000";

/** The active data source, or throw — a misconfigured flag must fail loudly
 * at startup, not silently serve static data while the operator believes the
 * console is live (METHODOLOGY §9). */
export function resolveDataSource(env: DataEnv): DataSource {
  const raw = (env.VITE_DATA_SOURCE ?? "static").trim().toLowerCase();
  if (raw === "static" || raw === "api") return raw;
  throw new Error(
    `VITE_DATA_SOURCE must be "static" or "api", got ${JSON.stringify(raw)}`,
  );
}

/** The api-mode service origin, trailing slashes trimmed. */
export function resolveApiBase(env: DataEnv): string {
  const raw = (env.VITE_API_BASE ?? "").trim();
  return (raw || DEFAULT_API_BASE).replace(/\/+$/, "");
}

/** The base every data fetch resolves against, per the active source. */
export function resolveDataBase(env: DataEnv): string {
  if (resolveDataSource(env) === "api") return `${resolveApiBase(env)}/data/`;
  // `BASE_URL` is "./" (see vite.config base), so static data resolves
  // relative to the served document — dev, static build, and opened dist.
  return `${env.BASE_URL ?? "./"}data/`;
}

/** The optional bearer token for api-mode mutate routes (undefined = none). */
export function resolveApiToken(env: DataEnv): string | undefined {
  const raw = (env.VITE_CONSOLE_API_TOKEN ?? "").trim();
  return raw || undefined;
}

const ENV: DataEnv = import.meta.env;

export const DATA_SOURCE: DataSource = resolveDataSource(ENV);
export const API_BASE: string = resolveApiBase(ENV);
export const API_TOKEN: string | undefined = resolveApiToken(ENV);

const DATA_BASE = resolveDataBase(ENV);

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
  // copy-export.mjs copies it into public/data alongside the payloads. In api
  // mode the service serves a live manifest at the same path.
  manifest: (signal?: AbortSignal) =>
    getJSON<ExportManifest>("_manifest.json", signal),
};

export type DataClient = typeof dataClient;
