import type { ReactNode } from "react";
import { dataClient } from "@/lib/dataClient";
import { useAsyncData } from "@/hooks/useAsyncData";
import { ErrorState, Loading } from "@/components/ui/StatePanel";
import type {
  DataStatusView,
  FeedStatus,
  LakeGapReport,
  MarketSnapshot,
  SlaFeedStatus,
} from "@/types/viewmodels";

/** Feed status string -> pill variant (honest: stale/lag reads as warn). */
function feedPill(status: string): "ok" | "warn" | "bad" {
  const s = status.toLowerCase();
  if (s.includes("ok") || s.includes("fresh")) return "ok";
  if (s.includes("stale") || s.includes("lag") || s.includes("warn"))
    return "warn";
  return "bad";
}

function ageLabel(feed: FeedStatus): string {
  const parts: string[] = [];
  if (feed.last_timestamp) parts.push(`last ${feed.last_timestamp}`);
  if (feed.age_days != null) parts.push(`${feed.age_days.toFixed(1)}d old`);
  return parts.join(" · ") || "no observations";
}

/** SLA verdict card: latest observation vs the SLA's required date (E4-M1). */
function SlaCard({ s }: { s: SlaFeedStatus }) {
  const dates =
    s.latest || s.required_date
      ? `latest ${s.latest ?? "—"} · required ≥ ${s.required_date ?? "—"}`
      : "no observations";
  return (
    <div className="panel">
      <div className="phead">
        <span className="t mono">{s.feed}</span>
        <span className={`pill ${feedPill(s.state)}`}>
          <i />
          {s.state}
        </span>
      </div>
      <div className="mono small dim">{dates}</div>
    </div>
  );
}

/** Gap-report pill: verified 0, N gaps, or an honest "unchecked" (E4-M1). */
function gapPill(g: LakeGapReport): { variant: "ok" | "warn" | "bad"; label: string } {
  if (g.n_gaps == null) return { variant: "warn", label: "unchecked" };
  if (g.n_gaps === 0) return { variant: "ok", label: "no gaps" };
  return { variant: "bad", label: `${g.n_gaps} gap${g.n_gaps === 1 ? "" : "s"}` };
}

function GapCard({ g }: { g: LakeGapReport }) {
  const pill = gapPill(g);
  const window =
    g.window_start && g.window_end
      ? `${g.window_start} → ${g.window_end}`
      : "dataset unreadable — gaps not checked";
  return (
    <div className="panel">
      <div className="phead">
        <span className="t">{g.feed}</span>
        <span className={`pill ${pill.variant}`}>
          <i />
          {pill.label}
        </span>
      </div>
      <div className="mono small dim">{window}</div>
      {g.gap_dates.length > 0 && (
        <div className="mono small dim">
          missing {g.gap_dates.join(", ")}
          {g.n_gaps != null && g.n_gaps > g.gap_dates.length
            ? ` (+${g.n_gaps - g.gap_dates.length} earlier)`
            : ""}
        </div>
      )}
    </div>
  );
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
  const sla = status.sla ?? [];
  const gaps = status.gaps ?? [];
  const notes = status.notes ?? [];
  return (
    <>
      <div className="sec">
        Ingest SLA{" "}
        <span className="dim">
          — per-ingestor freshness vs the pinned C1 SLA, as of {status.asof}
        </span>
        <span className="ln" />
      </div>
      {sla.length > 0 ? (
        <div className="grid c4">
          {sla.map((s) => (
            <SlaCard key={s.feed} s={s} />
          ))}
        </div>
      ) : (
        <p className="note">
          {notes[0] ??
            "Per-ingestor SLA verdicts are unavailable in this export."}
        </p>
      )}

      <div className="sec">
        Lake gaps{" "}
        <span className="dim">— missing sessions inside the observed span</span>
        <span className="ln" />
      </div>
      <div className="grid c4">
        {gaps.map((g) => (
          <GapCard key={g.feed} g={g} />
        ))}
        {gaps.length === 0 && (
          <p className="note">No gap reports in this export.</p>
        )}
      </div>

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
        <MarketTile
          label="VIX"
          value={market.vix != null ? String(market.vix) : "—"}
        />
        <MarketTile
          label="10Y yield"
          value={market.ten_year != null ? `${market.ten_year}%` : "—"}
        />
        <MarketTile
          label="Fed funds"
          value={market.fed_funds != null ? `${market.fed_funds}%` : "—"}
        />
        {/* Bare tiles, matching the mockup's Data & Market figs. The Breadth +
            yield-curve ⓘ definitions live on the Overview conditions snapshot
            (E1-M5-OVERVIEW-CONDITION-TIPS) — the mockup's single home for them —
            rather than being retrofitted here. */}
        <MarketTile
          label="Breadth > MA200"
          value="—"
          sub="lands with E4"
          pending
        />
        <MarketTile label="2s10s" value="—" sub="lands with E4" pending />
      </div>
      {market.notes?.length ? <p className="note">{market.notes[0]}</p> : null}
      <p className="note">
        33-symbol universe · union timeline 2003 → 2026 · point-in-time
        validated. SLA verdicts mirror the C1 freshness monitor; intraday
        quotes arrive with the execution layer.
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
        Per-ingestor freshness judged against the pinned C1 SLA, lake gap
        detection, and a snapshot of the market environment the strategies
        operate in.
      </div>
      {state.status === "loading" && <Loading label="Loading data status…" />}
      {state.status === "error" && <ErrorState error={state.error} />}
      {state.status === "ready" && (
        <DataMarketBody status={state.data.status} market={state.data.market} />
      )}
    </section>
  );
}
