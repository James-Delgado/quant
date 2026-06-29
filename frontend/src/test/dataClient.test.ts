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
