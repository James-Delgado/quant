import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Explanations } from "@/pages/Explanations";

describe("Explanations panel", () => {
  it("renders six serif reading cards", () => {
    const { container } = render(<Explanations />);
    // serif reading view (DECISIONS #4): each card carries `.panel.read` -> --serif.
    const cards = container.querySelectorAll(".panel.read");
    expect(cards.length).toBe(6);
    expect(screen.getAllByRole("heading", { level: 4 })).toHaveLength(6);
    expect(screen.getByText("Purge & embargo")).toBeInTheDocument();
    expect(screen.getByText("In-sample vs out-of-sample")).toBeInTheDocument();
  });

  it("renders the card bodies verbatim from the frozen mockup", () => {
    render(<Explanations />);
    expect(
      screen.getByText(/each is weighted by the unique portion of its window/),
    ).toBeInTheDocument();
    expect(screen.getByText(/it is why the trial registry is kept/)).toBeInTheDocument();
  });
});
