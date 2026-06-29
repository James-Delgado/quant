import { heatFill } from "@/lib/chartGeometry";
import type { ConditionHeatmap } from "@/types/viewmodels";
import { fixed } from "@/lib/format";

/** Strategy × condition Sharpe grid; cell fill ramps with magnitude + sign. */
export function Heatmap({ heatmap }: { heatmap: ConditionHeatmap }) {
  const { strategies, conditions, values } = heatmap;
  const maxAbs = Math.max(
    ...values.flat().map((v) => (v === null ? 0 : Math.abs(v))),
    0.001,
  );
  return (
    <table className="heat">
      <thead>
        <tr>
          <th />
          {conditions.map((c) => (
            <th key={c}>{c.replace(/_/g, "-")}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {strategies.map((s, r) => (
          <tr key={s}>
            <td className="lbl">{s}</td>
            {conditions.map((c, col) => {
              const v = values[r]?.[col] ?? null;
              return (
                <td key={c} style={{ background: heatFill(v, maxAbs) }}>
                  {v === null ? "—" : fixed(v, 2)}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
