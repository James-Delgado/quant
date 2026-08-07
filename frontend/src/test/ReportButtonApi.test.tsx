/**
 * Api-mode ReportButton behaviour (E2-M4): submission swaps the pre-filled
 * `issues/new` tab for `POST /feedback`, degrades to the URL path on 401, and
 * keeps the modal (and typed report) intact on other failures. Lives in its
 * own file because the `@/lib/dataClient` mock is module-wide — the
 * static-mode tests in ReportButton.test.tsx run against the real module.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReportButton } from "@/components/layout/ReportButton";

vi.mock("@/lib/dataClient", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/dataClient")>();
  return {
    ...original,
    DATA_SOURCE: "api" as const,
    API_BASE: "http://127.0.0.1:8000",
    API_TOKEN: "tok",
  };
});

const FUTURE = {
  v7_startTransition: true,
  v7_relativeSplatPath: true,
} as const;

function renderButton() {
  return render(
    <MemoryRouter initialEntries={["/overview"]} future={FUTURE}>
      <ReportButton />
    </MemoryRouter>,
  );
}

async function openAndFill(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /report an issue/i }));
  await user.type(screen.getByLabelText("Title"), "Broken chart");
  await user.type(screen.getByLabelText("Description"), "It rendered wrong.");
}

let openSpy: ReturnType<typeof vi.fn>;
beforeEach(() => {
  openSpy = vi.fn(() => null);
  vi.stubGlobal("open", openSpy);
});
afterEach(() => vi.unstubAllGlobals());

describe("ReportButton (api mode)", () => {
  it("POSTs to /feedback with the bearer token and toasts the issue number", async () => {
    const fetchSpy = vi.fn(async () => ({
      ok: true,
      status: 201,
      json: async () => ({
        issue_url: "https://github.com/James-Delgado/quant/issues/7",
        issue_number: 7,
        promoted: false,
      }),
    }));
    vi.stubGlobal("fetch", fetchSpy);

    const user = userEvent.setup();
    renderButton();
    await openAndFill(user);
    await user.click(screen.getByRole("button", { name: "Submit" }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/issue #7 filed/i),
    );
    // One-click server-side filing: no new-tab fallback fired.
    expect(openSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    const [url, init] = fetchSpy.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("http://127.0.0.1:8000/feedback");
    expect(init.headers).toHaveProperty("Authorization", "Bearer tok");
    const body = JSON.parse(init.body as string) as Record<string, string>;
    expect(body.title).toBe("Broken chart");
    expect(body.panel).toBe("Overview");
    expect(body).toHaveProperty("build_sha");
    expect(body).toHaveProperty("app_version");
  });

  it("degrades to the pre-filled issue tab on 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 401,
        json: async () => ({ detail: "invalid token" }),
      })),
    );

    const user = userEvent.setup();
    renderButton();
    await openAndFill(user);
    await user.click(screen.getByRole("button", { name: "Submit" }));

    await waitFor(() => expect(openSpy).toHaveBeenCalledTimes(1));
    const [url] = openSpy.mock.calls[0];
    expect(new URL(url as string).pathname.endsWith("/issues/new")).toBe(true);
    expect(screen.getByRole("status")).toHaveTextContent(/auth failed/i);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps the modal and typed report on a non-auth failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 502,
        json: async () => ({ detail: "GitHub issue creation failed" }),
      })),
    );

    const user = userEvent.setup();
    renderButton();
    await openAndFill(user);
    await user.click(screen.getByRole("button", { name: "Submit" }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/failed to file/i),
    );
    // Honest failure: no fallback tab, report still there for a retry.
    expect(openSpy).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByLabelText("Title")).toHaveValue("Broken chart");
    // Focus returns inside the dialog (the disabled in-flight Submit dropped it).
    expect(screen.getByLabelText("Title")).toHaveFocus();
  });
});
