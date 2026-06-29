import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Topbar } from "@/components/layout/Topbar";
import type { ManifestSource } from "@/types/viewmodels";

const SOURCES: ManifestSource[] = [
  { source: "Trial Registry", modified_at: "2026-06-28T17:52:48Z" },
  { source: "Strategy checkpoints", modified_at: null },
];

function renderTopbar(props: Partial<Parameters<typeof Topbar>[0]> = {}) {
  return render(<Topbar title="Overview" onMenu={vi.fn()} {...props} />);
}

describe("Topbar freshness stamp", () => {
  it("renders the export-run time from the manifest, formatted in UTC", () => {
    const { container } = renderTopbar({ generatedAt: "2026-06-28T23:42:09Z" });
    const meta = container.querySelector(".meta");
    expect(meta?.textContent).toBe("data exported 2026-06-28 23:42 UTC");
  });

  it("shows nothing when the manifest is absent (tolerates a missing export)", () => {
    const { container } = renderTopbar();
    const meta = container.querySelector(".meta");
    expect(meta?.textContent).toBe("");
    expect(meta?.getAttribute("title")).toBeNull();
  });

  it("surfaces per-source mtimes in a tooltip, 'unknown' for a null mtime", () => {
    const { container } = renderTopbar({
      generatedAt: "2026-06-28T23:42:09Z",
      sources: SOURCES,
    });
    const title = container.querySelector(".meta")?.getAttribute("title") ?? "";
    expect(title).toContain("Trial Registry updated 2026-06-28 17:52 UTC");
    expect(title).toContain("Strategy checkpoints updated unknown");
  });

  it("renders the accessible per-source freshness disclosure alongside the stamp", () => {
    renderTopbar({ generatedAt: "2026-06-28T23:42:09Z", sources: SOURCES });
    // The ⓘ disclosure trigger (E1-M2-TOPBAR-FRESHNESS-DISCLOSURE) is the
    // keyboard/SR path to the per-source mtimes that also ride on `title`.
    expect(
      screen.getByRole("button", { name: /per-source data freshness/i }),
    ).toBeInTheDocument();
  });

  it("omits the freshness disclosure when there are no per-source mtimes", () => {
    renderTopbar({ generatedAt: "2026-06-28T23:42:09Z" });
    expect(
      screen.queryByRole("button", { name: /per-source data freshness/i }),
    ).toBeNull();
  });

  it("keeps the honest 'not connected' live status (DECISIONS #7)", () => {
    renderTopbar({ generatedAt: "2026-06-28T23:42:09Z" });
    expect(screen.getByText(/not connected/i)).toBeInTheDocument();
  });

  it("exposes no internal file paths (DECISIONS #5/#11)", () => {
    const { container } = renderTopbar({
      generatedAt: "2026-06-28T23:42:09Z",
      sources: SOURCES,
    });
    const meta = container.querySelector(".meta");
    const text = `${meta?.textContent ?? ""} ${meta?.getAttribute("title") ?? ""}`;
    expect(text).not.toMatch(/\.json|\.yaml|data\//);
  });
});
