import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Conditions } from "@/pages/Conditions";
import { stubExportFetch } from "./mockExport";

beforeEach(() => stubExportFetch());
afterEach(() => vi.unstubAllGlobals());

describe("Conditions panel", () => {
  it("renders Sharpe-by-condition bars from the live-computable axes", async () => {
    const { container } = render(<Conditions />);
    await screen.findByText("Sharpe by condition");
    // The de-underscored condition labels appear as SVG bar axis ticks.
    const chart = container.querySelector(".chart") as SVGElement;
    expect(within(chart as unknown as HTMLElement).getByText("low-vol")).toBeInTheDocument();
    expect(within(chart as unknown as HTMLElement).getByText("rates-falling")).toBeInTheDocument();
  });

  it("renders the strategy × condition heatmap", async () => {
    render(<Conditions />);
    expect(await screen.findByText("Strategy × condition")).toBeInTheDocument();
    // row labels are the strategy ids.
    expect(screen.getByText("signed", { selector: ".lbl" })).toBeInTheDocument();
  });

  it("renders the named stress-window table", async () => {
    render(<Conditions />);
    expect(await screen.findByText("Global Financial Crisis")).toBeInTheDocument();
    expect(screen.getByText("COVID crash")).toBeInTheDocument();
    expect(screen.getByText("’07–’09")).toBeInTheDocument();
  });
});
