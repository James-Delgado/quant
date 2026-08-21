import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DataMarket } from "@/pages/DataMarket";
import { stubExportFetch } from "./mockExport";

beforeEach(() => stubExportFetch());
afterEach(() => vi.unstubAllGlobals());

describe("Data & Market panel", () => {
  it("renders per-feed freshness with an honest stale pill", async () => {
    render(<DataMarket />);
    // The label appears on both the age-based feed card and its gap report.
    expect(
      (await screen.findAllByText("Daily equity bars")).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText("stale").length).toBeGreaterThan(0);
  });

  it("renders per-ingestor SLA verdicts vs the pinned C1 SLA (E4-M1)", async () => {
    render(<DataMarket />);
    expect(await screen.findByText("tiingo")).toBeInTheDocument();
    expect(screen.getByText("fred:DGS10")).toBeInTheDocument();
    expect(screen.getByText("fresh")).toBeInTheDocument();
    expect(screen.getByText("missing")).toBeInTheDocument();
    expect(
      screen.getByText(/latest 2026-06-26 · required ≥ 2026-06-26/),
    ).toBeInTheDocument();
  });

  it("surfaces a seeded lake gap and an honest unchecked state (E4-M1)", async () => {
    render(<DataMarket />);
    expect(await screen.findByText("1 gap")).toBeInTheDocument();
    expect(screen.getByText(/missing 2026-06-24/)).toBeInTheDocument();
    // n_gaps === null renders as "unchecked" — never a fabricated "no gaps".
    expect(screen.getByText("unchecked")).toBeInTheDocument();
    expect(screen.queryByText("no gaps")).toBeNull();
  });

  it("degrades honestly when the export predates E4 (no sla/gaps fields)", async () => {
    stubExportFetch({
      "data_status.json": {
        asof: "2026-06-28",
        feeds: [
          {
            feed: "Daily equity bars",
            last_timestamp: "2026-06-05",
            age_days: 23.5,
            status: "stale",
          },
        ],
      },
    });
    render(<DataMarket />);
    expect(await screen.findByText("Daily equity bars")).toBeInTheDocument();
    expect(
      screen.getByText(/SLA verdicts are unavailable in this export/),
    ).toBeInTheDocument();
  });

  it("renders live market values: VIX plus the E4-M3 breadth and 2s10s tiles", async () => {
    render(<DataMarket />);
    expect(await screen.findByText("VIX")).toBeInTheDocument();
    expect(screen.getByText("15.4")).toBeInTheDocument();
    expect(screen.getByText("Breadth > MA200")).toBeInTheDocument();
    expect(screen.getByText("68%")).toBeInTheDocument(); // 0.68 fraction
    expect(screen.getByText("31 symbols judged")).toBeInTheDocument();
    expect(screen.getByText("+0.52pp")).toBeInTheDocument(); // 10Y − 2Y
  });

  it("renders the live regime tiles from the condition machinery (E4-M3)", async () => {
    render(<DataMarket />);
    expect(await screen.findByText("Volatility regime")).toBeInTheDocument();
    expect(screen.getByText("mid vol")).toBeInTheDocument();
    expect(screen.getByText("uptrend")).toBeInTheDocument();
    expect(screen.getByText("rates steady")).toBeInTheDocument();
  });

  it("renders the live feature-drift tile from the catalog monitor verdicts (E4-M3)", async () => {
    stubExportFetch({
      "catalog.json": {
        summary: {
          registered: 3,
          stable: 1,
          drifting: 1,
          stale: 0,
          mean_coverage: 0.9,
        },
        features: [],
      },
    });
    render(<DataMarket />);
    // 1 stable + 1 drifting + 0 stale → 2 monitored, 1 drifting.
    expect(await screen.findByText("1 drifting")).toBeInTheDocument();
    expect(screen.getByText("2 features monitored live")).toBeInTheDocument();
  });

  it("degrades each environment tile to an honest pending — never a fabricated value", async () => {
    stubExportFetch({
      "market.json": {
        asof: "2026-06-28",
        vix: 15.4,
        ten_year: 4.47,
        fed_funds: 3.62,
        two_year: null,
        spread_2s10s: null,
        breadth_above_ma200: null,
        breadth_n_symbols: null,
        vol_regime: null,
        trend_regime: null,
        rates_regime: null,
        notes: ["2s10s curve unavailable — DGS2/DGS10 series not readable."],
      },
      "catalog.json": {
        summary: {
          registered: 2,
          stable: 0,
          drifting: 0,
          stale: 0,
          mean_coverage: null,
        },
        features: [],
      },
    });
    render(<DataMarket />);
    await screen.findByText("Breadth > MA200");
    expect(screen.getByText("universe prices unavailable")).toBeInTheDocument();
    expect(screen.getByText("DGS2/DGS10 unavailable")).toBeInTheDocument();
    expect(screen.getByText("feature monitor not wired")).toBeInTheDocument();
    expect(screen.getByText(/2s10s curve unavailable/)).toBeInTheDocument();
  });

  it("leaves the Breadth/2s10s tiles bare — their ⓘ definitions moved to the Overview (E1-M5-OVERVIEW-CONDITION-TIPS)", async () => {
    render(<DataMarket />);
    await screen.findByText("Breadth > MA200");
    // Mockup parity: the Data & Market figs carry no InfoTip; the conditions
    // definitions live once, on the Overview conditions snapshot.
    expect(screen.queryByRole("button", { name: /^Breadth:/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Yield curve:/ })).toBeNull();
  });
});
