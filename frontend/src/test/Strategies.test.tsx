import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Strategies } from "@/pages/Strategies";
import { stubExportFetch } from "./mockExport";

const FUTURE = {
  v7_startTransition: true,
  v7_relativeSplatPath: true,
} as const;

beforeEach(() => stubExportFetch());
afterEach(() => vi.unstubAllGlobals());

function renderStrategies(entry = "/strategies?pick=signed") {
  return render(
    <MemoryRouter initialEntries={[entry]} future={FUTURE}>
      <Strategies />
    </MemoryRouter>,
  );
}

describe("Strategies panel", () => {
  it("renders the roster from the export", async () => {
    renderStrategies();
    const roster = await screen.findByText("GBM · signed returns", {
      selector: ".nm",
    });
    expect(roster).toBeInTheDocument();
    expect(
      screen.getByText("ARIMA(1,0,0) control", { selector: ".nm" }),
    ).toBeInTheDocument();
  });

  it("honors the ?pick= param for the initial detail selection", async () => {
    renderStrategies("/strategies?pick=signed");
    await waitFor(() =>
      expect(
        screen.getByText(/It fights the trend in up-markets\./),
      ).toBeInTheDocument(),
    );
  });

  it("swaps the detail view when a roster row is selected", async () => {
    const user = userEvent.setup();
    renderStrategies("/strategies?pick=signed");
    await screen.findByText(/It fights the trend in up-markets\./);

    await user.click(
      screen.getByText("ARIMA(1,0,0) control", { selector: ".nm" }),
    );
    await waitFor(() =>
      expect(
        screen.getByText(/It stays aligned with the long trend\./),
      ).toBeInTheDocument(),
    );
    // The selected roster row carries the selection marker.
    const selected = screen
      .getByText("ARIMA(1,0,0) control", { selector: ".nm" })
      .closest(".rost");
    expect(selected).toHaveClass("sel");
  });

  it("shows the cumulative chart with a control overlay legend", async () => {
    renderStrategies("/strategies?pick=signed");
    await screen.findByText(/It fights the trend in up-markets\./);
    const legend = screen.getAllByText("ARIMA").length;
    expect(legend).toBeGreaterThan(0);
    expect(within(document.body).getAllByText("Cumulative return").length).toBe(
      1,
    );
  });

  it("shows an honest empty-state when the export has no strategies", async () => {
    // Fresh-clone export: strategies.json is [] (no checkpoints) → no roster,
    // no detail. The panel must say so, not render a blank frame (METHODOLOGY §9).
    stubExportFetch({ "strategies.json": [] });
    renderStrategies("/strategies");
    expect(
      await screen.findByText(/no strategies exported/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/python -m quant\.console export/),
    ).toBeInTheDocument();
    // No roster rows are rendered.
    expect(document.querySelectorAll(".rost").length).toBe(0);
  });
});
