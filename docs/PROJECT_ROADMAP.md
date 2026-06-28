# Project Roadmap — Post-Phase 4A

*Master pivot document. Authored after the Phase 4A no-go verdict
([`PHASE_4A_REPORT.md`](PHASE_4A_REPORT.md)). Sequencing and
prioritization layer above the phase docs. Completed phase docs have
moved to [`docs/historical/`](historical/).*

> **Reading order for a fresh agent or contributor:**
> 1. [`AGENT_OPERATION.md`](AGENT_OPERATION.md) — **the procedure** for
>    picking up and executing a task end-to-end. Read first if you're
>    operating; the default user prompt is "pick up the next ready task
>    from `PRIORITIES.yaml`" and this doc is the rest of the instructions.
> 2. This document — **what** we're building and why.
> 3. [`METHODOLOGY.md`](METHODOLOGY.md) — **how** to do it honestly
>    (binding contract for every PRD and every agent run).
> 4. [`PRIORITIES.yaml`](PRIORITIES.yaml) — **next up** task to pick.
> 5. The task's `references.primary` and any cited PRD or concept doc.

---

## 1 — Vision

A system that:

- **Predicts what is actually predictable** on a chosen universe (not
  necessarily next-bar return on the Dow 30 — Phase 4A demonstrated that
  target is structurally unlearnable from public features on this universe).
- **Acts on those predictions with calibrated confidence and proper sizing**
  — emits prediction + confidence interval, sizes position by volatility
  target and confidence, respects max-position and drawdown caps.
- **Runs daily (or intraday) in paper, then live**, with continuous
  reconciliation between backtest and paper, and continuous monitoring
  of model behaviour against the regime axis the harness already tracks.
- **Continuously researches new features, data sources, and model variants** 
  via an agent harness once one productive
  research-to-deployment cycle has proven the loop.

Phases 0–4A built the research substrate for the first bullet only. The
remaining three are largely untouched.

---
## 2 — Where we are

| Capability | Status | Evidence |
|---|---|---|
| Data lake + ingestion (OHLCV, FRED, SEC, sentiment) | ✅ Built, batch | `src/quant/ingest/`, `flows/daily.py` |
| Purged walk-forward backtester + cost simulator | ✅ Production-quality | `backtest/walkforward.py`, `harness.py` |
| Regime-conditional evaluation + gates | ✅ Built | `backtest/regimes.py`, `regime_metrics.py` |
| Label/feature ablation orchestrators | ✅ Built | `backtest/ablation.py` |
| Feature catalog + drift test | ✅ Built (27 columns) | `features/catalog.{py,yaml}` |
| Pre-committed-protocol pattern | ✅ Codified | `PHASE_4A_RETROSPECTIVE.md` §"What worked" |
| Predictive edge over ARIMA on this universe | ❌ Not demonstrated | Phase 4A gate verdict |
| Live data pipeline (same-day) | ❌ Not built | Ingestion is nightly batch |
| Execution layer (paper or live) | ❌ Not built | Phase 4 Track B deferred |
| Position sizing + risk management | ❌ Not built | Simulator is 1-share uniform |
| Confidence calibration | ❌ Not built | Models emit point predictions only |
| Continuous-agent harness | ❌ Vision spec only | `PHASE_5_AGENTS.md` |
| OOS-only feature attribution method | ❌ Open question | M3 ρ = −0.074 finding |
| Human-visible console / observability layer | ❌ Not built (spec mature) | Project E — `docs/project-e/` (PRDs + mockup + `DECISIONS.md`) |

The research substrate is mature. The deployment substrate is empty. The
human-interface layer (Project E) is specified and buildable over existing
artifacts.

---

## 3 — The pivot

Phase 4A's negative result reshapes the project in four ways:

1. **Stop refining a non-edge on this *sandbox*.** Three label schemes,
   M3 ablations, FRED-lag correction, regime-conditional evaluation, and
   Bonferroni-adjusted secondary arms all failed to flip the verdict.
   The model isn't broken; the information set on the chosen sandbox
   is. Note explicitly: **the Dow 30 + SPY/QQQ/IWM universe was a
   sandbox to develop infrastructure on a clean, survivorship-free set —
   not a claim that no edge exists on equities**. Universe selection is
   part of the design space going forward.
2. **Move from "find edge first, then build deployment" to "build
   deployment in parallel with refocused research."** The "find edge
   first" gate produced a clean negative. Continuing to gate deployment
   on a positive research result risks the perfectionism trap:
   indefinite research refinement, no deployment, no real-world feedback.
3. **Treat target reframing, alternative data, execution-layer build,
   and universe selection as a portfolio**, not a strict sequence. Each
   is a bet that could pay off independently; combining them in one PRD
   recreates the confounding problem Phase 4A discipline was built to
   avoid.
4. **Adopt a portfolio-of-strategies design intent.** Successful quant
   shops do not run a single model on a single universe with a single
   target. They run a **portfolio of (target, model, universe,
   regime-conditioning) tuples** and rebalance among them. The
   infrastructure decisions below (catalog, ledger, harness, monitoring)
   are sized for the portfolio, not for one model.

The PRD risk table for Phase 4A pre-committed this pivot:

> *Phase 4A ends with a documented "no edge" report; transition is to
> either new data sources or fundamentally different label/target
> framing — not to Track A.*

This roadmap honors that.

---

## 4 — Project portfolio

Four projects, each independently scoped, with explicit dependencies.

### Project A — Research substrate & methodology (DONE / MAINTAIN)

- **Scope**: the harness, catalog, ablation orchestrators, regime
  machinery, runner pattern, methodology contract from
  [`PHASE_4A_RETROSPECTIVE.md`](PHASE_4A_RETROSPECTIVE.md).
- **Status**: complete through Phase 4A. Maintain via the existing test
  suite (467 tests) and drift tests.
- **Future work in scope**: pattern improvements identified in the
  retrospective — trial-count ledger as code artifact, deflated-Sharpe
  in gate function, OOS-only attribution method.

### Project B — Predictive research (post-4A)

The replacement for Phase 4A's research mission. Built as a portfolio of
PRDs, each with its own pre-commitment.

| Sub-project | Hypothesis | PRD status |
|---|---|---|
| **B1 — Target reframing** | A target other than next-bar return (e.g. 21-day drawdown classification, vol prediction, n-day directional) is structurally more learnable on this universe | Recommended first; not yet drafted |
| **B2 — OOS-only attribution method** | Permutation importance on the test slice (or per-fold ablation as canonical signal) produces a feature-importance ranking that *does* transfer OOS | Recommended in parallel with B1 — addresses the ρ = −0.074 blocker |
| **B3 — Alternative data: options-implied surfaces** | Options-implied vol skew / put-call ratios carry information equity-only features don't (Cboe daily settle is free) | Conditional on B1's verdict; queued |
| **B4 — Universe shift** | A less-efficient universe (mid-cap, single-stock options, international) has more extractable edge than Dow 30 + ETFs | Conditional, lower priority |

**Constraint that applies to every B sub-project**: must reuse the existing
harness, catalog, regime gates, and runner pattern; must follow the
methodology contract in `PHASE_4A_RETROSPECTIVE.md`. No new PRD is approved
without a pre-committed exit gate quoted verbatim in a gate function.

### Project C — Live execution & deployment infrastructure

The Phase 4 Track B mission, elevated to a parallel build rather than a
deferred one. The hypothesis: building the deployment loop with a
placeholder strategy is the highest-value, lowest-research-risk move
because (a) it forces all the missing infrastructure into existence,
(b) it gives Project B a real deployment target, and (c) it eliminates
the "found edge in backtest, never shipped" failure mode.

| Sub-project | Outcome |
|---|---|
| **C1 — Live data + same-day inference pipeline** | A "today's data" reader that returns the most recent point-in-time-correct bar for any universe symbol; freshness SLA monitored; nightly batch supplemented (or replaced) by same-day pull |
| **C2 — Execution layer (LEAN local + paper)** | LEAN local installed; placeholder algorithm consumes model predictions from outside LEAN; paper-trading runs daily; results reconcile with the Phase 1 backtest |
| **C3 — Position sizing + risk management** | Vol-targeted sizing replaces the simulator's 1-share-uniform; max-position caps; drawdown stops; live-mode position state |
| **C4 — Confidence calibration** | Models emit prediction + calibrated confidence (conformal prediction or quantile regression); sizing logic consumes confidence |
| **C5 — Monitoring + reconciliation** *(superseded by Project E3)* | Daily dashboard: positions, P&L, regime indicator, paper-vs-backtest delta, model output histogram. **Superseded by [Project E3 — Live Monitoring](#project-e--human-interface--observability)**: live monitoring + paper-vs-backtest reconciliation now ship in the console (`docs/project-e/E3-live-monitoring.prd.md`) on top of the E2 API. |

**Placeholder strategies that unblock C2 before any B verdict**:
buy-and-hold-SPY (trivial), monthly-rebalance equal-weight Dow 30, or the
Phase 4A ARIMA control (already in code). Any of these is enough to drive
the C1→C5 build.

### Project D — Continuous research agents (Phase 5)

Vision spec is mature ([`PHASE_5_AGENTS.md`](PHASE_5_AGENTS.md)).
**Explicitly gated** (decision 7 in §8 — both triggers required):

1. **Project B has produced an ablation matrix with at least one cell
   that clears its pre-committed gate** (any B sub-project counts).
2. **B2's OOS attribution method is in code with M3 catalog integration
   shipped.** Agent F's design depends on it; building the agent against
   an attribution method that doesn't transfer (the ρ = −0.074 finding)
   is wasted work.

**Agent scope when Phase 5 begins**: each agent operates on a defined set
of artifact types — not just features. Agent F (formerly "feature
engineer") expands to **all candidate-generation artifact types**:
features, label schemes, prediction targets, regime detectors, sample
weighting schemes. Agent M (model developer) likewise covers models +
labels + per-model hyperparameter spaces. Agent R (research scout)
surfaces work for both. Universe selection is shared across both. This
matches the portfolio-of-tuples design intent in §3.4.

Until both triggers are met, Phase 5 stays a spec.

### Project E — Human Interface & Observability

A **human-interface and observability layer** that makes the platform's
results, models, and data human-visible — both a daily-driver analytical
console for the operator and a credible surface that shows a knowledgeable
quant that the data, data preparation, and models can be trusted (conveyed
through substance, never self-description). Full PRDs, the consensus mockup,
and the decision log live in [`docs/project-e/`](project-e/)
([`DECISIONS.md`](project-e/DECISIONS.md) is the rationale of record).

Decomposed into four sub-projects with different dependencies, chained
**E1 → E2 → E3/E4**. Bundling them would recreate the confounding the
methodology exists to avoid and would block the immediately-valuable E1 on
infrastructure that does not exist yet.

| Sub-project | Outcome | Gated on |
|---|---|---|
| **E1 — Research & Trust Console** | Two-layer build (tested Python service layer in `src/quant/console/` + disposable React/TS SPA) reading existing artifacts; 8 panels at mockup parity; static-JSON export (no server); in-UI "Report an issue" button → `feedback` GitHub issue + `PRIORITIES.yaml` promotion path | **Buildable now** (existing artifacts only) |
| **E2 — Console API** | FastAPI service wrapping the *same* E1 service-layer readers (no duplicated logic); read endpoints at export-schema parity; real `POST /feedback`; freshness/health + on-demand re-export; React data source swaps static↔API behind a flag | **Project C1** (live data) + E1 service layer |
| **E3 — Live Monitoring** | Lights up E1's live tiles — live P&L / positions / exposure, per-strategy live performance, live regime indicator, and a paper-vs-backtest reconciliation panel with a pre-committed delta threshold. **Supersedes C5.** | **E2** + **C2/C3** (paper execution + sizing) |
| **E4 — Data & Market Status** | Live feed health vs SLA + gap detection, staleness/gap/drift alerting (pinned thresholds), live market-environment view, and a live feature-drift monitor (dynamic counterpart to E1's catalog stats) | **E2** + **C1** (live data + freshness SLA) |

**Conventions that bind every E build agent** (from
[`DECISIONS.md`](project-e/DECISIONS.md)): the mockup is the frozen scope +
visual + interaction contract (new asks become `feedback` issues → tasks, not
silent additions); reuse the existing `storage/` + `features/` modules with
**no new datastore**; never *say* "trust" (convey it through substance); no
internal file paths in the UI; regimes are presented as live-computable
**conditions** with named episodes demoted to a stress-windows view; the
issue tracker is engineer/agent-visible only (no user-facing tracker panel).
Binds methodology rules 15–21; every sub-project ends with a `*-CLOSE`
end-to-end validation + one-page closeout report.

---

## 5 — Methodology contract (forward)

The full contract lives in [`METHODOLOGY.md`](METHODOLOGY.md) — single
source of truth, machine-readable for agents, **binding for every PRD
and every agent run** (more strictly for agents than for humans, not
less; an autonomous loop has *more* p-hacking surface than a human
session, not less).

Summary of what the contract covers — read `METHODOLOGY.md` for the
authoritative version:

- 10 practices from Phase 4A (pre-committed thresholds, gates-in-code,
  contamination audits, contract-before-consumer, conditional
  sub-milestones, drift tests, checkpointed compute, parity audits,
  honest deviation declarations, materiality-before-significance).
- 4 post-4A upgrades: slice + full-panel discipline, trial-count ledger
  as code artifact, DSR-aware gates, OOS-only attribution.
- Agent-harness application: pre-registration required, DSR N computed
  from the ledger, hard guardrails tested in CI, verdicts emitted by
  gate functions (not paraphrased by agents).

---

## 6 — Dependencies & sequencing

```
A (substrate) ── done ──┐
                        │
                        ├──> B1 (target reframing)    ─┐
                        ├──> B2 (OOS attribution)     ─┤── feeds D triggers
                        │                              │
                        ├──> C1 (live data)            │
                        │      │                       │
                        │      └──> C2 (LEAN/paper) ─┐ │
                        │             │              │ │
                        │             ├──> C3 (sizing/risk)
                        │             ├──> C4 (confidence)
                        │             └──> C5 (monitoring)
                        │
                        └──> B3, B4 (data/universe) ── conditional on B1
                                                       │
                                                       └─ feeds D triggers

D (Phase 5 agents) ── gated on both: B-cycle artifact AND B2 (OOS attribution) in code

E (human interface & observability)
   E1 (console over existing artifacts) ── buildable now
        └──> E2 (console API) ── gated on C1
               ├──> E3 (live monitoring; supersedes C5) ── also gated on C2/C3
               └──> E4 (data & market status) ── also gated on C1
```

**Ratified sequencing (decision 1 in §8)**: parallel across B and C, but
**single-threaded agent execution** from the priority list. Concurrent
agents on overlapping files (`backtest/harness.py`, `models/*.py`,
`features/catalog.yaml`) would produce merge conflicts the harness is
not yet built to resolve. The priority list ([`PRIORITIES.yaml`](PRIORITIES.yaml))
enforces single-thread; concurrent execution via git worktrees becomes
a Phase 5 sub-feature.

| Now (0–4 weeks) | Soon (4–12 weeks) | Later (gated) |
|---|---|---|
| A — trial-count ledger (`A-LEDGER`) — unblocks every PRD draft below | C2 (LEAN + paper) on top of C1 | B3 (alt data) only if B1 surfaces no edge |
| B1 PRD drafting + execution | C3 (sizing) + C4 (confidence) | B4 (universe) lowest priority |
| B2 PRD drafting + execution | C5 (monitoring) | D (Phase 5) only after triggers met |
| C1 (live data audit + same-day reader) | | |

Why parallel across projects: B1 and C1 share no code paths and no
compute, so they don't contend at the *project* level. Agents work on
one task at a time, but the tasks they pick come from any project.

Why sequential within B: B1 answers "what should we predict?" before B3
asks "what data should feed the prediction?" Spending on a Cboe ingestor
before knowing whether the target is correct is exactly the mistake
Phase 4A documented.

---

## 7 — Milestone sketch per project

These are *sketches*, not PRDs. Each becomes a full PRD via the same
`/plan-prd` → `/plan` pipeline Phase 4A used.

### B1 — Target reframing

| # | Milestone | Outcome |
|---|---|---|
| M1 | Candidate target catalog | 4 targets pre-committed with rationale: 21-day drawdown classification (binary: P(>5% drawdown)?), 21-day realized vol prediction, 5-day directional, 21-day cumulative direction |
| M2 | Per-target ablation matrix on slice | Each target × {ARIMA control, GBM, naive baseline} on 5-symbol × 8-year slice; verdict via existing `phase4a_gate_report` |
| M3 | Full-panel confirmation of winners | Any M2 candidate showing per-regime edge is re-evaluated on the full panel under the M6 runner pattern |
| Gate | At least one target surfaces a Sharpe-or-classification-metric edge that survives deflation (DSR > 0) | Binary; if no, the verdict is "no extractable edge from this feature set on any of the four targets" — fold into B3 or close the project |

### B2 — OOS-only attribution method

| # | Milestone | Outcome |
|---|---|---|
| M1 | Method shortlist + theory | Permutation importance computed on test fold, per-fold ablation as the canonical signal, conformal feature relevance — pick 2 to implement |
| M2 | Implementation + sanity test | The method, run on the M6 final feature set, reproduces an OOS attribution that correlates with the M3 ablation lifts (Spearman ρ > 0.5 — the inverse of the broken IS signal) |
| M3 | Documentation + catalog integration | `attribution_status` field added to `catalog.yaml`; future PRD ablations can pin attribution evidence per feature |

### C1 — Live data + same-day inference pipeline

| # | Milestone | Outcome |
|---|---|---|
| M1 | Same-day data SLA audit | For each ingestor (Alpaca, Tiingo, FRED, EDGAR), document the actual freshness available; flag gaps |
| M2 | Today's-bar reader | A function `get_pit_bar(symbol, asof)` returning the most recent point-in-time-correct bar; integrated with `build_features()` |
| M3 | Freshness monitor | Cron + alert if any feed is stale beyond its SLA |

### C2 — Execution layer (LEAN local + paper)

| # | Milestone | Outcome |
|---|---|---|
| M1 | LEAN local installed + hello-world algorithm runs | Model lives outside LEAN; LEAN consumes predictions via signal feed. **Platform decision (ratified §8.3)**: LEAN local first; fall back to Alpaca paper adapter only if LEAN install friction exceeds 2 days. |
| M2 | ARIMA(1,0,0) daily signal in paper | **Placeholder decision (ratified §8.4)**: ARIMA(1,0,0) daily — exercises prediction emission, signal feed, paper execution, position state, and (later) calibration/sizing. GBM placeholder is intentionally rejected — it adds ~25 min/backtest of compute without exercising any deployment infrastructure ARIMA doesn't. |
| M3 | Reconciliation harness | Daily paper-trading P&L reconciles with the Phase 1 backtest for the same period; any >1% delta investigated |

### C3 — Position sizing + risk management

| # | Milestone | Outcome |
|---|---|---|
| M1 | Vol-targeted sizing | Replace 1-share-uniform with target-vol allocation; backtest sanity-checks against existing harness |
| M2 | Max-position + drawdown stops | Hard caps on per-symbol exposure; trailing-drawdown stop logic |
| M3 | Live-mode position state | Persisted state across daily runs; matches LEAN's view of holdings |

### C4 — Confidence calibration

| # | Milestone | Outcome |
|---|---|---|
| M1 | Method shortlist | Conformal prediction vs. quantile regression vs. bootstrap CI — pick 1 for ARIMA, 1 for GBM |
| M2 | Models emit calibrated intervals | `BacktestResult` extended with `oos_prediction_intervals`; sizing logic can read them |
| M3 | Calibration audit | Per-regime coverage tests: 90% intervals actually contain 90% of OOS realizations |

> **Boundary with Project E4**: live *calibration*-drift monitoring (coverage
> decaying below target on live data) is C4's scope — the live extension of M3
> — and is *surfaced* in the console (E3/E4) but *computed* by C4's calibration
> machinery. E4 owns *feature/data*-distribution drift, which is a different
> signal. Keep the two distinct when C4-PRD and E4 are drafted.

### C5 — Monitoring + reconciliation *(superseded by Project E3)*

> **Superseded by [Project E3 — Live Monitoring](#project-e--human-interface--observability)**
> (`docs/project-e/E3-live-monitoring.prd.md`). The monitoring dashboard and
> paper-vs-backtest reconciliation now ship in the console on top of the E2
> API rather than as a standalone C5 dashboard. C5's scope redistributes
> cleanly. **Dashboard** (C5-M1): positions / P&L / exposure + paper-vs-backtest
> delta + the live regime indicator + the **model-output (signal-distribution)
> histogram** → **E3**. **Alerting** (C5-M2): paper-backtest divergence → E3;
> **regime-change alert** + feed staleness + gap detection + feature-distribution
> drift → **E4** (`docs/project-e/E4-data-market-status.prd.md`). The
> model-output histogram and regime-change alert were the two C5 elements not
> already in the E3/E4 PRDs and were folded into them.
> **Calibration drift stays with C4** — it is the live extension of C4-M3's
> per-regime calibration audit (does a 90% interval still cover 90% live?),
> *distinct* from E4's feature-distribution drift monitor; the console surfaces
> it but C4's calibration machinery computes it. Retained here for the
> dependency record; the `C5-PRD` task's disposition is open (see
> `PRIORITIES.yaml`).

| # | Milestone | Outcome |
|---|---|---|
| M1 | Daily dashboard | Positions, P&L, regime indicator, paper-vs-backtest delta, model output histogram |
| M2 | Alerting | Regime change, paper-backtest divergence, calibration drift, feed staleness |

### D — Phase 5 continuous agents

Already specified in [`PHASE_5_AGENTS.md`](PHASE_5_AGENTS.md). Begins only
when both triggers in §4 are met.

### E — Human interface & observability

Already specified in full PRDs under [`docs/project-e/`](project-e/) (E1–E4 +
the consensus mockup + [`DECISIONS.md`](project-e/DECISIONS.md)) — more
detailed than the sketches above, so not re-sketched here. A clear-context
agent translates each PRD's milestone table into `PRIORITIES.yaml` tasks (E1
buildable now; E2–E4 gated per §4). E3 supersedes C5.

---

## 8 — Ratified decisions

*(Ratified 2026-06-17. Each becomes a frozen line in subsequent PRDs.
Updates require a roadmap revision, not an in-PRD override.)*

1. **Sequencing**: parallel across projects B and C, with single-threaded
   agent execution from [`PRIORITIES.yaml`](PRIORITIES.yaml). Concurrent
   agent execution is deferred to a Phase 5 sub-feature (git worktrees).
2. **B1 target scope**: all four candidate targets (21-day drawdown
   classification, 21-day realized vol, 5-day directional, 21-day
   cumulative direction) tested in one ablation matrix. The data
   selects, not the researcher.
3. **C2 platform**: LEAN local first. Fall back to Alpaca paper adapter
   only if LEAN install friction exceeds 2 days. Documented in C2-M1.
4. **C2 placeholder strategy**: ARIMA(1,0,0) daily. GBM intentionally
   rejected as a placeholder — it adds ~25 min/backtest of compute
   without exercising additional deployment infrastructure.
5. **B2 timing**: parallel with B1 (not a strict prerequisite). B1's M1
   and M2 can proceed with raw per-fold ablation; B2's output unblocks
   the catalog write-back on B1's results and Trigger 2 for Phase 5.
6. **Trial-count ledger**: lives at `data/ledger.yaml`, written by every
   runner, audited by `tests/test_ledger.py`. Schema in
   [`METHODOLOGY.md`](METHODOLOGY.md) §"Reference schemas". Implemented
   as the first task (`A-LEDGER` in [`PRIORITIES.yaml`](PRIORITIES.yaml))
   because it unblocks every PRD draft below it.
7. **Phase 5 trigger formalism**: **both** of the following are required:
   (a) Project B has produced an ablation matrix with at least one cell
   that clears its pre-committed gate; (b) B2's OOS attribution method is
   in code with B2-M3 catalog integration shipped. Either alone is
   insufficient.

### Methodology and document-organization decisions (from same turn)

- **Methodology contract location**: extracted to standalone
  [`METHODOLOGY.md`](METHODOLOGY.md). Agents read one short file to
  understand the rules instead of digging through phase docs.
- **Applies to the agent harness, strictly**: every Phase 5 agent's
  system prompt links to `METHODOLOGY.md`; pre-registration is required
  for every agent-run experiment; verdicts come from gate functions, not
  agent prose. See `METHODOLOGY.md` §"Application to the agent harness".
- **`docs/` organization**: completed phase docs moved to
  [`docs/historical/`](historical/) (Phase 0–3 specs, Phase 4 advanced
  spec, refactor docs). Top level now holds only the active references:
  `PROJECT_ROADMAP.md`, `METHODOLOGY.md`, `PRIORITIES.yaml`,
  `PHASE_4A_REPORT.md` (verdict), `PHASE_4A_RETROSPECTIVE.md` (narrative),
  `PHASE_5_AGENTS.md` (active future spec), plus `CONTRIBUTING.md`,
  `ENV.md`, and the `concepts/` reference set.

---

## 9 — What this roadmap does *not* do

- It does not declare any of B1–B4 a guaranteed win. The Phase 4A
  evidence says edge is not guaranteed; the project's job is to test
  hypotheses honestly, not to assume them.
- It does not commit to Phase 5 timing. The triggers are the commitment.
- It does not displace the existing phase docs (`PHASE_0–5`). Those
  remain authoritative for their own scope. This document is the
  sequencing and prioritization layer above them.
- It does not budget compute or wall-time per milestone. Each PRD
  picks those up.

---

*Status: RATIFIED 2026-06-17. Next agent action: pick task `A-LEDGER`
from [`PRIORITIES.yaml`](PRIORITIES.yaml). `CLAUDE.md`'s project-status
section should be updated to reference this roadmap and the methodology
contract.*
