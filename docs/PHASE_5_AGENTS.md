# Phase 5 — Autonomous Research Agents (Continuous Alpha Search)

> **Spec document.** See the Phase 0–4 specs for the foundation this phase
> builds on, and `.claude/prds/phase-4a-feature-and-label-redesign.prd.md`
> for the artifacts Phase 4A deliberately left behind for this phase.
> Status: **vision spec** — when Phase 5 begins, this document becomes a PRD
> via `/plan-prd` and milestone plans via `/plan`, the same pipeline Phase 4A
> used.

---

## Objective

Convert the human-driven research loop of Phases 0–4A into a **continuously
running, agent-operated loop**: autonomous agents propose alpha candidates,
pre-register experiments, implement them, evaluate them through the existing
harness, and document verdicts — while the human reviews *verdicts* instead of
running *experiments*, and retains exclusive ownership of the evaluation
standards.

The fleet (long-run vision, stated 2026-06-12):

- **Agent R — research scout.** Monitors arXiv and other sources for new
  algorithms, strategies, features, and data ideas; produces structured,
  implementable candidate briefs.
- **Agent F — feature engineer.** Builds point-in-time-correct features from
  briefs and its own catalog-gap analysis; evaluates them via per-feature
  ablation; continuously grows the feature catalog.
- **Agent M — model developer.** Implements candidate models and label
  schemes; evaluates them against the pinned baselines and regime gates.

Phase 4A's infrastructure was designed *in anticipation of* this phase: the
feature catalog is the agents' read/write contract, the ablation orchestrators
are their evaluation API, the regime gate machinery is their verdict function,
and the trials registry is the control on what happens when machines run
experiments around the clock.

### Lineage

The original formulation (2026-06-08, during Phase 4A PRD drafting) was a
**two-agent pair** with research embedded in each agent:

> "1 agent will be responsible for continuous feature engineering, including
> researching new features for alpha, creation of those features and their
> respective pipelines, and testing/evaluating gain with the feature or with
> a different combination of features. The other agent will be responsible
> for continuous model development, including researching advanced
> algorithms or strategies being developed via arxiv or some other
> resources, creating the model in the repo, and evaluating/analyzing the
> model offline. Having these two continuous agents is my ultimate goal.
> This is how we can rapidly test new strategies, add feature complexity,
> and identify alpha."

The 2026-06-12 restatement split research into its own agent (R below). Both
shapes are valid: if a standalone scout proves heavy, the fallback is the
original two-agent pair with R's sourcing duties folded into F and M.

---

## Entry gate (prerequisites)

- **Phase 4A complete**, including the written exit-gate report
  (`docs/PHASE_4A_REPORT.md`) — **either verdict**. Phase 5 does *not*
  require a "go": a no-go *increases* the value of automation, because the
  search space widens (new data sources, new target framings) to exactly the
  breadth a continuous loop handles better than episodic human sessions.
- Required artifacts landed and tested: regime-conditional harness (4A-M1),
  label/feature ablation orchestrators (4A-M2/M3), feature catalog + drift
  test (4A-M4), corrected FRED joins (4A-M5), trials registry (4A-M6).
- Evaluation standards pinned in `docs/concepts/` with explicit update
  protocols ("do not retune to make a model pass").

---

## The central design problem

An autonomous experiment loop is a **p-hacking machine by default**. A fleet
that runs hundreds of comparisons will produce spectacular-looking Sharpe
ratios by selection alone. The Phase 2.5 T4 failure (DSR = 0.364, with the
trial count *unknown* because it was never tracked) is the small-scale preview
of this failure mode. Phase 5's architecture therefore treats the
anti-selection-bias machinery as the *core product*, and the agents as
clients of it. Every design decision below follows from this.

---

## Workstream 1 — The agent harness (shared infrastructure)

Built **before** any agent. Four components:

### 1a. Run ledger

A machine-writable extension of the Phase 4A trials registry. Every
experiment is one record:

```yaml
- id: run-2027-01-15-0003
  agent: F                       # R | F | M | human
  brief_id: 2027-01-10-vol-of-vol
  preregistration: prereg/2027-01-14-vol-of-vol.yaml
  config_hash: "…"               # ordered columns + harness kwargs
  n_comparisons: 4               # regime columns evaluated — feeds DSR N
  started: 2027-01-15T02:00:00Z
  verdict: tested_no_edge        # pinned vocabulary, mirrors catalog enums
  artifacts: data/phase5/runs/run-2027-01-15-0003/
```

The DSR deflation N is *computed from the ledger*, never estimated. An
experiment result that isn't in the ledger is invalid by convention and
rejected at review.

### 1b. Pre-registration

No experiment runs without a committed pre-registration record: hypothesis,
success criterion (which pinned gate, which threshold), and planned comparison
count — written *before* the run starts. This mechanizes the pre-commitment
discipline the project already practices by hand (VIX thresholds,
`LDP_DEFAULT`, T1–T6, the 4A protocol).

### 1c. Scheduler + budgets

Prefect (already in the repo for daily ingestion — `flows/daily.py` is the
precedent) or cron-driven headless Claude Code sessions. Per-agent weekly
compute budgets, per-run wall-clock caps, and an auto-halt rule: if the
harness self-tests (random → no edge; leaky → caught) ever fail, the entire
loop stops until a human investigates.

### 1d. Weekly digest

A human-readable report: briefs found, experiments run, verdicts, ledger
stats, the period's best result *deflated against the ledger N*, and an
explicit "decisions needed from you" list. The human's primary interface.

### Hard guardrails (enforced, not advisory)

Agents may **never** modify:

- `backtest/walkforward.py` / `harness.py` split logic (purge/embargo
  invariants — see `backtest/CLAUDE.md`),
- thresholds in `docs/concepts/evaluation-standards.md` or any pinned
  default (`LDP_DEFAULT`, VIX thresholds, era boundaries, gate criteria),
- the run-ledger schema or the guardrail tests themselves.

Enforcement is layered: guardrail tests in CI, per-directory CLAUDE.md
instructions, and protected-path review. Changes to any of these require a
human-authored PR.

---

## Workstream 2 — Agent F (feature engineer) — *built first*

Built first because Phase 4A constructed its exact contracts: the catalog
(what exists), the drift test (what must stay consistent), the per-feature
ablation with noise guard (how candidates are judged), and the glossary
(where rationale lives).

- **Reads**: triaged briefs, `features/catalog.yaml`, the glossary,
  per-regime results in the ledger.
- **Does**: implements a candidate feature (point-in-time correct, validated
  prelude, tests — mirroring `engineering.py` / `cross_sectional.py`
  patterns); registers it (`ablation_status: untested`); pre-registers the
  ablation; runs `run_feature_ablation` with the noise guard; writes the
  verdict back to catalog + ledger. Per the original vision, scope includes
  **feature combinations** (interaction sets via `feature_sets`, not just
  add-one — each combination is one pre-registered, ledger-counted trial)
  and, for `required_data: new-ingestor` briefs, **building the data
  pipeline** (mirroring the Phase 0/3 ingestor patterns — schema, pandera
  validation, lake write) with human review before the new source is trusted.
- **Cannot**: touch labels, split logic, gates, or other agents' lanes.
- **MVP definition**: one brief taken end-to-end (idea → feature → ablation →
  catalog write-back → digest entry) with zero human intervention mid-loop.

---

## Workstream 3 — Agent R (research scout)

- **Sources**: arXiv (`q-fin.*`, `cs.LG`, `stat.ML`), SSRN, a curated list of
  practitioner blogs/feeds (the RSS ingestor from Phase 3 is the plumbing
  precedent).
- **Output contract**: structured briefs at `research/briefs/`:

```yaml
id: 2027-01-10-vol-of-vol
title: "Volatility-of-volatility as a cross-sectional signal"
source: "arXiv:XXXX.XXXXX"
mechanism: "why would this be priced in, and who is on the other side?"
required_data: available          # available | new-ingestor | unobtainable
consumer: feature                 # feature | label | model | data-source
implementation_sketch: "…"
expected_evidence: "which gate this should move, and in which regime"
status: new                       # new | triaged | implemented | rejected
```

- **Constraints**: read-only on the codebase; never implements; dedups
  against existing briefs and the catalog. A triage step (human at first,
  later scored) gates `new → triaged` so brief spam can't flood Agents F/M.
- The Phase 4A report's "candidate directions" section seeds the initial
  backlog — especially under a no-go, where new data sources and alternative
  target framings are the declared next moves.

---

## Workstream 4 — Agent M (model developer) — *built last*

Highest blast radius (it touches `models/` and consumes the most compute), so
it ships after the harness, F, and R have proven the loop.

- **Reads**: triaged model/label briefs; pinned baselines; gate machinery.
- **Does**: implements candidates under `models/` (mirroring the
  `.fit()`/`.predict()` contract); pre-registers; evaluates via
  `run_portfolio_backtest` / `run_label_ablation` + `phase4a_gate_report`;
  writes ledger verdicts.
- If Phase 4A ended "go": Agent M's first backlog item is the Track A
  (transformer) PRD, evaluated under the same harness per
  `PHASE_4_ADVANCED.md` sub-track A rules (ephemeral GPU, must beat the
  simpler model by a complexity-justifying margin).
- If "no-go": Agent M works the alternative-framing backlog (e.g.,
  cross-sectional relative-return targets) fed by Agent R.

---

## Delivery milestones

| # | Milestone | Outcome | Status |
|---|---|---|---|
| 1 | Run ledger + pre-registration | No experiment can run unlogged; DSR N computed from the ledger | pending |
| 2 | Agent F MVP | One brief → feature → ablation → catalog write-back, end-to-end unattended | pending |
| 3 | Agent R MVP | Scout + brief schema + triage queue; initial backlog seeded from the 4A report | pending |
| 4 | Scheduler, budgets, weekly digest | The loop runs on a cadence with hard compute caps and a human-readable digest | pending |
| 5 | Agent M MVP | One model/label brief evaluated through the gate machinery, end-to-end | pending |
| 6 | Unattended soak + autonomy go/no-go | 4 consecutive weeks unattended; expansion of autonomy is a gated, explicit decision | pending |

Ordering rationale: infrastructure before agents (the ledger is the
p-hacking control — nothing runs without it); F before R before M (F has the
strongest existing contracts; R feeds the others; M has the largest blast
radius).

---

## Exit criteria

- The loop runs **≥ 4 consecutive weeks unattended** (human interaction
  limited to digests and PR review), producing **≥ 8 pre-registered,
  ledger-logged evaluations** with **zero** guardrail or leakage-control
  violations.
- 100% of experiments traceable end-to-end: brief → pre-registration → run →
  verdict → catalog/ledger write-back.
- Every weekly digest includes a false-discovery audit: the period's best
  observed Sharpe deflated against the ledger's true N.
- An explicit, documented go/no-go on *expanding* agent autonomy (more
  sources, bigger budgets, less triage) — autonomy is earned in steps, like
  everything else in this project.

---

## Risks and pitfalls

- **P-hacking at machine scale** — the central risk. Mitigation is the
  architecture itself: ledger + pre-registration + immutable gates + digest
  deflation. If these feel like friction, they are working.
- **Agents editing invariants** — guardrail tests, protected paths,
  per-directory CLAUDE.md, human-only PRs for standards.
- **Compute runaway** — per-run caps, weekly budgets, ephemeral compute only
  (the `PHASE_4_ADVANCED.md` GPU discipline applies).
- **Brief spam / low-quality research** — triage gate between `new` and
  `triaged`; dedup against catalog and prior briefs.
- **Code-vs-docs drift at agent speed** — extend the M4 drift-test pattern:
  every agent-writable artifact gets an enforcement test.
- **Verdict laundering** — an agent summarizing its own result favorably.
  Verdicts come from gate functions (`phase4a_gate_report`,
  `feature_ablation_gate`), not from agent prose; digests quote the function
  output.
- **Confusing research autonomy with capital autonomy** — Phase 5 automates
  *research*. Execution (Track B / LEAN / live capital) stays human-gated and
  outside this phase entirely.

---

## Tooling

Claude Code headless sessions / Agent SDK (agent runtime), Prefect
(scheduling — already in the repo), the Phase 1–4A harness as the sole
evaluation standard, `features/catalog.yaml` as the feature contract, arXiv
API + RSS for Agent R, YAML + pydantic for briefs/ledger/pre-registration
schemas (the M4 pattern).

---

## What comes next

Phase 5 automates Project A's research loop. Sub-track B of Phase 4
(execution layer, LEAN, paper trading — the bridge to Project B) remains a
separate, human-gated track: connecting an autonomous research fleet to a
live execution layer is its own risk-control problem and warrants its own
phase document when reached.
