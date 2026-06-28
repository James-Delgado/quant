import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DataMarket } from "@/pages/DataMarket";
import { stubExportFetch } from "./mockExport";

beforeEach(() => stubExportFetch());
afterEach(() => vi.unstubAllGlobals());

describe("Data & Market panel", () => {
  it("renders per-feed freshness with an honest stale pill", async () => {
    render(<DataMarket />);
    expect(await screen.findByText("Daily equity bars")).toBeInTheDocument();
    expect(screen.getAllByText("stale").length).toBeGreaterThan(0);
  });

  it("renders real market values and an explicit pending state for E4 metrics", async () => {
    render(<DataMarket />);
    expect(await screen.findByText("VIX")).toBeInTheDocument();
    expect(screen.getByText("15.4")).toBeInTheDocument();
    expect(screen.getByText("Breadth > MA200")).toBeInTheDocument();
    expect(screen.getAllByText("lands with E4").length).toBe(2);
  });
});
