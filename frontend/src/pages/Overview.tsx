import { useNavigate } from "react-router-dom";
import { dataClient } from "@/lib/dataClient";
import { useAsyncData } from "@/hooks/useAsyncData";
import { Figure } from "@/components/ui/Figure";
import { InfoTip } from "@/components/ui/InfoTip";
import { TableScroll } from "@/components/ui/TableScroll";
import { ErrorState, Loading } from "@/components/ui/StatePanel";
import {
  LineChart,
  type ChartSeries,
  type StressBand,
} from "@/components/charts/LineChart";
import { Sparkline, type Tone } from "@/components/charts/Sparkline";
import { dateFraction } from "@/lib/chartGeometry";
import { signClass, signedFixed, signedPct, yearSpan } from "@/lib/format";
import type { StrategyCard } from "@/types/viewmodels";

/** Sparkline tone from a Sharpe sign. */
function tone(sharpe: number): Tone {
  if (sharpe > 0) return "gain";
  if (sharpe < 0) return "loss";
  return "steel";
}

/** Map a feed's status string to a pill variant (honest: stale reads as warn). */
function feedPill(status: string): "ok" | "warn" | "bad" {
  const s = status.toLowerCase();
  if (s.includes("ok") || s.includes("fresh")) return "ok";
  if (s.includes("stale") || s.includes("lag") || s.includes("warn"))
    return "warn";
  return "bad";
}

/** The deployable candidate = the highest-Sharpe arm on the roster. */
function pickCandidate(rows: StrategyCard[]): StrategyCard | null {
  if (!rows.length) return null;
  return rows.reduce((best, r) => (r.sharpe > best.sharpe ? r : best), rows[0]);
}

export function Overview() {
  const navigate = useNavigate();
  const state = useAsyncData(async (signal) => {
    const [strategies, conditions, dataStatus, market, portfolio] =
      await Promise.all([
        dataClient.strategies(signal),
        dataClient.conditions(signal),
        dataClient.dataStatus(signal),
        dataClient.market(signal),
        dataClient.portfolio(signal),
      ]);
    return { strategies, conditions, dataStatus, market, portfolio };
  }, []);

  const banner = (
    <div className="banner">
      <b>Research mode.</b> Live execution connects in a later phase — strategy
      performance below is from the walk-forward backtest. Live P&amp;L and
      intraday market data activate once the execution layer is online.
    </div>
  );

  if (state.status === "loading") {
    return (
      <section>
        {banner}
        <Loading label="Loading overview…" />
      </section>
    );
  }
  if (state.status === "error") {
    return (
      <section>
        {banner}
        <ErrorState error={state.error} />
      </section>
    );
  }

  const rows = Array.isArray(state.data.strategies)
    ? state.data.strategies
    : [];
  const candidate = pickCandidate(rows);
  const stressWindows = state.data.conditions?.stress_windows ?? [];
  const feeds = state.data.dataStatus?.feeds ?? [];
  const market = state.data.market;
  const portfolio = state.data.portfolio;

  const bands: StressBand[] =
    candidate && candidate.oos_start && candidate.oos_end
      ? stressWindows
          .map((w) => ({
            x0: dateFraction(w.start, candidate.oos_start!, candidate.oos_end!),
            x1: dateFraction(w.end, candidate.oos_start!, candidate.oos_end!),
          }))
          .filter(
            (b) =>
              Number.isFinite(b.x0) && Number.isFinite(b.x1) && b.x1 > b.x0,
          )
      : [];

  // Overlay the SPY buy-and-hold benchmark when the export carries it (same OOS
  // span, downsampled to the same points so it aligns index-for-index). When it
  // is absent the hero stays candidate-only with an honest note — never faked.
  const hasBenchmark = (candidate?.benchmark_sparkline?.length ?? 0) > 0;
  const heroSeries: ChartSeries[] = candidate
    ? [
        { values: candidate.sparkline, className: "ln-port" },
        ...(hasBenchmark
          ? [{ values: candidate.benchmark_sparkline, className: "ln-bench" }]
          : []),
      ]
    : [];

  return (
    <section>
      {banner}

      <div className="grid hero">
        <div className="panel">
          <div className="phead">
            <span className="t">Portfolio performance</span>
            <span className="s">
              deployable candidate · walk-forward backtest
            </span>
          </div>
          {candidate ? (
            <>
              <div className="figrow" style={{ marginBottom: 14 }}>
                <Figure
                  label="Total return"
                  value={signedPct(candidate.total_return)}
                  valueClass={signClass(candidate.total_return)}
                  sub={candidate.name}
                />
                <Figure
                  label={
                    <>
                      Sharpe
                      <InfoTip
                        label="Sharpe"
                        tip="Risk-adjusted return: average return over its volatility, annualized. Higher is better — above ~1 is strong for a daily strategy."
                      />
                    </>
                  }
                  value={signedFixed(candidate.sharpe)}
                  valueClass={signClass(candidate.sharpe)}
                  sub="net of costs"
                />
                <Figure
                  label={
                    <>
                      Max drawdown
                      <InfoTip
                        label="Max drawdown"
                        tip="The largest peak-to-trough drop in equity over the period. A measure of worst-case pain an allocator would have lived through."
                      />
                    </>
                  }
                  value={signedPct(candidate.max_drawdown)}
                  valueClass="loss"
                />
                <Figure
                  label="OOS span"
                  value={yearSpan(candidate.oos_start, candidate.oos_end)}
                  valueStyle={{ fontSize: 16 }}
                  sub={`${candidate.n_folds} folds`}
                />
              </div>
              <LineChart
                height={200}
                series={heroSeries}
                stressBands={bands}
                xTicks={[
                  { at: 0, label: candidate.oos_start?.slice(0, 4) ?? "" },
                  { at: 1, label: candidate.oos_end?.slice(0, 4) ?? "" },
                ]}
                ariaLabel={
                  hasBenchmark
                    ? `Cumulative return of ${candidate.name} versus SPY buy-and-hold`
                    : `Cumulative return of ${candidate.name}`
                }
              />
              <div className="legend">
                <span>
                  <i
                    className="swatch"
                    style={{ background: "var(--series)" }}
                  />{" "}
                  {candidate.name}
                </span>
                {hasBenchmark ? (
                  <span>
                    <i
                      className="swatch"
                      style={{
                        borderTop: "2px dashed var(--steel)",
                        height: 0,
                        width: 14,
                      }}
                    />{" "}
                    SPY · buy &amp; hold
                  </span>
                ) : null}
                <span>
                  <i
                    className="swatch band-stress"
                    style={{ height: 10, width: 14 }}
                  />{" "}
                  stress window
                </span>
              </div>
              {hasBenchmark ? (
                <p className="note">
                  Benchmark is SPY buy-and-hold over the same out-of-sample span
                  (growth of 1).
                </p>
              ) : (
                <p className="note">
                  Benchmark overlay (SPY / buy-and-hold) lands with the
                  live-data export; it is not fabricated here.
                </p>
              )}
            </>
          ) : (
            <Loading label="No strategies exported yet" />
          )}
        </div>

        <div className="panel">
          <div className="phead">
            <span className="t">Market snapshot</span>
            <span className="s">{market?.asof ?? "—"}</span>
          </div>
          <ul className="lin">
            <li>
              <span className="k">VIX</span>
              <span className="v">{market?.vix ?? "—"}</span>
            </li>
            <li>
              <span className="k">10Y yield</span>
              <span className="v">
                {market?.ten_year != null ? `${market.ten_year}%` : "—"}
              </span>
            </li>
            <li>
              <span className="k">Fed funds</span>
              <span className="v">
                {market?.fed_funds != null ? `${market.fed_funds}%` : "—"}
              </span>
            </li>
          </ul>
          {market?.notes?.length ? (
            <p className="note">{market.notes[0]}</p>
          ) : null}

          <div className="phead" style={{ marginTop: 18 }}>
            <span className="t">Data status</span>
          </div>
          <ul className="lin">
            {feeds.map((f) => (
              <li key={f.feed}>
                <span className="k">{f.feed}</span>
                <span className="v">
                  <span className={`pill ${feedPill(f.status)}`}>
                    <i />
                    {f.status}
                    {f.last_timestamp ? ` · ${f.last_timestamp}` : ""}
                  </span>
                </span>
              </li>
            ))}
          </ul>

          {portfolio ? (
            <>
              <div className="phead" style={{ marginTop: 18 }}>
                <span className="t">Strategy portfolio</span>
                <span className="s">deployment registry</span>
              </div>
              <div
                className="xtile"
                role="link"
                tabIndex={0}
                aria-label={`Open Strategy Portfolio — ${portfolio.n_enabled} in use, ${portfolio.n_idle} idle`}
                onClick={() => navigate("/portfolio")}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    navigate("/portfolio");
                  }
                }}
              >
                <ul className="lin">
                  <li>
                    <span className="k">In use</span>
                    <span className="v">
                      <span className="pill ok">
                        <i />
                        {portfolio.n_enabled}
                      </span>
                    </span>
                  </li>
                  <li>
                    <span className="k">Idle</span>
                    <span className="v">
                      <span className="pill idle">{portfolio.n_idle}</span>
                    </span>
                  </li>
                </ul>
                <p className="note" style={{ marginTop: 6 }}>
                  {portfolio.n_enabled} deployed · equal-weight allocation.{" "}
                  <span className="xlink">View portfolio →</span>
                </p>
              </div>
            </>
          ) : null}
        </div>
      </div>

      <div className="sec">
        Strategies <span className="dim">— which are up, down, and why</span>
        <span className="ln" />
      </div>
      <div className="panel flush">
        <TableScroll label="Strategies by Sharpe and return">
          <table>
            <thead>
              <tr>
                <th>Strategy</th>
                <th>Mode</th>
                <th className="num">Sharpe</th>
                <th className="num">Return</th>
                <th>Trajectory</th>
                <th>Primary driver</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr
                  key={s.id}
                  className="clk"
                  tabIndex={0}
                  role="link"
                  aria-label={`Open ${s.name} detail`}
                  onClick={() => navigate(`/strategies?pick=${s.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      navigate(`/strategies?pick=${s.id}`);
                    }
                  }}
                >
                  <td>{s.name}</td>
                  <td>
                    <span className="tag">{s.mode}</span>
                  </td>
                  <td className={`num ${signClass(s.sharpe)}`}>
                    {signedFixed(s.sharpe)}
                  </td>
                  <td className={`num ${signClass(s.total_return)}`}>
                    {signedPct(s.total_return)}
                  </td>
                  <td>
                    <Sparkline values={s.sparkline} tone={tone(s.sharpe)} />
                  </td>
                  <td className="small steel">{s.driver}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      </div>
    </section>
  );
}
