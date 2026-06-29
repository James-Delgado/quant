import type { ReactNode } from "react";
import { dataClient } from "@/lib/dataClient";
import { useAsyncData } from "@/hooks/useAsyncData";
import { ErrorState, Loading } from "@/components/ui/StatePanel";
import { InfoTip } from "@/components/ui/InfoTip";
import type { PortfolioStrategy, PortfolioView } from "@/types/viewmodels";

/**
 * Strategy Portfolio panel (E-STRATEGIES-PANEL).
 *
 * Renders the C6 deployment registry: ENABLED ("in use") and disabled ("idle")
 * strategies, each with its model / target / universe, equal-weight allocation,
 * and provenance. This is the *static* deployment portfolio — what is configured
 * to run, NOT realized returns. Live per-strategy P&L is E3 (live monitoring),
 * deliberately absent so the panel makes no claim it cannot back (DECISIONS
 * #5/#7). All values come from the service-layer `portfolio.json` export; the
 * panel only renders.
 */

function pct(value: number): string {
  return `${value.toFixed(value % 1 === 0 ? 0 : 1)}%`;
}

/** A labelled value cell in the strategy meta grid. */
function Kv({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="pf-kv">
      <span className="pf-k">{label}</span>
      <span className="pf-v">{children}</span>
    </div>
  );
}

function StrategyCardPanel({ strategy }: { strategy: PortfolioStrategy }) {
  const inUse = strategy.status === "enabled";
  return (
    <div className="panel pf-card">
      <div className="phead">
        <span className="t">{strategy.display_name}</span>
        <span className={`pill ${inUse ? "ok" : "idle"}`}>
          {inUse && <i />}
          {inUse ? "in use" : "idle"}
        </span>
      </div>
      <p className="pf-desc">{strategy.description}</p>
      <div className="pf-meta">
        <Kv label="Model">
          <span className="mono">{strategy.model_ref}</span>
        </Kv>
        <Kv label="Target">
          <span className="mono">{strategy.target_ref}</span>
        </Kv>
        <Kv label="Cadence">
          <span className="mono">
            {strategy.cadence} · {strategy.broker}
          </span>
        </Kv>
        <Kv label="Allocation">
          <span className="mono">
            {inUse ? pct(strategy.allocation_pct) : "—"}
          </span>
          {!inUse && (
            <span className="dim small"> (no capital while idle)</span>
          )}
        </Kv>
        <Kv label="Provenance">
          <span className={inUse ? "" : "dim"}>
            {strategy.provenance_summary}
          </span>
        </Kv>
        <Kv label="Universe">
          <span className="pf-tags">
            {strategy.universe.map((sym) => (
              <span className="tag" key={sym}>
                {sym}
              </span>
            ))}
          </span>
        </Kv>
      </div>
    </div>
  );
}

function Section({
  title,
  hint,
  strategies,
}: {
  title: string;
  hint: string;
  strategies: PortfolioStrategy[];
}) {
  if (strategies.length === 0) return null;
  return (
    <>
      <div className="sec">
        {title} <span className="dim">— {hint}</span>
        <span className="ln" />
      </div>
      <div className="pf-list">
        {strategies.map((s) => (
          <StrategyCardPanel strategy={s} key={s.id} />
        ))}
      </div>
    </>
  );
}

function PortfolioBody({ view }: { view: PortfolioView }) {
  const inUse = view.strategies.filter((s) => s.status === "enabled");
  const idle = view.strategies.filter((s) => s.status !== "enabled");
  return (
    <>
      <Section
        title="In use"
        hint={`${view.n_enabled} deployed · equal-weight allocation`}
        strategies={inUse}
      />
      <Section
        title="Idle"
        hint={`${view.n_idle} configured, not deployed`}
        strategies={idle}
      />
      {idle.length === 0 && (
        <p className="note">
          No idle strategies — every configured strategy is deployed.
        </p>
      )}
      <p className="note">
        This is the deployment portfolio — what is configured to run, and how
        capital is split across it. Realized per-strategy performance arrives
        with live execution monitoring; until then no live P&amp;L is shown
        here.
      </p>
    </>
  );
}

export function Portfolio() {
  const state = useAsyncData((signal) => dataClient.portfolio(signal), []);
  return (
    <section>
      <div className="h1">Strategy Portfolio</div>
      <div className="lead">
        Every strategy in the deployment registry — those{" "}
        <strong>in use</strong> (live in the daily run, sharing capital equally)
        and those <strong>idle</strong> (configured but not yet deployed). Each
        carries its model, prediction target, universe, allocation, and{" "}
        <InfoTip
          label="Provenance"
          tip="What justifies deploying this strategy: either an infrastructure placeholder (no edge claim) or a gate-verified trial verdict. No strategy goes live on an unbacked claim."
        />{" "}
        provenance.
      </div>
      {state.status === "loading" && <Loading label="Loading portfolio…" />}
      {state.status === "error" && <ErrorState error={state.error} />}
      {state.status === "ready" && <PortfolioBody view={state.data} />}
    </section>
  );
}
