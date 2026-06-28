import { fixed } from "@/lib/format";

interface LuckBarProps {
  /** Multiple-testing threshold: best Sharpe expected under the no-skill null. */
  luck: number;
  /** Best observed out-of-sample Sharpe to date. */
  best: number | null;
  width?: number;
  height?: number;
}

/**
 * Deflation "luck bar" gauge (Trial Registry). A horizontal track scaled to the
 * luck threshold; a fill marks the best observed Sharpe and a vertical marker
 * sits at the luck bar. The fill reads `loss` when best fails to clear the bar
 * and `gain` when it clears — the whole point of the registry (PRD §5).
 */
export function LuckBar({ luck, best, width = 320, height = 22 }: LuckBarProps) {
  // Scale so the luck bar sits at ~70% of the track, leaving headroom above it.
  const scaleMax = Math.max(luck, best ?? 0) * 1.25 || 1;
  const xOf = (v: number) => Math.max(0, Math.min(1, v / scaleMax)) * width;
  const clears = best != null && best >= luck;
  const markX = xOf(luck);
  const fillW = best != null ? xOf(best) : 0;
  const barY = 4;
  const barH = height - 8;

  return (
    <svg
      className="luckbar"
      width="100%"
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`Best observed Sharpe ${best == null ? "n/a" : fixed(best, 2)} versus luck bar ${fixed(luck, 2)}`}
    >
      <rect className="track" x={0} y={barY} width={width} height={barH} rx={2} />
      {best != null && (
        <rect
          className="fill"
          x={0}
          y={barY}
          width={fillW}
          height={barH}
          rx={2}
          fill={clears ? "var(--gain)" : "var(--loss)"}
        />
      )}
      <line className="mark" x1={markX} y1={0} x2={markX} y2={height} />
    </svg>
  );
}
