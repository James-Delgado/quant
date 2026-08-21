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

  it("renders real market values and an explicit pending state for E4 metrics", async () => {
    render(<DataMarket />);
    expect(await screen.findByText("VIX")).toBeInTheDocument();
    expect(screen.getByText("15.4")).toBeInTheDocument();
    expect(screen.getByText("Breadth > MA200")).toBeInTheDocument();
    expect(screen.getAllByText("lands with E4").length).toBe(2);
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
