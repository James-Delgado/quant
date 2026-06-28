import { describe, expect, it } from "vitest";
import {
  areaPath,
  dateFraction,
  extent,
  heatFill,
  linePath,
  scaleX,
  scaleY,
} from "@/lib/chartGeometry";

describe("chartGeometry", () => {
  it("extent widens a degenerate or empty series", () => {
    expect(extent([1, 5, 3])).toEqual([1, 5]);
    expect(extent([2, 2])).toEqual([1, 3]);
    expect(extent([])).toEqual([0, 1]);
  });

  it("scaleX spreads points evenly and scaleY inverts the axis", () => {
    expect(scaleX(0, 5, 100)).toBe(0);
    expect(scaleX(4, 5, 100)).toBe(100);
    // max of the domain sits at the top (small y); min at the bottom.
    expect(scaleY(10, [0, 10], 100, 0)).toBeCloseTo(0);
    expect(scaleY(0, [0, 10], 100, 0)).toBeCloseTo(100);
  });

  it("linePath emits a moveto then linetos", () => {
    const d = linePath([0, 1], { width: 10, height: 10 });
    expect(d.startsWith("M")).toBe(true);
    expect(d).toContain("L");
  });

  it("areaPath closes back to the baseline", () => {
    const d = areaPath([0, -0.3, -0.1], { width: 10, height: 10, baseline: 0 });
    expect(d.endsWith("Z")).toBe(true);
  });

  it("dateFraction places a date inside a span and clamps", () => {
    expect(dateFraction("2015-01-01", "2010-01-01", "2020-01-01")).toBeCloseTo(
      0.5,
      1,
    );
    expect(dateFraction("2005-01-01", "2010-01-01", "2020-01-01")).toBe(0);
    expect(Number.isNaN(dateFraction("x", "2010", "2010"))).toBe(true);
  });

  it("heatFill ramps alpha by sign and magnitude", () => {
    expect(heatFill(null, 2)).toBe("transparent");
    expect(heatFill(2, 2)).toContain("91,214,164"); // positive -> green
    expect(heatFill(-2, 2)).toContain("242,113,90"); // negative -> red
  });
});
