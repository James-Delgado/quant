import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { Sidebar } from "@/components/layout/Sidebar";
import { NAV_ITEMS } from "@/nav";

const FUTURE = {
  v7_startTransition: true,
  v7_relativeSplatPath: true,
} as const;

function renderSidebar() {
  return render(
    <MemoryRouter initialEntries={["/overview"]} future={FUTURE}>
      <Sidebar />
    </MemoryRouter>,
  );
}

describe("Sidebar", () => {
  it("renders the three nav groups with 'Evidence' (not 'Trust')", () => {
    renderSidebar();
    expect(screen.getByText("Monitor")).toBeInTheDocument();
    expect(screen.getByText("Evidence")).toBeInTheDocument();
    expect(screen.getByText("Reference")).toBeInTheDocument();
  });

  it("renders every navigation item as a link", () => {
    renderSidebar();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    for (const item of NAV_ITEMS) {
      expect(
        within(nav).getByRole("link", { name: new RegExp(item.label) }),
      ).toBeInTheDocument();
    }
  });

  it("uses no 'trust' language anywhere (DECISIONS #5)", () => {
    const { container } = renderSidebar();
    expect(container.textContent ?? "").not.toMatch(/trust/i);
  });

  it("exposes no internal file paths (DECISIONS #5/#11)", () => {
    const { container } = renderSidebar();
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/\.yaml/);
    expect(text).not.toMatch(/data\//);
    expect(text).not.toMatch(/ledger\.yaml|catalog\.yaml/);
  });

  it("shows the build footer and the report-issue entry point", () => {
    renderSidebar();
    expect(screen.getByText(/^build /)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /report an issue/i }),
    ).toBeInTheDocument();
  });
});
