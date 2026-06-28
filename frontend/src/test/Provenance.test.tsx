import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Provenance } from "@/pages/Provenance";
import { stubExportFetch } from "./mockExport";

beforeEach(() => stubExportFetch());
afterEach(() => vi.unstubAllGlobals());

function renderPanel() {
  return render(
    <MemoryRouter>
      <Provenance />
    </MemoryRouter>,
  );
}

describe("Provenance panel", () => {
  it("resolves the commit link to the James-Delgado/quant repo", async () => {
    renderPanel();
    const link = (await screen.findByText(/↗/, {
      selector: "a",
    })) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toContain(
      "github.com/James-Delgado/quant/commit/",
    );
  });

  it("renders leakage controls and self-tests as quiet enforced-status rows", async () => {
    const { container } = renderPanel();
    await screen.findByText("Leakage controls");
    expect(screen.getByText("Harness self-tests")).toBeInTheDocument();
    // enforced-status uses the ported `.ctrl` rows, never a war-story banner.
    expect(container.querySelectorAll(".ctrl").length).toBeGreaterThan(0);
    expect(screen.getByText(/Purge/)).toBeInTheDocument();
  });

  it("renders data lineage one item per line", async () => {
    const { container } = renderPanel();
    await screen.findByText("Data lineage");
    const lineagePanel = Array.from(container.querySelectorAll(".panel")).find(
      (p) => p.querySelector(".phead .t")?.textContent === "Data lineage",
    )!;
    const items = lineagePanel.querySelectorAll(".lin li");
    expect(items.length).toBe(3);
    expect(items[0].textContent).toContain("Alpaca daily OHLCV bars");
  });
});
