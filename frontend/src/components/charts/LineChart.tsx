import { areaPath, extent, linePath, scaleY } from "@/lib/chartGeometry";

export interface ChartSeries {
  values: number[];
  /** Path CSS class — e.g. "ln-port", "ln-bench", "dd-area". */
  className: string;
  /** Inline stroke override (per-strategy tone). */
  stroke?: string;
  /** Render as a closed area to the baseline instead of a polyline. */
  area?: boolean;
}

export interface StressBand {
  /** Fractional x bounds (0..1) across the plot width. */
  x0: number;
  x1: number;
}

export interface AxisTick {
  /** Fractional x position (0..1). */
  at: number;
  label: string;
}

interface LineChartProps {
  series: ChartSeries[];
  height: number;
  width?: number;
  /** Horizontal hairline count. */
  gridLines?: number;
  /** Draw a heavier axis line at this data value (e.g. 0 for rolling Sharpe). */
  zeroAxisAt?: number;
  stressBands?: StressBand[];
  xTicks?: AxisTick[];
  /** Corner y-labels, e.g. +1 / −1 on a rolling-Sharpe panel. */
  yCornerLabels?: { top?: string; bottom?: string };
  ariaLabel: string;
}

/**
 * Bespoke instrument line/area chart: hairline grid, optional stress shading,
 * optional zero axis, and one or more series over a shared y-domain so overlaid
 * lines (portfolio vs control) stay comparable. Scales to its container via the
 * `.chart` width:100% rule + the viewBox.
 */
export function LineChart({
  series,
  height,
  width = 640,
  gridLines = 3,
  zeroAxisAt,
  stressBands = [],
  xTicks = [],
  yCornerLabels,
  ariaLabel,
}: LineChartProps) {
  const padY = 4;
  const anchorValues = series.flatMap((s) => s.values);
  if (series.some((s) => s.area)) anchorValues.push(0);
  const domain = extent(anchorValues);

  const grid = Array.from(
    { length: gridLines },
    (_, i) => ((i + 1) / (gridLines + 1)) * height,
  );

  return (
    <svg
      className="chart"
      viewBox={`0 0 ${width} ${height}`}
      height={height}
      preserveAspectRatio="none"
      role="img"
      aria-label={ariaLabel}
    >
      {stressBands.map((b, i) => (
        <rect
          key={`band-${i}`}
          className="band-stress"
          x={b.x0 * width}
          y={0}
          width={Math.max(0, (b.x1 - b.x0) * width)}
          height={height}
        />
      ))}
      {grid.map((y, i) => (
        <line
          key={`grid-${i}`}
          className="grid-l"
          x1={0}
          y1={y}
          x2={width}
          y2={y}
        />
      ))}
      {zeroAxisAt !== undefined && (
        <line
          className="axis"
          x1={0}
          y1={scaleY(zeroAxisAt, domain, height, padY)}
          x2={width}
          y2={scaleY(zeroAxisAt, domain, height, padY)}
        />
      )}
      {series.map((s, i) => (
        <path
          key={`s-${i}`}
          className={s.className}
          stroke={s.stroke}
          d={
            s.area
              ? areaPath(s.values, { width, height, padY, domain })
              : linePath(s.values, { width, height, padY, domain })
          }
        />
      ))}
      {xTicks.map((t, i) => (
        <text
          key={`xt-${i}`}
          x={t.at <= 0 ? 2 : t.at >= 1 ? width - 2 : t.at * width}
          y={height - 5}
          textAnchor={t.at <= 0 ? "start" : t.at >= 1 ? "end" : "middle"}
        >
          {t.label}
        </text>
      ))}
      {yCornerLabels?.top && (
        <text x={2} y={12}>
          {yCornerLabels.top}
        </text>
      )}
      {yCornerLabels?.bottom && (
        <text x={2} y={height - 4}>
          {yCornerLabels.bottom}
        </text>
      )}
    </svg>
  );
}
