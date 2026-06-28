import { describe, expect, it } from "vitest";
import { fixed, pct, signClass, signedFixed, signedPct, yearSpan } from "@/lib/format";

describe("format helpers", () => {
  it("renders fixed decimals with a typographic minus", () => {
    expect(fixed(-0.34)).toBe("−0.34");
    expect(fixed(0.42)).toBe("0.42");
  });

  it("signedFixed always carries a leading sign", () => {
    expect(signedFixed(0.42)).toBe("+0.42");
    expect(signedFixed(-0.34)).toBe("−0.34");
    expect(signedFixed(0)).toBe("0.00");
    expect(signedFixed(Number.NaN)).toBe("—");
  });

  it("signedPct converts fractions to signed percentages", () => {
    expect(signedPct(0.418)).toBe("+41.8%");
    expect(signedPct(-0.211)).toBe("−21.1%");
    expect(pct(0.997)).toBe("99.7%");
  });

  it("signClass maps sign to a CSS class", () => {
    expect(signClass(1)).toBe("gain");
    expect(signClass(-1)).toBe("loss");
    expect(signClass(0)).toBe("");
  });

  it("yearSpan compacts an ISO date range", () => {
    expect(yearSpan("2004-06-20", "2026-03-30")).toBe("’04–’26");
    expect(yearSpan(null, "2026-03-30")).toBe("—");
  });
});
