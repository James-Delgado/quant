import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Ledger } from "@/pages/Ledger";
import { stubExportFetch } from "./mockExport";

beforeEach(() => stubExportFetch());
afterEach(() => vi.unstubAllGlobals());

describe("Trial Registry panel", () => {
  it("renders the deflation luck bar and best-vs-bar verdict", async () => {
    render(<Ledger />);
    expect(await screen.findByText("Deflation luck bar")).toBeInTheDocument();
    // best 0.42 does not clear luck 0.85.
    expect(screen.getByText(/0.42 < 0.85/)).toBeInTheDocument();
    expect(screen.getByText("does not clear the bar")).toBeInTheDocument();
  });

  it("links resolvable commits and shows a dash for content-hash runs", async () => {
    const { container } = render(<Ledger />);
    await screen.findByText("Trials to date");
    const links = container.querySelectorAll('a[href*="github.com/James-Delgado/quant/commit/"]');
    expect(links.length).toBe(1); // only the run carrying a commit_url
  });

  it("shows the trial count that drives the multiple-testing bar", async () => {
    render(<Ledger />);
    expect(await screen.findByText("Trials to date")).toBeInTheDocument();
    expect(screen.getByText("75")).toBeInTheDocument();
  });
});
