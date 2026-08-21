import type { ReactNode } from "react";
import { dataClient } from "@/lib/dataClient";
import { useAsyncData } from "@/hooks/useAsyncData";
import { ErrorState, Loading } from "@/components/ui/StatePanel";
import type {
  CatalogView,
  DataStatusView,
  FeedStatus,
  LakeGapReport,
  MarketSnapshot,
  SlaFeedStatus,
} from "@/types/viewmodels";

/** "mid_vol" → "mid vol" — regime labels come from the condition machinery. */
function fmtRegime(label: string): string {
  return label.replace(/_/g, " ");
}

/** Signed 2s10s spread in percentage points, e.g. "+0.52pp". */
function fmtSpread(spread: number): string {
  return `${spread >= 0 ? "+" : ""}${spread.toFixed(2)}pp`;
}

/** Breadth fraction → whole-percent display, e.g. "68%". */
function fmtBreadth(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

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
function gapPill(g: LakeGapReport): {
  variant: "ok" | "warn" | "bad";
  label: string;
} {
  if (g.n_gaps == null) return { variant: "warn", label: "unchecked" };
  if (g.n_gaps === 0) return { variant: "ok", label: "no gaps" };
  return {
    variant: "bad",
    label: `${g.n_gaps} gap${g.n_gaps === 1 ? "" : "s"}`,
  };
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
  catalog,
}: {
  status: DataStatusView;
  market: MarketSnapshot;
  catalog: CatalogView | null;
}) {
  const sla = status.sla ?? [];
  const gaps = status.gaps ?? [];
  const notes = status.notes ?? [];
  // Live feature-drift figures from the catalog monitor's verdicts (E4-M3) —
  // the single source of truth the alerts also consume (one signal, one judge).
  const monitored = catalog
    ? catalog.summary.stable + catalog.summary.drifting + catalog.summary.stale
    : 0;
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
            rather than being retrofitted here. Values are live as of E4-M3; a
            missing input renders an honest pending "—", never a fabricated
            figure. */}
        <MarketTile
          label="Breadth > MA200"
          value={
            market.breadth_above_ma200 != null
              ? fmtBreadth(market.breadth_above_ma200)
              : "—"
          }
          sub={
            market.breadth_above_ma200 != null
              ? `${market.breadth_n_symbols ?? 0} symbols judged`
              : "universe prices unavailable"
          }
          pending={market.breadth_above_ma200 == null}
        />
        <MarketTile
          label="2s10s"
          value={
            market.spread_2s10s != null ? fmtSpread(market.spread_2s10s) : "—"
          }
          sub={
            market.spread_2s10s != null
              ? `10Y ${market.ten_year ?? "—"} · 2Y ${market.two_year ?? "—"}`
              : "DGS2/DGS10 unavailable"
          }
          pending={market.spread_2s10s == null}
        />
      </div>

      <div className="sec">
        Market environment{" "}
        <span className="dim">
          — live regimes, labelled by the same condition machinery as the
          Conditions panel
        </span>
        <span className="ln" />
      </div>
      <div className="grid c4">
        <MarketTile
          label="Volatility regime"
          value={market.vol_regime ? fmtRegime(market.vol_regime) : "—"}
          sub={market.vol_regime ? "VIX vs pinned 15/25" : "VIX unavailable"}
          pending={!market.vol_regime}
        />
        <MarketTile
          label="Trend regime"
          value={market.trend_regime ? fmtRegime(market.trend_regime) : "—"}
          sub={
            market.trend_regime
              ? "benchmark vs MA200"
              : "benchmark unavailable"
          }
          pending={!market.trend_regime}
        />
        <MarketTile
          label="Rates regime"
          value={market.rates_regime ? fmtRegime(market.rates_regime) : "—"}
          sub={
            market.rates_regime
              ? "10Y trailing-quarter change"
              : "10Y unavailable"
          }
          pending={!market.rates_regime}
        />
        <MarketTile
          label="Feature drift"
          value={monitored > 0 ? `${catalog!.summary.drifting} drifting` : "—"}
          sub={
            monitored > 0
              ? `${monitored} features monitored live`
              : "feature monitor not wired"
          }
          pending={monitored === 0}
        />
      </div>
      {market.notes?.length ? (
        <p className="note">{market.notes.join(" ")}</p>
      ) : null}
      <p className="note">
        33-symbol universe · union timeline 2003 → 2026 · point-in-time
        validated. SLA verdicts mirror the C1 freshness monitor; intraday quotes
        arrive with the execution layer.
      </p>
    </>
  );
}

export function DataMarket() {
  const state = useAsyncData(async (signal) => {
    // catalog.json feeds only the live feature-drift tile; its absence must
    // not take down the whole panel (honest degrade → pending tile).
    const [status, market, catalog] = await Promise.all([
      dataClient.dataStatus(signal),
      dataClient.market(signal),
      dataClient.catalog(signal).catch(() => null),
    ]);
    return { status, market, catalog };
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
        <DataMarketBody
          status={state.data.status}
          market={state.data.market}
          catalog={state.data.catalog}
        />
      )}
    </section>
  );
}
