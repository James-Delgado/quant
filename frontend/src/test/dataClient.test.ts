import { afterEach, describe, expect, it, vi } from "vitest";
import { DataFetchError, dataClient } from "@/lib/dataClient";

function mockFetchOnce(body: unknown, ok = true, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok, status, json: async () => body })),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("dataClient", () => {
  it("parses the strategies array shape", async () => {
    mockFetchOnce([
      {
        id: "arima",
        name: "ARIMA(1,0,0) control",
        mode: "research",
        sharpe: 0.42,
        total_return: 2.72,
        max_drawdown: -0.6,
        status: "inconclusive",
        driver: "Baseline control arm.",
        sparkline: [1, 1.1, 1.2],
        n_folds: 87,
        oos_start: "2004-06-20",
        oos_end: "2026-03-30",
        config_hash: "f3b7533",
      },
    ]);
    const rows = await dataClient.strategies();
    expect(rows).toHaveLength(1);
    expect(rows[0].id).toBe("arima");
    expect(typeof rows[0].sharpe).toBe("number");
    expect(Array.isArray(rows[0].sparkline)).toBe(true);
  });

  it("throws DataFetchError on a non-OK response", async () => {
    mockFetchOnce(null, false, 404);
    await expect(dataClient.market()).rejects.toBeInstanceOf(DataFetchError);
  });

  it("parses the freshness manifest shape", async () => {
    mockFetchOnce({
      generated_at: "2026-06-28T23:42:09Z",
      sources: [
        { source: "Trial Registry", modified_at: "2026-06-28T17:52:48Z" },
        { source: "Strategy checkpoints", modified_at: null },
      ],
    });
    const m = await dataClient.manifest();
    expect(m.generated_at).toBe("2026-06-28T23:42:09Z");
    expect(m.sources).toHaveLength(2);
    expect(m.sources[1].modified_at).toBeNull();
  });
});

// ── static ↔ api source resolution (E2-M4) ───────────────────────────────────

import {
  DEFAULT_API_BASE,
  resolveApiBase,
  resolveApiToken,
  resolveDataBase,
  resolveDataSource,
} from "@/lib/dataClient";

describe("data-source resolution (E2-M4)", () => {
  it("defaults to static with the document-relative base", () => {
    expect(resolveDataSource({})).toBe("static");
    expect(resolveDataBase({ BASE_URL: "./" })).toBe("./data/");
  });

  it("api mode points the same paths at the service's /data tree", () => {
    const env = { VITE_DATA_SOURCE: "api" };
    expect(resolveDataSource(env)).toBe("api");
    expect(resolveDataBase(env)).toBe(`${DEFAULT_API_BASE}/data/`);
  });

  it("honours VITE_API_BASE and trims trailing slashes", () => {
    const env = {
      VITE_DATA_SOURCE: "api",
      VITE_API_BASE: "http://127.0.0.1:9001//",
    };
    expect(resolveApiBase(env)).toBe("http://127.0.0.1:9001");
    expect(resolveDataBase(env)).toBe("http://127.0.0.1:9001/data/");
  });

  it("normalises case/whitespace in the flag", () => {
    expect(resolveDataSource({ VITE_DATA_SOURCE: " API " })).toBe("api");
  });

  it("throws on an unknown flag value rather than silently serving static", () => {
    expect(() => resolveDataSource({ VITE_DATA_SOURCE: "live" })).toThrow(
      /VITE_DATA_SOURCE/,
    );
  });

  it("resolves the optional bearer token, blank meaning none", () => {
    expect(resolveApiToken({})).toBeUndefined();
    expect(resolveApiToken({ VITE_CONSOLE_API_TOKEN: "  " })).toBeUndefined();
    expect(resolveApiToken({ VITE_CONSOLE_API_TOKEN: "tok" })).toBe("tok");
  });
});
