import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeatureCatalog } from "@/pages/FeatureCatalog";
import { stubExportFetch } from "./mockExport";

beforeEach(() => stubExportFetch());
afterEach(() => vi.unstubAllGlobals());

describe("Feature Catalog panel", () => {
  it("renders the registry rows with a de-underscored group", async () => {
    render(<FeatureCatalog />);
    expect(await screen.findByText("ret_1d")).toBeInTheDocument();
    expect(screen.getByText("DFF")).toBeInTheDocument();
  });

  it("shows an explicit monitoring-pending state when stats are null", async () => {
    render(<FeatureCatalog />);
    expect(await screen.findByText(/Monitoring pending/)).toBeInTheDocument();
    // The registered count is real; coverage/stability render an honest dash.
    expect(screen.getByText("Registered").parentElement?.textContent).toContain("2");
  });

  it("surfaces the OOS-evidence badge from the ablation status", async () => {
    render(<FeatureCatalog />);
    // Scope to the pill — the word "edge" also appears in the explanatory note.
    expect(await screen.findByText("edge", { selector: ".pill" })).toBeInTheDocument();
    expect(screen.getByText("no edge", { selector: ".pill" })).toBeInTheDocument();
  });
});
