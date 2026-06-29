import { dataClient } from "@/lib/dataClient";
import { useAsyncData } from "@/hooks/useAsyncData";
import { Figure } from "@/components/ui/Figure";
import { InfoTip } from "@/components/ui/InfoTip";
import { TableScroll } from "@/components/ui/TableScroll";
import { ErrorState, Loading } from "@/components/ui/StatePanel";
import { DistMini } from "@/components/charts/DistMini";
import { pct } from "@/lib/format";
import type { CatalogView, FeatureCard } from "@/types/viewmodels";

/** Group key -> display label (de-underscored). */
function groupLabel(group: string): string {
  return group.replace(/_/g, "-");
}

/** OOS-evidence badge from the registry's real ablation/attribution fields. */
function oosBadge(f: FeatureCard): {
  label: string;
  cls: "ok" | "warn" | "bad" | "tag";
} {
  if (f.ablation_status === "tested_edge")
    return { label: "edge", cls: "warn" };
  if (f.ablation_status === "tested_no_edge")
    return { label: "no edge", cls: "bad" };
  return { label: "untested", cls: "tag" };
}

/** Stability pill from the monitor; null => monitoring pending (honest dash). */
function stabilityCell(stability: string | null) {
  if (stability == null) return <span className="dim small">—</span>;
  const s = stability.toLowerCase();
  const cls = s.includes("stable")
    ? "ok"
    : s.includes("drift")
      ? "warn"
      : "bad";
  return <span className={`pill ${cls}`}>{stability}</span>;
}

function muSigma(mean: number | null, std: number | null): string {
  if (mean == null || std == null) return "—";
  return `${mean.toFixed(3)} / ${std.toFixed(3)}`;
}

export function FeatureCatalog() {
  const state = useAsyncData((signal) => dataClient.catalog(signal), []);

  return (
    <section>
      <div className="h1">Feature Catalog</div>
      <div className="lead">
        The registered feature set with live monitoring — summary statistics,
        coverage, and a drift check that compares each feature's recent window
        against its training distribution.
      </div>

      {state.status === "loading" && <Loading label="Loading catalog…" />}
      {state.status === "error" && <ErrorState error={state.error} />}
      {state.status === "ready" && <Catalog data={state.data} />}
    </section>
  );
}

function Catalog({ data }: { data: CatalogView }) {
  const { summary, features } = data;
  // The lake-backed monitor (E1-M1-FEATURE-MONITOR) is not wired yet, so the
  // monitoring-derived stats are null. Detect that and render an explicit
  // pending state rather than a misleading "0 drifting / 0 stale".
  const monitoringPending = summary.mean_coverage == null;

  return (
    <>
      <div className="figrow" style={{ margin: "20px 0 6px" }}>
        <Figure label="Registered" value={summary.registered} />
        <Figure
          label="Stable"
          value={monitoringPending ? "—" : summary.stable}
          valueClass={monitoringPending ? "dim" : "gain"}
        />
        <Figure
          label={
            <>
              Drifting
              <InfoTip
                label="Drifting"
                tip="A feature whose recent distribution has shifted materially from the distribution the models were trained on. Worth review before trusting fresh predictions."
              />
            </>
          }
          value={monitoringPending ? "—" : summary.drifting}
          valueClass={monitoringPending ? "dim" : ""}
        />
        <Figure
          label="Stale"
          value={monitoringPending ? "—" : summary.stale}
          valueClass="dim"
        />
        <Figure
          label={
            <>
              Coverage
              <InfoTip
                label="Coverage"
                tip="Share of symbol-days where the feature has a non-null value over the panel."
              />
            </>
          }
          value={monitoringPending ? "—" : pct(summary.mean_coverage as number)}
          valueClass="dim"
        />
      </div>

      {monitoringPending && (
        <div className="banner" style={{ margin: "6px 0 10px" }}>
          <span>
            <b>Monitoring pending.</b> Coverage, μ/σ, distribution, and drift
            status populate once the nightly lake-backed feature monitor is
            wired. The registry below — groups, point-in-time rules, and OOS
            evidence — is live.
          </span>
        </div>
      )}

      <div className="panel flush" style={{ marginTop: 8 }}>
        <TableScroll label="Registered features with coverage and stability">
          <table>
            <thead>
              <tr>
                <th>Feature</th>
                <th>Group</th>
                <th className="num">Coverage</th>
                <th>Distribution</th>
                <th className="num">μ / σ</th>
                <th>Stability</th>
                <th>OOS status</th>
              </tr>
            </thead>
            <tbody>
              {features.map((f) => {
                const badge = oosBadge(f);
                return (
                  <tr key={f.name}>
                    <td className="mono small">{f.name}</td>
                    <td className="steel small">{groupLabel(f.group)}</td>
                    <td className="num">
                      {f.coverage == null ? "—" : pct(f.coverage)}
                    </td>
                    <td>
                      <DistMini
                        values={f.distribution}
                        ariaLabel={`${f.name} distribution`}
                      />
                    </td>
                    <td className="num small">{muSigma(f.mean, f.std)}</td>
                    <td>{stabilityCell(f.stability)}</td>
                    <td>
                      <span
                        className={
                          badge.cls === "tag" ? "tag" : `pill ${badge.cls}`
                        }
                      >
                        {badge.label}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableScroll>
      </div>
      <p className="note">
        Distributions and coverage refresh nightly once the monitor lands. OOS
        evidence is per-fold ablation: features marked <b>edge</b> carry it on
        slice evidence only — full-panel confirmation is open and flagged, not
        assumed.
      </p>
    </>
  );
}
