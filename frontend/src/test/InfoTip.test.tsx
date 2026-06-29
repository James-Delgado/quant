import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { InfoTip } from "@/components/ui/InfoTip";

const TIP = "Risk-adjusted return over its volatility, annualized.";

describe("InfoTip", () => {
  it("carries the tip accessibly (aria-label) and on data-tip for the hover bubble", () => {
    render(<InfoTip label="Sharpe" tip={TIP} />);
    // hover surface = the CSS `::after` reading data-tip; AT surface = aria-label.
    const btn = screen.getByRole("button", { name: `Sharpe: ${TIP}` });
    expect(btn).toHaveClass("info");
    expect(btn).toHaveAttribute("data-tip", TIP);
    expect(btn).toHaveAttribute("aria-expanded", "false");
  });

  it("pins on click and toggles the .open state", async () => {
    const user = userEvent.setup();
    render(<InfoTip label="Sharpe" tip={TIP} />);
    const btn = screen.getByRole("button");
    await user.click(btn);
    expect(btn).toHaveClass("open");
    expect(btn).toHaveAttribute("aria-expanded", "true");
    await user.click(btn);
    expect(btn).not.toHaveClass("open");
  });

  it("closes a pinned tip on Escape", async () => {
    const user = userEvent.setup();
    render(<InfoTip label="Sharpe" tip={TIP} />);
    const btn = screen.getByRole("button");
    await user.click(btn);
    expect(btn).toHaveClass("open");
    await user.keyboard("{Escape}");
    expect(btn).not.toHaveClass("open");
  });

  it("is keyboard operable: focusable and pinned with Enter", async () => {
    const user = userEvent.setup();
    render(<InfoTip label="Sharpe" tip={TIP} />);
    const btn = screen.getByRole("button");
    await user.tab();
    expect(btn).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(btn).toHaveClass("open");
  });

  it("dismisses on an outside click", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <InfoTip label="Sharpe" tip={TIP} />
        <button type="button">elsewhere</button>
      </div>,
    );
    const tip = screen.getByRole("button", { name: /Sharpe:/ });
    await user.click(tip);
    expect(tip).toHaveClass("open");
    await user.click(screen.getByText("elsewhere"));
    expect(tip).not.toHaveClass("open");
  });

  it("keeps only one tip open at a time (opening one closes the others)", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <InfoTip label="Sharpe" tip="first definition" />
        <InfoTip label="Drawdown" tip="second definition" />
      </div>,
    );
    const first = screen.getByRole("button", { name: /Sharpe:/ });
    const second = screen.getByRole("button", { name: /Drawdown:/ });
    await user.click(first);
    expect(first).toHaveClass("open");
    await user.click(second);
    expect(second).toHaveClass("open");
    expect(first).not.toHaveClass("open");
  });
});
