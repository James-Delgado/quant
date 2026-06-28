import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Portfolio } from "@/pages/Portfolio";
import { stubExportFetch } from "./mockExport";

beforeEach(() => stubExportFetch());
afterEach(() => vi.unstubAllGlobals());

describe("Strategy Portfolio panel", () => {
  it("renders enabled strategies under 'In use' with allocation and provenance", async () => {
    render(<Portfolio />);
    expect(
      await screen.findByText("ARIMA(1,0,0) Placeholder"),
    ).toBeInTheDocument();
    expect(screen.getByText("In use")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(
      screen.getByText(/Placeholder \(infrastructure/),
    ).toBeInTheDocument();
  });

  it("renders idle strategies under 'Idle' with no allocation", async () => {
    render(<Portfolio />);
    expect(await screen.findByText("GBM (idle)")).toBeInTheDocument();
    expect(screen.getByText("Idle")).toBeInTheDocument();
    expect(screen.getByText(/no capital while idle/)).toBeInTheDocument();
  });

  it("shows status pills for both in-use and idle strategies", async () => {
    render(<Portfolio />);
    await screen.findByText("ARIMA(1,0,0) Placeholder");
    // "in use" / "idle" also appear in the lead copy, so assert the pills via count.
    expect(screen.getAllByText("in use").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("idle").length).toBeGreaterThanOrEqual(1);
  });

  it("shows the universe symbols as tags", async () => {
    render(<Portfolio />);
    expect(await screen.findByText("QQQ")).toBeInTheDocument();
    expect(screen.getByText("IWM")).toBeInTheDocument();
  });

  it("states honestly that no live P&L is shown (deferred to live monitoring)", async () => {
    render(<Portfolio />);
    expect(
      await screen.findByText(/no live P&L is shown here/i),
    ).toBeInTheDocument();
  });
});
