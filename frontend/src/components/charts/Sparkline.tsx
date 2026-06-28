import { linePath } from "@/lib/chartGeometry";

export type Tone = "gain" | "loss" | "steel" | "series";

const TONE_STROKE: Record<Tone, string> = {
  gain: "var(--gain)",
  loss: "var(--loss)",
  steel: "var(--steel)",
  series: "var(--series)",
};

/** Inline trajectory sparkline for dense tables (mockup: 64×18, 1.4px stroke). */
export function Sparkline({ values, tone = "steel" }: { values: number[]; tone?: Tone }) {
  const width = 64;
  const height = 18;
  if (values.length < 2) return <span className="dim small">—</span>;
  const d = linePath(values, { width, height, padY: 2 });
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path className="spark" stroke={TONE_STROKE[tone]} d={d} />
    </svg>
  );
}
