import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReportButton } from "@/components/layout/ReportButton";

const FUTURE = {
  v7_startTransition: true,
  v7_relativeSplatPath: true,
} as const;

function renderButton(path = "/provenance") {
  return render(
    <MemoryRouter initialEntries={[path]} future={FUTURE}>
      <ReportButton />
    </MemoryRouter>,
  );
}

let openSpy: ReturnType<typeof vi.fn>;
beforeEach(() => {
  openSpy = vi.fn(() => null);
  vi.stubGlobal("open", openSpy);
});
afterEach(() => vi.unstubAllGlobals());

describe("ReportButton", () => {
  it("renders an enabled trigger and no dialog until opened", () => {
    renderButton();
    const trigger = screen.getByRole("button", { name: /report an issue/i });
    expect(trigger).toBeEnabled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens the capture modal with labelled fields", async () => {
    const user = userEvent.setup();
    renderButton();
    await user.click(screen.getByRole("button", { name: /report an issue/i }));
    const dialog = screen.getByRole("dialog", { name: /report an issue/i });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByLabelText("Title")).toBeInTheDocument();
    expect(screen.getByLabelText("Type")).toBeInTheDocument();
    expect(screen.getByLabelText("Severity")).toBeInTheDocument();
    expect(screen.getByLabelText("Description")).toBeInTheDocument();
  });

  it("auto-captures the current panel as context", async () => {
    const user = userEvent.setup();
    renderButton("/provenance");
    await user.click(screen.getByRole("button", { name: /report an issue/i }));
    // The Provenance route title is captured into the context line + build SHA.
    expect(screen.getByText(/Context: Provenance/)).toBeInTheDocument();
    expect(screen.getByText(/build /)).toBeInTheDocument();
  });

  it("closes on Escape and restores focus to the trigger", async () => {
    const user = userEvent.setup();
    renderButton();
    const trigger = screen.getByRole("button", { name: /report an issue/i });
    await user.click(trigger);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("closes on Cancel", async () => {
    const user = userEvent.setup();
    renderButton();
    await user.click(screen.getByRole("button", { name: /report an issue/i }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps Submit disabled until a title is entered", async () => {
    const user = userEvent.setup();
    renderButton();
    await user.click(screen.getByRole("button", { name: /report an issue/i }));
    const submit = screen.getByRole("button", { name: "Submit" });
    expect(submit).toBeDisabled();
    await user.type(screen.getByLabelText("Title"), "Broken chart");
    expect(submit).toBeEnabled();
  });

  it("opens a prefilled GitHub issue on Submit and toasts", async () => {
    const user = userEvent.setup();
    renderButton("/provenance");
    await user.click(screen.getByRole("button", { name: /report an issue/i }));
    await user.type(screen.getByLabelText("Title"), "Broken chart");
    await user.type(screen.getByLabelText("Description"), "It rendered wrong.");
    await user.click(screen.getByRole("button", { name: "Submit" }));

    expect(openSpy).toHaveBeenCalledTimes(1);
    const [url, target] = openSpy.mock.calls[0];
    const parsed = new URL(url as string);
    expect(parsed.pathname.endsWith("/issues/new")).toBe(true);
    expect(parsed.searchParams.get("labels")).toBe("feedback");
    expect(parsed.searchParams.get("title")).toBe("Broken chart");
    expect(parsed.searchParams.get("body")).toContain("Panel: Provenance");
    expect(target).toBe("_blank");

    // Modal closes and a confirmation toast appears (no tracker panel).
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/new tab/i);
  });

  it("renders no persistent tracker surface — only the button (DECISIONS #11)", () => {
    renderButton();
    // Closed state: just the trigger, nothing list/tracker-like.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(screen.queryByText(/tracker/i)).not.toBeInTheDocument();
  });
});
