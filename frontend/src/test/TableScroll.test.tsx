import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TableScroll } from "@/components/ui/TableScroll";

/**
 * jsdom computes no layout, so scroll metrics read 0 unless we define them.
 * Redefine the three the affordance measures, then fire a scroll so the
 * component re-measures (the production triggers are scroll / resize / RO).
 */
function setGeometry(
  el: HTMLElement,
  geom: { scrollWidth: number; clientWidth: number; scrollLeft: number },
) {
  Object.defineProperty(el, "scrollWidth", {
    configurable: true,
    value: geom.scrollWidth,
  });
  Object.defineProperty(el, "clientWidth", {
    configurable: true,
    value: geom.clientWidth,
  });
  Object.defineProperty(el, "scrollLeft", {
    configurable: true,
    value: geom.scrollLeft,
  });
  fireEvent.scroll(el);
}

function renderScroll() {
  render(
    <TableScroll label="Demo table">
      <table>
        <tbody>
          <tr>
            <td>cell</td>
          </tr>
        </tbody>
      </table>
    </TableScroll>,
  );
  const region = screen.getByRole("region", { name: "Demo table" });
  const wrap = region.parentElement as HTMLElement;
  return { region, wrap };
}

describe("TableScroll affordance", () => {
  it("shows no edge cue when the table fits its container", () => {
    const { region, wrap } = renderScroll();
    setGeometry(region, { scrollWidth: 200, clientWidth: 200, scrollLeft: 0 });
    expect(wrap).not.toHaveAttribute("data-scroll-left");
    expect(wrap).not.toHaveAttribute("data-scroll-right");
  });

  it("cues the right edge when columns overflow and the view is at the start", () => {
    const { region, wrap } = renderScroll();
    setGeometry(region, { scrollWidth: 400, clientWidth: 200, scrollLeft: 0 });
    expect(wrap).toHaveAttribute("data-scroll-right");
    expect(wrap).not.toHaveAttribute("data-scroll-left");
  });

  it("cues both edges mid-scroll, then only the left edge at the end", () => {
    const { region, wrap } = renderScroll();
    setGeometry(region, {
      scrollWidth: 400,
      clientWidth: 200,
      scrollLeft: 100,
    });
    expect(wrap).toHaveAttribute("data-scroll-left");
    expect(wrap).toHaveAttribute("data-scroll-right");

    // Scrolled fully right: no more off-screen columns on the right.
    setGeometry(region, {
      scrollWidth: 400,
      clientWidth: 200,
      scrollLeft: 200,
    });
    expect(wrap).toHaveAttribute("data-scroll-left");
    expect(wrap).not.toHaveAttribute("data-scroll-right");
  });

  it("keeps the scroll region accessible and the cues decorative", () => {
    const { region, wrap } = renderScroll();
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region).toHaveAttribute("aria-label", "Demo table");
    const cues = wrap.querySelectorAll(".table-scroll-cue");
    expect(cues).toHaveLength(2);
    cues.forEach((cue) => expect(cue).toHaveAttribute("aria-hidden", "true"));
  });
});
