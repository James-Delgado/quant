/// <reference types="vite/client" />

// Build-time git short SHA, injected via Vite `define` (see vite.config.ts).
declare const __BUILD_SHA__: string;

// Build-time app version (package.json), injected via Vite `define`.
declare const __APP_VERSION__: string;

// E2-M4 data-source flag env vars (all optional; see src/lib/dataClient.ts).
interface ImportMetaEnv {
  /** "static" (default) | "api" — which source the data client reads. */
  readonly VITE_DATA_SOURCE?: string;
  /** Api-mode service origin (default http://127.0.0.1:8000). */
  readonly VITE_API_BASE?: string;
  /** Optional bearer token for api-mode mutate routes (POST /feedback). */
  readonly VITE_CONSOLE_API_TOKEN?: string;
}
