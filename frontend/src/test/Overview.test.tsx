import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Overview } from "@/pages/Overview";
import { stubExportFetch } from "./mockExport";

const FUTURE = {
  v7_startTransition: true,
  v7_relativeSplatPath: true,
} as const;

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
    expect(screen.getAllByText(/walk-forward backtest/).length).toBeGreaterThan(
      0,
    );
  });

  it("renders the deployable candidate (highest Sharpe) in the hero", async () => {
    const { container } = renderOverview();
    await screen.findByText(/Research mode\./);
    const hero = container.querySelector(".hero .panel") as HTMLElement;
    // arima (+0.42) beats signed (−0.34) -> hero shows its +272.0% return + Sharpe.
    expect(within(hero).getByText("+272.0%")).toBeInTheDocument();
    expect(within(hero).getByText("+0.42")).toBeInTheDocument();
    // candidate name appears in the hero (figure sub + legend).
    expect(
      within(hero).getAllByText("ARIMA(1,0,0) control").length,
    ).toBeGreaterThan(0);
  });

  it("lists every strategy and navigates to its detail on click", async () => {
    const user = userEvent.setup();
    renderOverview();
    const row = await screen.findByRole("link", {
      name: /Open ARIMA\(1,0,0\) control detail/,
    });
    await user.click(row);
    await waitFor(() =>
      expect(screen.getByTestId("loc")).toHaveTextContent(
        "/strategies?pick=arima",
      ),
    );
  });

  it("surfaces real feed status without inventing freshness", async () => {
    renderOverview();
    expect(await screen.findByText("Daily equity bars")).toBeInTheDocument();
    expect(screen.getAllByText(/stale/).length).toBeGreaterThan(0);
  });

  it("summarizes the deployment portfolio (in-use / idle counts) without faking P&L", async () => {
    renderOverview();
    const tile = await screen.findByRole("link", {
      name: /Open Strategy Portfolio — 1 in use, 1 idle/,
    });
    // Honest counts from the registry view-model, plus the equal-weight framing...
    expect(within(tile).getByText("In use")).toBeInTheDocument();
    expect(within(tile).getByText("Idle")).toBeInTheDocument();
    expect(
      within(tile).getByText(/equal-weight allocation/),
    ).toBeInTheDocument();
    // ...and no live-P&L claim anywhere in the tile (E3 territory, DECISIONS #5/#7).
    expect(tile).not.toHaveTextContent(/P&L|live/i);
  });

  it("cross-links the portfolio summary tile to the Portfolio panel", async () => {
    const user = userEvent.setup();
    renderOverview();
    const tile = await screen.findByRole("link", {
      name: /Open Strategy Portfolio/,
    });
    await user.click(tile);
    await waitFor(() =>
      expect(screen.getByTestId("loc")).toHaveTextContent("/portfolio"),
    );
  });

  it("overlays the SPY benchmark on the hero when the export carries it (E1-M3-OVERVIEW-BENCHMARK)", async () => {
    const { container } = renderOverview();
    await screen.findByText(/Research mode\./);
    const hero = container.querySelector(".hero .panel") as HTMLElement;
    // candidate arima carries a benchmark_sparkline -> a dashed bench line is
    // drawn alongside the portfolio line...
    expect(hero.querySelector("path.ln-port")).toBeInTheDocument();
    expect(hero.querySelector("path.ln-bench")).toBeInTheDocument();
    // ...named in the legend, with the honest "same OOS span" caption (no faked
    // overlay note).
    expect(within(hero).getByText(/SPY · buy & hold/)).toBeInTheDocument();
    expect(
      within(hero).getByText(
        /SPY buy-and-hold over the same out-of-sample span/,
      ),
    ).toBeInTheDocument();
    expect(hero).not.toHaveTextContent(/it is not fabricated here/);
  });

  it("retrofits an inline ⓘ tooltip onto the Sharpe figure (E1-M5)", async () => {
    renderOverview();
    await screen.findByText(/Research mode\./);
    // The reusable InfoTip is a focusable button carrying the tip on aria-label.
    const tip = screen.getByRole("button", {
      name: /Sharpe: Risk-adjusted return/,
    });
    expect(tip).toHaveClass("info");
    expect(tip.getAttribute("data-tip")).toContain("annualized");
  });
});
