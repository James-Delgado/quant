import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { FreshnessDisclosure } from "@/components/layout/FreshnessDisclosure";
import { freshnessLines } from "@/lib/freshness";
import type { ManifestSource } from "@/types/viewmodels";

const GENERATED_AT = "2026-06-28T23:42:09Z";
const SOURCES: ManifestSource[] = [
  { source: "Trial Registry", modified_at: "2026-06-28T17:52:48Z" }, // same day — fresh
  { source: "Strategy checkpoints", modified_at: "2026-06-13T00:00:00Z" }, // 15d behind
  { source: "Feature catalog", modified_at: null }, // unknown
];

describe("freshnessLines", () => {
  it("formats known mtimes in UTC and 'unknown' for null", () => {
    const lines = freshnessLines(SOURCES, GENERATED_AT);
    expect(lines[0]).toMatchObject({
      source: "Trial Registry",
      stamp: "2026-06-28 17:52 UTC",
    });
    expect(lines[2]).toMatchObject({ source: "Feature catalog", stamp: "unknown" });
  });

  it("flags a source lagging the export run by more than the threshold", () => {
    const lines = freshnessLines(SOURCES, GENERATED_AT);
    expect(lines[0].behind).toBe(false); // same-day → fresh
    expect(lines[1].behind).toBe(true); // 15 days → behind
  });

  it("never flags a null mtime as behind (honesty: no guessing)", () => {
    const lines = freshnessLines(SOURCES, GENERATED_AT);
    expect(lines[2].behind).toBe(false);
  });

  it("flags nothing when generatedAt is absent (no datum to compare)", () => {
    const lines = freshnessLines(SOURCES, undefined);
    expect(lines.every((l) => !l.behind)).toBe(true);
  });
});

describe("FreshnessDisclosure", () => {
  it("renders a focusable ⓘ trigger, collapsed by default", () => {
    render(<FreshnessDisclosure generatedAt={GENERATED_AT} sources={SOURCES} />);
    const btn = screen.getByRole("button");
    expect(btn).toHaveClass("info");
    expect(btn).toHaveAttribute("aria-expanded", "false");
    // Region is not in the tree until opened.
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("reveals per-source rows in an accessible region on click", async () => {
    const user = userEvent.setup();
    render(<FreshnessDisclosure generatedAt={GENERATED_AT} sources={SOURCES} />);
    await user.click(screen.getByRole("button"));
    const region = screen.getByRole("region", { name: /per-source data freshness/i });
    expect(region.textContent).toContain("Trial Registry");
    expect(region.textContent).toContain("updated 2026-06-28 17:52 UTC");
    expect(region.textContent).toContain("updated unknown");
  });

  it("wires aria-controls from the trigger to the revealed region", async () => {
    const user = userEvent.setup();
    render(<FreshnessDisclosure generatedAt={GENERATED_AT} sources={SOURCES} />);
    const btn = screen.getByRole("button");
    await user.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
    expect(btn.getAttribute("aria-controls")).toBe(
      screen.getByRole("region").getAttribute("id"),
    );
  });

  it("shows a 'behind' badge only on the lagging source", async () => {
    const user = userEvent.setup();
    render(<FreshnessDisclosure generatedAt={GENERATED_AT} sources={SOURCES} />);
    await user.click(screen.getByRole("button"));
    const badges = screen.getAllByText(/^behind$/i);
    expect(badges).toHaveLength(1);
    // The badge belongs to the lagging Strategy-checkpoints row.
    expect(badges[0].closest("li")?.textContent).toContain("Strategy checkpoints");
  });

  it("marks the trigger when any source is behind (accessible count)", () => {
    render(<FreshnessDisclosure generatedAt={GENERATED_AT} sources={SOURCES} />);
    const btn = screen.getByRole("button");
    expect(btn).toHaveClass("warn");
    expect(btn).toHaveAccessibleName(/1 behind the latest export/i);
  });

  it("does not mark the trigger when every source is fresh", () => {
    render(
      <FreshnessDisclosure
        generatedAt={GENERATED_AT}
        sources={[{ source: "Trial Registry", modified_at: "2026-06-28T17:52:48Z" }]}
      />,
    );
    const btn = screen.getByRole("button");
    expect(btn).not.toHaveClass("warn");
    expect(btn).toHaveAccessibleName("Per-source data freshness");
  });

  it("dismisses on Escape", async () => {
    const user = userEvent.setup();
    render(<FreshnessDisclosure generatedAt={GENERATED_AT} sources={SOURCES} />);
    await user.click(screen.getByRole("button"));
    expect(screen.getByRole("region")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("dismisses on an outside click", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <FreshnessDisclosure generatedAt={GENERATED_AT} sources={SOURCES} />
        <button type="button">outside</button>
      </div>,
    );
    await user.click(screen.getByRole("button", { name: /per-source/i }));
    expect(screen.getByRole("region")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "outside" }));
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("exposes no internal file paths (DECISIONS #5/#11)", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <FreshnessDisclosure generatedAt={GENERATED_AT} sources={SOURCES} />,
    );
    await user.click(screen.getByRole("button"));
    expect(container.textContent ?? "").not.toMatch(/\.json|\.yaml|data\//);
  });
});
