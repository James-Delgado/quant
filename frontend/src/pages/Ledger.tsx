import { dataClient } from "@/lib/dataClient";
import { useAsyncData } from "@/hooks/useAsyncData";
import { Figure } from "@/components/ui/Figure";
import { InfoTip } from "@/components/ui/InfoTip";
import { TableScroll } from "@/components/ui/TableScroll";
import { ErrorState, Loading } from "@/components/ui/StatePanel";
import { LuckBar } from "@/components/charts/LuckBar";
import { fixed } from "@/lib/format";
import type { LedgerRun, LedgerView } from "@/types/viewmodels";

/** Project key -> compact display label. */
function projectLabel(project: string): string {
  const p = project.toLowerCase();
  if (p === "phase-4a" || p === "4a") return "4A";
  return project.toUpperCase();
}

/** Verdict -> humanized label + pill variant. */
function verdictBadge(verdict: string): { label: string; cls: "ok" | "warn" | "bad" | "" } {
  const v = verdict.toLowerCase();
  if (v.includes("pass")) return { label: "passed", cls: "ok" };
  if (v.includes("fail")) return { label: "gate failed", cls: "bad" };
  if (v.includes("running") || v.includes("pending")) return { label: verdict, cls: "warn" };
  return { label: verdict.replace(/_/g, " "), cls: "" };
}

function shortCommit(commit: string | null): string {
  return commit ? commit.slice(0, 7) : "—";
}

function Row({ r }: { r: LedgerRun }) {
  const badge = verdictBadge(r.verdict);
  return (
    <tr>
      <td>{r.milestone}</td>
      <td>
        <span className="tag">{projectLabel(r.project)}</span>
      </td>
      <td className="num">{r.comparisons}</td>
      <td>
        <span className={badge.cls ? `pill ${badge.cls}` : "pill"}>{badge.label}</span>
      </td>
      <td className="mono small">
        {r.commit_url ? (
          <a href={r.commit_url} target="_blank" rel="noopener noreferrer">
            {shortCommit(r.commit)} ↗
          </a>
        ) : (
          <span className="dim">—</span>
        )}
      </td>
    </tr>
  );
}

function Registry({ data }: { data: LedgerView }) {
  const clears = data.best != null && data.best >= data.luck_bar;
  return (
    <>
      <div className="figrow" style={{ margin: "20px 0 8px" }}>
        <Figure label="Trials to date" value={data.n_trials} sub={`${data.n_entries} registered runs`} />
        <Figure
          label={
            <>
              Luck bar
              <InfoTip
                label="Luck bar"
                tip="The best Sharpe you'd expect from pure chance given how many strategies have been tried. A real result must clear this, and it rises as the trial count grows."
              />
            </>
          }
          value={fixed(data.luck_bar, 2)}
          valueClass="ser"
          sub="under no-skill null"
        />
        <Figure
          label="Best result"
          value={
            data.best == null
              ? "—"
              : `${fixed(data.best, 2)} ${clears ? "≥" : "<"} ${fixed(data.luck_bar, 2)}`
          }
          valueClass={clears ? "gain" : "loss"}
          sub={clears ? "clears the bar" : "does not clear the bar"}
        />
      </div>

      <div className="panel" style={{ marginTop: 4 }}>
        <div className="phead">
          <span className="t">Deflation luck bar</span>
          <span className="s">best vs no-skill threshold</span>
        </div>
        <LuckBar luck={data.luck_bar} best={data.best} />
        <div className="legend">
          <span>
            <i className="swatch" style={{ background: clears ? "var(--gain)" : "var(--loss)" }} /> best
            observed Sharpe
          </span>
          <span>
            <i className="swatch" style={{ background: "var(--warnc)", width: 2 }} /> luck bar
          </span>
        </div>
      </div>

      <div className="sec">
        Registered runs <span className="dim">— every pre-registered comparison</span>
        <span className="ln" />
      </div>
      <div className="panel flush">
        <TableScroll label="Registered comparison runs and verdicts">
        <table>
          <thead>
            <tr>
              <th>Run</th>
              <th>Project</th>
              <th className="num">Comparisons</th>
              <th>Verdict</th>
              <th>Commit</th>
            </tr>
          </thead>
          <tbody>
            {data.runs.map((r) => (
              <Row key={r.id} r={r} />
            ))}
          </tbody>
        </table>
        </TableScroll>
      </div>
      <p className="note">
        The trial count drives the multiple-testing bar: the more strategies tried, the
        higher an out-of-sample Sharpe must clear to be believed. Content-hash runs predate
        resolvable commit links and show “—”.
      </p>
    </>
  );
}

export function Ledger() {
  const state = useAsyncData((signal) => dataClient.ledger(signal), []);
  return (
    <section>
      <div className="h1">Trial Registry</div>
      <div className="lead">
        Every pre-registered comparison and its verdict. The count drives the
        multiple-testing bar that an out-of-sample result must clear to be believed.
      </div>
      {state.status === "loading" && <Loading label="Loading registry…" />}
      {state.status === "error" && <ErrorState error={state.error} />}
      {state.status === "ready" && <Registry data={state.data} />}
    </section>
  );
}
