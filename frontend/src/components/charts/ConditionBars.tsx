interface BarItem {
  label: string;
  value: number;
  sub?: string;
}

interface ConditionBarsProps {
  items: BarItem[];
  height?: number;
  width?: number;
  ariaLabel: string;
}

/**
 * Signed bar chart around a central zero axis — Sharpe by market condition.
 * Positive bars rise (gain), negative bars fall (loss). One bar per condition,
 * from the single walk-forward Sharpe series the export provides.
 */
export function ConditionBars({ items, height = 180, width = 460, ariaLabel }: ConditionBarsProps) {
  if (!items.length) return <span className="dim small">no condition data</span>;
  const midY = height / 2;
  const half = midY - 28; // headroom for labels
  const maxAbs = Math.max(...items.map((d) => Math.abs(d.value)), 0.001);
  const slot = width / items.length;
  const barW = Math.min(34, slot * 0.5);

  return (
    <svg
      className="chart"
      viewBox={`0 0 ${width} ${height}`}
      height={height}
      role="img"
      aria-label={ariaLabel}
    >
      <line className="axis" x1={0} y1={midY} x2={width} y2={midY} />
      {items.map((d, i) => {
        const cx = slot * (i + 0.5);
        const h = (Math.abs(d.value) / maxAbs) * half;
        const positive = d.value >= 0;
        return (
          <g key={d.label}>
            <rect
              className={positive ? "bar-pos" : "bar-neg"}
              x={cx - barW / 2}
              y={positive ? midY - h : midY}
              width={barW}
              height={h}
            />
            <text x={cx} y={height - 6} textAnchor="middle">
              {d.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
