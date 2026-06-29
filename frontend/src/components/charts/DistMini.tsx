interface DistMiniProps {
  /** Bin counts (already binned by the service layer); null/empty => pending. */
  values: number[] | null;
  width?: number;
  height?: number;
  ariaLabel: string;
}

/**
 * Tiny distribution sparkbar for the Feature Catalog (mockup class `.dist`).
 * Bespoke SVG, not a charting lib (E1-M3 decision). Renders an honest dim dash
 * when the monitor has not produced a distribution yet — never a fake shape.
 */
export function DistMini({
  values,
  width = 58,
  height = 20,
  ariaLabel,
}: DistMiniProps) {
  if (!values || values.length === 0) {
    return (
      <span className="dim small" aria-label={`${ariaLabel} — pending`}>
        —
      </span>
    );
  }
  const max = Math.max(...values, 1);
  const n = values.length;
  const gap = 3;
  const bw = Math.max(2, (width - gap * (n - 1)) / n);
  return (
    <svg
      className="dist"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={ariaLabel}
    >
      {values.map((v, i) => {
        const h = Math.max(1, (v / max) * height);
        return (
          <rect
            key={i}
            x={i * (bw + gap)}
            y={height - h}
            width={bw}
            height={h}
          />
        );
      })}
    </svg>
  );
}
