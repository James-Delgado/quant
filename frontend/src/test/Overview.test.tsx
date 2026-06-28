import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Overview } from "@/pages/Overview";
import { stubExportFetch } from "./mockExport";

const FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true } as const;

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

beforeEach(() => stubExportFetch());
afterEach(() => vi.unstubAllGlobals());

function renderOverview() {
  return render(
    <MemoryRouter future={FUTURE}>
      <Overview />
      <LocationProbe />
    </MemoryRouter>,
  );
}

describe("Overview panel", () => {
  it("frames performance as walk-forward backtest, not live execution", async () => {
    renderOverview();
    const banner = await screen.findByText(/Research mode\./);
    // The banner names live P&L only as an explicitly-deferred feature...
    expect(banner.closest(".banner")).toHaveTextContent(
      /Live P&L and intraday market data activate once the execution layer is online/,
    );
    // ...and the hero labels its figures as walk-forward, never "live".
    expect(screen.getAllByText(/walk-forward backtest/).length).toBeGreaterThan(0);
  });

  it("renders the deployable candidate (highest Sharpe) in the hero", async () => {
    const { container } = renderOverview();
    await screen.findByText(/Research mode\./);
    const hero = container.querySelector(".hero .panel") as HTMLElement;
    // arima (+0.42) beats signed (−0.34) -> hero shows its +272.0% return + Sharpe.
    expect(within(hero).getByText("+272.0%")).toBeInTheDocument();
    expect(within(hero).getByText("+0.42")).toBeInTheDocument();
    // candidate name appears in the hero (figure sub + legend).
    expect(within(hero).getAllByText("ARIMA(1,0,0) control").length).toBeGreaterThan(0);
  });

  it("lists every strategy and navigates to its detail on click", async () => {
    const user = userEvent.setup();
    renderOverview();
    const row = await screen.findByRole("link", { name: /Open ARIMA\(1,0,0\) control detail/ });
    await user.click(row);
    await waitFor(() =>
      expect(screen.getByTestId("loc")).toHaveTextContent("/strategies?pick=arima"),
    );
  });

  it("surfaces real feed status without inventing freshness", async () => {
    renderOverview();
    expect(await screen.findByText("Daily equity bars")).toBeInTheDocument();
    expect(screen.getAllByText(/stale/).length).toBeGreaterThan(0);
  });
});
