import { useSearchParams } from "react-router-dom";
import { dataClient } from "@/lib/dataClient";
import { useAsyncData } from "@/hooks/useAsyncData";
import { ErrorState, Loading } from "@/components/ui/StatePanel";
import { InfoTip } from "@/components/ui/InfoTip";
import type { ProvenanceView, StrategyCard } from "@/types/viewmodels";

/** Short 7-char commit for display; the full hash drives the link. */
function shortCommit(commit: string | null): string {
  return commit ? commit.slice(0, 7) : "—";
}

/** Cost line from per-share commission + slippage bps, honest about missing parts. */
function costLine(commission: number | null, slippageBps: number | null): string {
  const parts: string[] = [];
  if (commission != null) parts.push(`${(commission * 100).toFixed(1)}¢/sh`);
  if (slippageBps != null) parts.push(`${slippageBps} bps`);
  return parts.length ? parts.join(" · ") : "—";
}

function num(v: number | null): string {
  return v == null ? "—" : String(v);
}

function RunConfig({ p }: { p: ProvenanceView }) {
  const c = p.config;
  return (
    <div className="panel">
      <div className="phead">
        <span className="t">Run configuration</span>
        <span className="s">{c.model}</span>
      </div>
      <ul className="lin">
        <li>
          <span className="k">commit</span>
          <span className="v">
            {p.commit_url ? (
              <a href={p.commit_url} target="_blank" rel="noopener noreferrer">
                {shortCommit(p.commit)} ↗
              </a>
            ) : (
              <span className="dim">{shortCommit(p.commit)}</span>
            )}
          </span>
        </li>
        <li>
          <span className="k">train / test / step</span>
          <span className="v">
            {num(c.train_window)} / {num(c.test_window)} / {num(c.step)}
          </span>
        </li>
        <li>
          <span className="k">
            embargo
            <InfoTip
              label="Embargo"
              tip="Bars dropped between train and test to stop adjacent, serially-correlated samples from leaking into the test window."
            />
          </span>
          <span className="v">{c.embargo == null ? "—" : `${c.embargo} bars`}</span>
        </li>
        <li>
          <span className="k">label horizon</span>
          <span className="v">{num(c.label_horizon)}</span>
        </li>
        <li>
          <span className="k">costs</span>
          <span className="v">{costLine(c.commission_per_share, c.slippage_bps)}</span>
        </li>
      </ul>
    </div>
  );
}

/** Quiet enforced-status rows — leakage controls / self-tests (DECISIONS #5). */
function ControlRows({
  title,
  sub,
  rows,
}: {
  title: string;
  sub?: string;
  rows: ProvenanceView["leakage_controls"];
}) {
  return (
    <div className="panel">
      <div className="phead">
        <span className="t">{title}</span>
        {sub && <span className="s">{sub}</span>}
      </div>
      {rows.map((r) => (
        <div className="ctrl" key={r.name}>
          <span className="ck">✓</span>
          <span>
            {r.name}
            {r.detail ? ` — ${r.detail}` : ""}
          </span>
          <span className="meta2">{r.status}</span>
        </div>
      ))}
    </div>
  );
}

export function Provenance() {
  const [searchParams, setSearchParams] = useSearchParams();
  const picked = searchParams.get("run");

  const roster = useAsyncData((signal) => dataClient.strategies(signal), []);
  const rows: StrategyCard[] =
    roster.status === "ready" && Array.isArray(roster.data) ? roster.data : [];
  const ids = rows.map((r) => r.id);
  const selectedId = picked && ids.includes(picked) ? picked : ids[0];

  const provState = useAsyncData(async (signal) => {
    if (!selectedId) return null;
    return dataClient.provenance(selectedId, signal);
  }, [selectedId]);

  function select(id: string) {
    setSearchParams({ run: id }, { replace: true });
  }

  return (
    <section>
      <div className="h1">Provenance</div>
      <div className="lead">
        The exact inputs and controls behind a result — reproducible from the pinned
        configuration and commit. Pick a run to inspect its leakage controls, harness
        self-tests, and data lineage.
      </div>

      <div className="sec">
        Runs
        <span className="ln" />
      </div>
      {roster.status === "loading" && <Loading label="Loading runs…" />}
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
                <span className="tag">{r.mode}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {selectedId && provState.status === "loading" && <Loading label="Loading provenance…" />}
      {provState.status === "error" && <ErrorState error={provState.error} />}
      {provState.status === "ready" && provState.data && (
        <div className="grid c2" style={{ marginTop: 18 }}>
          <RunConfig p={provState.data} />
          <ControlRows
            title="Leakage controls"
            sub="enforced this run"
            rows={provState.data.leakage_controls}
          />
          <ControlRows title="Harness self-tests" rows={provState.data.self_tests} />
          <div className="panel">
            <div className="phead">
              <span className="t">Data lineage</span>
              <span className="s">point-in-time validated</span>
            </div>
            <ul className="lin">
              {provState.data.lineage.map((src) => (
                <li key={src}>
                  <span className="k" style={{ color: "var(--bone)" }}>
                    {src}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </section>
  );
}
