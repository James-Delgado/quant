import type { ReactNode } from "react";
import { dataClient } from "@/lib/dataClient";
import { useAsyncData } from "@/hooks/useAsyncData";
import { ErrorState, Loading } from "@/components/ui/StatePanel";
import { InfoTip } from "@/components/ui/InfoTip";
import type { DataStatusView, FeedStatus, MarketSnapshot } from "@/types/viewmodels";

/** Feed status string -> pill variant (honest: stale/lag reads as warn). */
function feedPill(status: string): "ok" | "warn" | "bad" {
  const s = status.toLowerCase();
  if (s.includes("ok") || s.includes("fresh")) return "ok";
  if (s.includes("stale") || s.includes("lag") || s.includes("warn")) return "warn";
  return "bad";
}

function ageLabel(feed: FeedStatus): string {
  const parts: string[] = [];
  if (feed.last_timestamp) parts.push(`last ${feed.last_timestamp}`);
  if (feed.age_days != null) parts.push(`${feed.age_days.toFixed(1)}d old`);
  return parts.join(" · ") || "no observations";
}

/** A market figure tile; renders an explicit pending state when the value is null. */
function MarketTile({
  label,
  value,
  sub,
  pending,
}: {
  label: ReactNode;
  value: string;
  sub?: string;
  pending?: boolean;
}) {
  return (
    <div className="panel fig">
      <span className="lab">{label}</span>
      <span className={`val${pending ? " dim" : ""}`}>{value}</span>
      {sub && <span className={`sub${pending ? "" : " steel"}`}>{sub}</span>}
    </div>
  );
}

function DataMarketBody({
  status,
  market,
}: {
  status: DataStatusView;
  market: MarketSnapshot;
}) {
  return (
    <>
      <div className="sec">
        Feeds <span className="dim">— lake freshness as of {status.asof}</span>
        <span className="ln" />
      </div>
      <div className="grid c4">
        {status.feeds.map((f) => (
          <div className="panel" key={f.feed}>
            <div className="phead">
              <span className="t">{f.feed}</span>
              <span className={`pill ${feedPill(f.status)}`}>
                <i />
                {f.status}
              </span>
            </div>
            <div className="mono small dim">{ageLabel(f)}</div>
          </div>
        ))}
      </div>

      <div className="sec">
        Market snapshot <span className="dim">— {market.asof ?? "—"}</span>
        <span className="ln" />
      </div>
      <div className="grid c4">
        <MarketTile label="VIX" value={market.vix != null ? String(market.vix) : "—"} />
        <MarketTile
          label="10Y yield"
          value={market.ten_year != null ? `${market.ten_year}%` : "—"}
        />
        <MarketTile
          label="Fed funds"
          value={market.fed_funds != null ? `${market.fed_funds}%` : "—"}
        />
        <MarketTile
          label={
            <>
              Breadth &gt; MA200
              <InfoTip
                label="Breadth"
                tip="Share of the universe trading above its 200-day moving average — a gauge of how broad the uptrend is."
              />
            </>
          }
          value="—"
          sub="lands with E4"
          pending
        />
        <MarketTile
          label={
            <>
              2s10s
              <InfoTip
                label="Yield curve"
                tip="2s10s: the 10-year minus 2-year Treasury yield. Negative (inverted) has historically preceded recessions; positive here."
              />
            </>
          }
          value="—"
          sub="lands with E4"
          pending
        />
      </div>
      {market.notes?.length ? <p className="note">{market.notes[0]}</p> : null}
      <p className="note">
        33-symbol universe · union timeline 2003 → 2026 · point-in-time validated. Live SLA
        monitoring and intraday quotes arrive with the execution layer.
      </p>
    </>
  );
}

export function DataMarket() {
  const state = useAsyncData(async (signal) => {
    const [status, market] = await Promise.all([
      dataClient.dataStatus(signal),
      dataClient.market(signal),
    ]);
    return { status, market };
  }, []);

  return (
    <section>
      <div className="h1">Data &amp; Market</div>
      <div className="lead">
        Feed health over the lake, and a snapshot of the market environment the strategies
        operate in. Live SLA monitoring and intraday quotes arrive with the execution layer.
      </div>
      {state.status === "loading" && <Loading label="Loading data status…" />}
      {state.status === "error" && <ErrorState error={state.error} />}
      {state.status === "ready" && (
        <DataMarketBody status={state.data.status} market={state.data.market} />
      )}
    </section>
  );
}
