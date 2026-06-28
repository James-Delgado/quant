import { useSearchParams } from "react-router-dom";
import { dataClient } from "@/lib/dataClient";
import { useAsyncData } from "@/hooks/useAsyncData";
import { Figure } from "@/components/ui/Figure";
import { ErrorState, Loading } from "@/components/ui/StatePanel";
import { LineChart } from "@/components/charts/LineChart";
import { Histogram } from "@/components/charts/Histogram";
import { signClass, signedFixed, signedPct } from "@/lib/format";
import type { StrategyCard, StrategyDetail } from "@/types/viewmodels";

/** Status string -> roster pill variant. */
function statusPill(status: string): "ok" | "warn" | "bad" | "" {
  const s = status.toLowerCase();
  if (s.includes("candidate") || s.includes("pass")) return "ok";
  if (s.includes("under") || s.includes("fail") || s.includes("no edge")) return "bad";
  if (s.includes("running") || s.includes("pending")) return "warn";
  return "";
}

function lineTone(sharpe: number): string {
  if (sharpe > 0) return "var(--gain)";
  if (sharpe < 0) return "var(--loss)";
  return "var(--series)";
}

function values(points: { value: number }[]): number[] {
  return points.map((p) => p.value);
}

function Detail({ detail, control }: { detail: StrategyDetail; control: StrategyDetail }) {
  const delta = detail.metrics.sharpe - control.metrics.sharpe;
  const isControl = detail.id === control.id;
  const stroke = lineTone(detail.metrics.sharpe);

  return (
    <>
      <div className="sec">
        Detail — <span>{detail.name}</span>
        <span className="ln" />
      </div>
      <p className="lead" style={{ marginTop: 0 }}>
        {detail.description}
      </p>
      <div className="figrow" style={{ margin: "18px 0 6px" }}>
        <Figure
          label="Sharpe"
          value={signedFixed(detail.metrics.sharpe)}
          valueClass={signClass(detail.metrics.sharpe)}
          sub={isControl ? "control" : `ARIMA ${signedFixed(control.metrics.sharpe)}`}
        />
        <Figure
          label="Δ vs control"
          value={isControl ? "—" : signedFixed(delta)}
          valueClass={isControl ? "dim" : signClass(delta)}
        />
        <Figure
          label="Total return"
          value={signedPct(detail.metrics.total_return)}
          valueClass={signClass(detail.metrics.total_return)}
        />
        <Figure
          label="Max drawdown"
          value={signedPct(detail.metrics.max_drawdown)}
          valueClass="loss"
        />
      </div>

      <div className="grid c2" style={{ marginTop: 14 }}>
        <div className="panel">
          <div className="phead">
            <span className="t">Cumulative return</span>
            <span className="s">vs ARIMA control</span>
          </div>
          <LineChart
            height={170}
            series={
              isControl
                ? [{ values: values(detail.equity), className: "ln-port", stroke }]
                : [
                    { values: values(control.equity), className: "ln-bench" },
                    { values: values(detail.equity), className: "ln-port", stroke },
                  ]
            }
            ariaLabel={`Cumulative return of ${detail.name}`}
          />
          <div className="legend">
            <span>
              <i className="swatch" style={{ background: stroke }} /> {isControl ? "control" : "selected"}
            </span>
            {!isControl && (
              <span>
                <i className="swatch" style={{ background: "var(--steel)" }} /> ARIMA
              </span>
            )}
          </div>
        </div>

        <div className="panel">
          <div className="phead">
            <span className="t">Drawdown</span>
            <span className="s">underwater</span>
          </div>
          <LineChart
            height={170}
            series={[{ values: values(detail.drawdown), className: "dd-area", area: true }]}
            gridLines={2}
            ariaLabel={`Drawdown of ${detail.name}`}
          />
          <div className="legend">
            <span className="dim">deepest underwater stretch</span>
          </div>
        </div>

        <div className="panel">
          <div className="phead">
            <span className="t">Rolling Sharpe</span>
            <span className="s">252-bar</span>
          </div>
          <LineChart
            height={150}
            series={[{ values: values(detail.rolling_sharpe), className: "ln-port", stroke }]}
            gridLines={0}
            zeroAxisAt={0}
            yCornerLabels={{ top: "+", bottom: "−" }}
            ariaLabel={`Rolling Sharpe of ${detail.name}`}
          />
          <div className="legend">
            <span className="dim">crossing zero marks a regime of edge / no edge</span>
          </div>
        </div>

        <div className="panel">
          <div className="phead">
            <span className="t">Daily return distribution</span>
            <span className="s">net of costs</span>
          </div>
          <Histogram hist={detail.return_hist} ariaLabel={`Return distribution of ${detail.name}`} />
        </div>
      </div>

      <div
        className="banner"
        style={{ borderLeftColor: stroke, margin: "18px 0 0" }}
      >
        <span>
          <b>Why.</b> {detail.why}
        </span>
      </div>
    </>
  );
}

export function Strategies() {
  const [searchParams, setSearchParams] = useSearchParams();
  const picked = searchParams.get("pick");

  const roster = useAsyncData((signal) => dataClient.strategies(signal), []);
  const rows: StrategyCard[] =
    roster.status === "ready" && Array.isArray(roster.data) ? roster.data : [];
  const ids = rows.map((r) => r.id);
  const selectedId = picked && ids.includes(picked) ? picked : ids[0];

  const detailState = useAsyncData(async (signal) => {
    if (!selectedId) return null;
    const [detail, control] = await Promise.all([
      dataClient.strategy(selectedId, signal),
      dataClient.strategy("arima", signal),
    ]);
    return { detail, control };
  }, [selectedId]);

  function select(id: string) {
    setSearchParams({ pick: id }, { replace: true });
  }

  return (
    <section>
      <div className="h1">Strategies</div>
      <div className="lead">
        Every research strategy under evaluation, each benchmarked against the
        ARIMA(1,0,0) control. New strategies — and live ones, once execution is online —
        append to this roster.
      </div>

      <div className="sec">
        Roster
        <span className="ln" />
      </div>
      {roster.status === "loading" && <Loading label="Loading roster…" />}
      {roster.status === "error" && <ErrorState error={roster.error} />}
      {roster.status === "ready" && (
        <div className="roster">
          {rows.map((r) => (
            <div
              key={r.id}
              className={`rost${r.id === selectedId ? " sel" : ""}`}
              tabIndex={0}
              role="button"
              aria-pressed={r.id === selectedId}
              onClick={() => select(r.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  select(r.id);
                }
              }}
            >
              <div>
                <div className="nm">{r.name}</div>
                <div className="ds">{r.driver}</div>
              </div>
              <div className="rt">
                <div className={`v ${signClass(r.sharpe)}`}>{signedFixed(r.sharpe)}</div>
                <span className={`pill ${statusPill(r.status)}`}>{r.status}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {selectedId && detailState.status === "loading" && <Loading label="Loading detail…" />}
      {detailState.status === "error" && <ErrorState error={detailState.error} />}
      {detailState.status === "ready" && detailState.data && (
        <Detail detail={detailState.data.detail} control={detailState.data.control} />
      )}
    </section>
  );
}
