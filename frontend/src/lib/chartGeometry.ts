/**
 * Pure SVG geometry for the instrument charts.
 *
 * The console's charting "library" is a small set of bespoke SVG primitives
 * (decided in E1-M3): the frozen mockup is hand-authored SVG in a specific
 * instrument language (hairline grids, mono ticks, stress shading), and the
 * service layer already emits neutral chart-ready series — so a heavyweight
 * charting dependency would fight the design and blow the bundle budget for
 * what are line/area/bar/histogram primitives. Keeping the math here, pure and
 * unit-tested, also keeps the choice reversible: a later swap to Plotly/ECharts
 * is presentation-only because nothing upstream changes.
 */

export type Domain = [min: number, max: number];

/** [min, max] of a series, widened to a unit band when degenerate/empty. */
export function extent(values: number[]): Domain {
  let min = Infinity;
  let max = -Infinity;
  for (const v of values) {
    if (Number.isFinite(v)) {
      if (v < min) min = v;
      if (v > max) max = v;
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  if (min === max) return [min - 1, max + 1];
  return [min, max];
}

/** Even horizontal placement of the i-th of n points across [0, width]. */
export function scaleX(i: number, n: number, width: number): number {
  if (n <= 1) return 0;
  return (i / (n - 1)) * width;
}

/** Map a value to a y pixel (SVG y grows downward; max sits at padY). */
export function scaleY(
  value: number,
  domain: Domain,
  height: number,
  padY = 0,
): number {
  const [min, max] = domain;
  const span = max - min || 1;
  const t = (value - min) / span; // 0 at min, 1 at max
  return height - padY - t * (height - 2 * padY);
}

export interface LineOpts {
  width: number;
  height: number;
  padY?: number;
  /** Explicit y-domain — pass a shared one to align overlaid series. */
  domain?: Domain;
}

/** SVG path `d` for a polyline over an evenly-spaced numeric series. */
export function linePath(values: number[], opts: LineOpts): string {
  const { width, height, padY = 0 } = opts;
  if (values.length === 0) return "";
  const domain = opts.domain ?? extent(values);
  return values
    .map((v, i) => {
      const x = scaleX(i, values.length, width).toFixed(2);
      const y = scaleY(v, domain, height, padY).toFixed(2);
      return `${i === 0 ? "M" : "L"}${x},${y}`;
    })
    .join(" ");
}

/**
 * Closed "underwater" area between the series and a baseline value (default 0).
 * Used for the drawdown chart, where values are <= 0 and the baseline is the top.
 */
export function areaPath(
  values: number[],
  opts: LineOpts & { baseline?: number },
): string {
  const { width, height, padY = 0, baseline = 0 } = opts;
  if (values.length === 0) return "";
  const domain = opts.domain ?? extent([...values, baseline]);
  const line = linePath(values, { ...opts, domain });
  const yBase = scaleY(baseline, domain, height, padY).toFixed(2);
  const xN = scaleX(values.length - 1, values.length, width).toFixed(2);
  const x0 = scaleX(0, values.length, width).toFixed(2);
  return `${line} L${xN},${yBase} L${x0},${yBase} Z`;
}

/** Fractional position (0..1) of an ISO date inside an ISO [start, end] span. */
export function dateFraction(date: string, start: string, end: string): number {
  const t = Date.parse(date);
  const a = Date.parse(start);
  const b = Date.parse(end);
  if (
    !Number.isFinite(t) ||
    !Number.isFinite(a) ||
    !Number.isFinite(b) ||
    b <= a
  ) {
    return Number.NaN;
  }
  return Math.min(1, Math.max(0, (t - a) / (b - a)));
}

/**
 * Heatmap cell fill: green for positive, red for negative, alpha scaled by
 * |value| / maxAbs (floored so a non-zero cell is always faintly visible).
 * Mirrors the mockup's inline rgba() ramp.
 */
export function heatFill(value: number | null, maxAbs: number): string {
  if (value === null || Number.isNaN(value) || maxAbs <= 0)
    return "transparent";
  const alpha = Math.min(0.9, 0.12 + (Math.abs(value) / maxAbs) * 0.78);
  const rgb = value >= 0 ? "91,214,164" : "242,113,90";
  return `rgba(${rgb},${alpha.toFixed(2)})`;
}
