import type { Histogram as Hist } from "@/types/viewmodels";

interface HistogramProps {
  hist: Hist;
  height?: number;
  width?: number;
  ariaLabel: string;
}

/**
 * Daily-return distribution: bars colored by the sign of each bin (loss left,
 * gain right) with a vertical axis at zero — the mockup's left-skew read.
 */
export function Histogram({ hist, height = 150, width = 480, ariaLabel }: HistogramProps) {
  const { bin_edges, counts } = hist;
  if (!counts?.length || bin_edges.length !== counts.length + 1) {
    return <span className="dim small">no distribution data</span>;
  }
  const lo = bin_edges[0];
  const hi = bin_edges[bin_edges.length - 1];
  const span = hi - lo || 1;
  const maxCount = Math.max(...counts, 1);
  const plotH = height - 15; // leave room for the 0% tick
  const xOf = (v: number) => ((v - lo) / span) * width;
  const zeroX = xOf(0);

  return (
    <svg
      className="chart"
      viewBox={`0 0 ${width} ${height}`}
      height={height}
      preserveAspectRatio="none"
      role="img"
      aria-label={ariaLabel}
    >
      <line className="axis" x1={zeroX} y1={0} x2={zeroX} y2={plotH} />
      {counts.map((c, i) => {
        const x0 = xOf(bin_edges[i]);
        const x1 = xOf(bin_edges[i + 1]);
        const h = (c / maxCount) * plotH;
        const center = (bin_edges[i] + bin_edges[i + 1]) / 2;
        return (
          <rect
            key={i}
            x={x0 + 0.5}
            y={plotH - h}
            width={Math.max(0, x1 - x0 - 1)}
            height={h}
            fill={center < 0 ? "var(--loss)" : "var(--gain)"}
          />
        );
      })}
      <text x={zeroX} y={height - 3} textAnchor="middle">
        0%
      </text>
    </svg>
  );
}
