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

  it("links runs that carry a commit and dashes runs without one", async () => {
    const { container } = render(<Ledger />);
    await screen.findByText("Trials to date");
    const links = container.querySelectorAll(
      'a[href*="github.com/James-Delgado/quant/commit/"]',
    );
    // The git-sha run and the content-hash run joined to its checkpoint commit
    // both link; the audit run (no recorded git_sha) shows "—".
    expect(links.length).toBe(2);
    expect(container.querySelectorAll("td.mono .dim").length).toBe(1);
  });

  it("shows the trial count that drives the multiple-testing bar", async () => {
    render(<Ledger />);
    expect(await screen.findByText("Trials to date")).toBeInTheDocument();
    expect(screen.getByText("75")).toBeInTheDocument();
  });
});
