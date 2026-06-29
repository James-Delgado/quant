import { dataClient } from "@/lib/dataClient";
import { useAsyncData } from "@/hooks/useAsyncData";
import { ErrorState, Loading } from "@/components/ui/StatePanel";
import { ConditionBars } from "@/components/charts/ConditionBars";
import { Heatmap } from "@/components/charts/Heatmap";
import { TableScroll } from "@/components/ui/TableScroll";
import { fixed, signClass, yearSpan } from "@/lib/format";

/** Render an export condition key for display: "low_vol" -> "low-vol". */
function condLabel(key: string): string {
  return key.replace(/_/g, "-");
}

export function Conditions() {
  const state = useAsyncData((signal) => dataClient.conditions(signal), []);

  return (
    <section>
      <div className="h1">Condition attribution</div>
      <div className="lead">
        Where each strategy earns or bleeds, by market condition. Axes are
        live-computable, point-in-time market conditions — volatility (VIX),
        trend (benchmark vs its 200-day moving average), and rates (10-year
        Treasury) — aligned to the out-of-sample calendar. Named historical
        episodes are kept separately as stress windows.
      </div>

      {state.status === "loading" && <Loading label="Loading conditions…" />}
      {state.status === "error" && <ErrorState error={state.error} />}
      {state.status === "ready" && (
        <>
          <div className="grid c2" style={{ marginTop: 20 }}>
            <div className="panel">
              <div className="phead">
                <span className="t">Sharpe by condition</span>
                <span className="s">deployable candidate</span>
              </div>
              <ConditionBars
                ariaLabel="Walk-forward Sharpe by market condition"
                items={state.data.by_condition.map((c) => ({
                  label: condLabel(c.condition),
                  value: c.sharpe,
                }))}
              />
              <div className="legend">
                <span>
                  <i className="swatch" style={{ background: "var(--gain)" }} />{" "}
                  positive Sharpe
                </span>
                <span>
                  <i className="swatch" style={{ background: "var(--loss)" }} />{" "}
                  negative Sharpe
                </span>
              </div>
            </div>

            <div className="panel">
              <div className="phead">
                <span className="t">Strategy × condition</span>
                <span className="s">Sharpe</span>
              </div>
              <Heatmap heatmap={state.data.heatmap} />
            </div>
          </div>

          <div className="sec">
            Stress windows{" "}
            <span className="dim">— named historical episodes</span>
            <span className="ln" />
          </div>
          <div className="panel flush">
            <TableScroll label="Stress windows by Sharpe">
              <table>
                <thead>
                  <tr>
                    <th>Episode</th>
                    <th>Window</th>
                    <th className="num">Sharpe</th>
                    <th className="num">Bars</th>
                  </tr>
                </thead>
                <tbody>
                  {state.data.stress_windows.map((w) => (
                    <tr key={w.name}>
                      <td>{w.name}</td>
                      <td className="num dim">{yearSpan(w.start, w.end)}</td>
                      <td
                        className={`num ${w.sharpe == null ? "dim" : signClass(w.sharpe)}`}
                      >
                        {w.sharpe == null ? "—" : fixed(w.sharpe, 2)}
                      </td>
                      <td className="num dim">{w.n_bars}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          </div>
        </>
      )}
    </section>
  );
}
