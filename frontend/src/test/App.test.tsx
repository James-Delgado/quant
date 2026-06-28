import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "@/App";

const FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true } as const;

// AppShell does one real fetch (data_status). Stub it so routing tests are
// hermetic and don't depend on synced files.
beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({ asof: "2026-06-28", feeds: [] }),
    })),
  );
});
afterEach(() => vi.unstubAllGlobals());

function renderApp(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]} future={FUTURE}>
      <App />
    </MemoryRouter>,
  );
}

describe("App routing", () => {
  it("redirects the index route to Overview", async () => {
    renderApp("/");
    await waitFor(() =>
      expect(screen.getByText("Overview", { selector: ".ttl" })).toBeInTheDocument(),
    );
    const link = screen.getByRole("link", { name: /Overview/ });
    expect(link).toHaveClass("active");
  });

  it("navigates to another panel and updates the title + active state", async () => {
    const user = userEvent.setup();
    renderApp("/overview");
    await user.click(screen.getByRole("link", { name: /Provenance/ }));
    expect(screen.getByText("Provenance", { selector: ".ttl" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Provenance/ })).toHaveClass("active");
  });

  it("renders the real Explanations panel for the last nav slug (no scaffold left)", async () => {
    // E1-M5 swapped the final placeholder (explain) for the real reference panel,
    // so every nav slug now maps to a real page.
    renderApp("/explain");
    // findBy* flushes the AppShell data-status fetch inside act().
    expect(await screen.findByText("Explanations", { selector: ".h1" })).toBeInTheDocument();
    expect(screen.getByText("Purge & embargo")).toBeInTheDocument();
    expect(screen.queryByText(/scaffold/i)).not.toBeInTheDocument();
  });
});
