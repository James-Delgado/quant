# Methodology Contract

> **Audience**: every human and every agent that runs experiments, writes
> PRDs, or commits research code in this repository.
>
> **Scope**: how we test hypotheses honestly and report verdicts that
> survive scrutiny. *What* to research is decided in PRDs; this document
> is *how* to research.
>
> **Authority**: this contract binds the agent harness (Phase 5) **more
> strictly than it binds human researchers**, not less. An autonomous loop
> has more p-hacking surface than a human session, not less. Any agent
> that bypasses these practices is producing untrustworthy results, full
> stop.

This contract was derived from the Phase 4A experience (see
[`PHASE_4A_RETROSPECTIVE.md`](PHASE_4A_RETROSPECTIVE.md) for the
lessons-learned narrative). It is the canonical reference; the
retrospective is the story behind why each rule exists.

---

## The 10 practices

### 1. Pre-commit numeric thresholds before any compute touches the experiment

Every regime boundary, ablation threshold, materiality cut-off, label
parameter, and gate constant is **pinned in code or PRD prose before
any result is visible**. Reference examples: `VIXThresholdDetector(15, 25)`,
`LDP_DEFAULT`, M5's `(5% sign-flip, 0.1 |ΔSharpe|)`, M3's paired-bootstrap
90% CI noise guard, M6 protocol items 1–7.

If a threshold changes after a result is visible, the experiment is
invalidated. Re-run with the new threshold under a new ledger entry.

### 2. Quote exit gates verbatim in code, not just in prose

Every PRD's success metric must be implemented as a function (see
`backtest/regime_metrics.py::phase4a_gate_report` as the reference
implementation). The function is the source of truth. Prose describes the
function; it does not redefine it.

Future agents (human or LLM) cannot retroactively rewrite a gate without a
code change visible in `git diff`.

### 3. Run a contamination audit before sequencing milestones

Every milestone plan includes an explicit "what could contaminate
downstream milestones?" pass before committing to execution order. Phase
4A's M5 (FRED-lag leakage) was promoted ahead of M3 because the planning
audit caught a plausible leak that would have contaminated M3's baseline.
The leak was real.

If the audit surfaces a credible contamination path, re-sequence.

### 4. Build contracts before consumers

When a milestone pair includes both a registry/catalog/schema *and* an
ablation/experiment against it, the registry comes first. The catalog
(`features/catalog.yaml`) should have come before M3's ablation, not after.

### 5. Use conditional sub-milestones with explicit skip criteria

When a milestone's value depends on a prior milestone's outcome, the PRD
pre-writes **both** the trigger and the skip path. Phase 4A's M2.5
(meta-labeling) is the reference: trigger = "M2 surfaces a winning primary
scheme"; skip path = "no scheme beats ARIMA → skip M2.5 entirely."

The skip path is binding. Once the trigger fails, the milestone cannot
be revived by alternative justification without a new PRD.

### 6. Enforce code-vs-config contracts in both directions

Drift tests assert `set(produced) == set(catalog)` and name the offender
either way (`unregistered` and `phantom` lists). The reference
implementation is `tests/test_catalog.py`.

This pattern generalizes to: pinned defaults, schema fields, regime
boundaries, ledger entries — any code-vs-config invariant.

### 7. Checkpoint expensive compute; verdict from checkpoints

Expensive runs (anything > 5 minutes of wall-time) write per-arm checkpoints
to parquet + JSON metadata; verdict notebooks load checkpoints and compute
verdicts in seconds. The reference pattern is
`scripts/run_phase4a_arms.py` + `notebooks/09_phase4a_exit_gate.ipynb`.

The verdict notebook must never re-fit a model. The runner must be
idempotent and resumable from any arm.

### 8. Record invariant-parity audits in code, not prose

Sample-weight parity, FRED lag values, seed values, OOS index intersection
sizes, dropped-bar counts — every invariant the verdict depends on is
pinned in the runner's `metadata.json` and asserted at gate time.

Phase 4A's sample-weight parity audit caught a real bug
(`run_label_ablation` silently mis-weighting `triple_barrier` because it
didn't update `label_horizon`). Without the audit, the bug would have
entered the gate result.

### 9. Declare protocol deviations honestly

Any corner-cut from the pre-committed protocol is declared in the report
with the deviation's bounded impact analysis. Phase 4A's `vol_scaled` DM
unit conversion was skipped; the report declares it explicitly and shows
that the Sharpe-side already fails, so the unit correction cannot flip
the verdict.

Silent corner-cuts are worse than declared ones. Always declare.

### 10. Cross-regime composite rankings; materiality before significance

Whenever an ablation produces per-regime results, use a Borda-style
balanced composite to rank candidates (used in M2 label-scheme ranking
and M6 cross-scheme verdict). Cherry-picking the regime where you happen
to win is forbidden.

Materiality thresholds (is this big enough to matter?) precede
significance tests (is this distinguishable from noise?). M5's protocol
is the reference: trip the 5% sign-flip and 0.1 |ΔSharpe| bars first;
only then consult DM p-values.

---

## The 4 upgrades (post-4A additions)

### 11. Slice + full-panel discipline

Any slice-level verdict that influences a `tested_edge` /
`tested_no_edge` label gets a paired, compute-gated full-panel
confirmation milestone. M3's `xs_rank_vol_21d` and `trend_regime` survivors
should have had this; they don't, and that's flagged as a Phase 4A
near-miss.

### 12. Trial-count ledger as a code artifact

Cumulative trial count across all PRDs lives in `data/ledger.yaml` (see
schema below), written by every runner, audited by CI. The
Bailey-López de Prado deflated-Sharpe N comes from the ledger, not from
hand-counting.

### 13. DSR-aware gates

Gates incorporate deflated Sharpe (Bailey & López de Prado, 2014) as a
second-stage test. Gate-pass requires both pre-committed Sharpe threshold
**and** DSR above zero, with the deflation threshold set against the
cumulative ledger N.

### 14. OOS-only attribution

In-sample feature importance (SHAP, `feature_importances_`, permutation
importance on the train set) is informational only. Decisions about
which features to keep, drop, or propose are driven by OOS attribution
(per-fold ablation, OOS permutation importance) — *the* canonical
finding of M3 is that IS importance does not transfer (Spearman ρ = −0.074).

Project B2 builds the OOS attribution toolkit; until then, per-feature
ablation is the only trustworthy signal.

---

## Engineering practices

Research code is software. The 10+4 research practices above protect the
honesty of the verdict; the engineering practices below protect the
integrity of the code that produces it. Both bind humans and agents.

### 15. Tests land with code, not after

No untested code merges. Each new module ships with unit tests under
`tests/`, named for the module under test (`tests/test_<module>.py`).
Changes to existing modules preserve or extend the existing tests; bug
fixes ship a regression test that fails on the bug before it ships the
fix. The 467-test suite at Phase 4A close is the load-bearing artifact
for safe refactoring — it must grow with the codebase, not lag it.

### 16. 80% line coverage minimum

Project-wide line coverage target is 80%, consistent with the global
rules in `~/.claude/rules/ecc/common/testing.md`. Tooling:
`.venv/bin/pytest tests/ --cov=src --cov-report=term-missing`.

### 17. End-to-end validation notebook for cross-module changes

When a change crosses module boundaries (e.g. a new label scheme in
`features/` is consumed by `backtest/` via a model in `models/`), the PRD
includes a notebook that exercises the change end-to-end on real
fixtures or the lake. Phase 4A's `notebooks/05_phase4a_regime_harness.ipynb`
through `notebooks/09_phase4a_exit_gate.ipynb` are the reference: each
milestone closes with a notebook that demonstrates the change works,
captures the verdict, and survives `nbconvert --execute`.

### 18. CI runs the full suite; red CI blocks merge

Every commit to main runs `.venv/bin/pytest tests/`. A failing test
blocks merge. The drift tests (`tests/test_catalog.py`, the upcoming
`tests/test_ledger.py` and `tests/test_priorities.py`) are CI-gated, not
advisory.

### 19. Pre-commit lint via ruff

`.venv/bin/ruff check src/ tests/ scripts/` and `.venv/bin/ruff format`
before commit. The tooling is in `.venv/bin/`; calling it directly is
the canonical invocation per `CLAUDE.md`.

### 20. Post-task review before marking complete

Before any task in `PRIORITIES.yaml` flips to `status: done`, the
implementer (human or agent) runs a structured review of the deliverable:
- Re-read the code for limitations, bugs, and unintended behaviour
  changes.
- Cross-check the deliverable against the methodology rules above —
  particularly §1 (pre-committed thresholds), §6 (drift contracts),
  §15 (tests landed), §17 (E2E notebook if cross-module).
- **Append any discovered follow-up tasks to `PRIORITIES.yaml`** with
  a `notes` field linking to the commit/PR/notebook where the gap was
  found. Discovered work is captured, not dropped on the floor.
- Note any methodology deviations explicitly in the commit message,
  consistent with §9 (honest declaration of deviations).

For Phase 5 agents this becomes an enforced `PostToolUse` /
`Stop` hook that runs the review against the gate functions. Until
then it is social contract plus this rule.

---

## Application to the agent harness (Phase 5)

Every rule above applies to every agent (R, F, M) with the following
strict additions:

- **Pre-registration is required for every agent-run experiment.** No
  exceptions. The pre-registration record is one ledger entry; the
  result is a second ledger entry referencing the same ID. An
  unregistered result is invalid by convention.
- **The DSR deflation N is computed from the ledger**, not estimated.
  An agent's "best result this week" is automatically deflated by the
  number of comparisons the ledger records that week.
- **Hard guardrails are tested, not advisory** (see
  [`PHASE_5_AGENTS.md`](PHASE_5_AGENTS.md) §"Hard guardrails"). Agents
  may not modify walk-forward split logic, pinned thresholds, or the
  ledger schema. Enforcement is via CI tests + protected paths.
- **Verdicts come from gate functions, not from agent prose.** Weekly
  digests quote the function output verbatim. An agent that paraphrases
  a verdict favourably is committing verdict laundering and the run is
  invalidated.

The methodology contract is the agent harness's compliance specification.
Every Phase 5 agent's system prompt links to this file.

---

## Open questions for the next research PRD

These are deferred until the next PRD's scoping resolves them. They will
become rules 15+ when answered.

- **OOS attribution method.** Permutation importance computed OOS,
  per-fold ablation as canonical, or conformal feature relevance? Pick
  in Project B2.
- **Trial-count ledger schema.** Sketched below; needs to be finalized
  and put under a drift test.
- **Materiality thresholds for non-Sharpe targets.** Classification
  metrics (accuracy, AUC, recall), expected-shortfall improvements,
  calibration errors. Set during Project B1 / C4 PRD drafting.

---

## Reference schemas

### Trial-count ledger (proposed — to be ratified in B1 or B2)

Location: `data/ledger.yaml`. Append-only. CI-audited via a drift test.

```yaml
# Each entry is one trial — one pre-registered, executed, verdict-reported
# comparison.
- id: ledger-2026-06-13-0001
  prd: phase-4a
  milestone: M6
  agent: human               # human | R | F | M
  preregistration: docs/historical/PHASE_4A_REPORT.md#2--the-gate-verbatim
  config_hash: f3b75332527b7b58e952522a1df093bd2dede78320b7a17747d995dcfe06fc49
  n_comparisons: 1           # number of per-regime tests in this trial
  started_at: 2026-06-13T11:00:00Z
  completed_at: 2026-06-13T11:16:43Z
  verdict: gate_failed       # gate_passed | gate_failed | inconclusive
  artifacts:
    - data/phase4a/arima/
  notes: "ARIMA control arm, Phase 4A M6."
```

### Pre-registration record (proposed)

Location: `research/preregistration/{date}-{slug}.yaml`. Created before
the experiment runs; immutable thereafter.

```yaml
id: 2026-07-01-b1-target-drawdown
prd: b1
milestone: B1-M2
hypothesis: "21-day drawdown classification is structurally more learnable than next-bar return on the M6 feature set."
success_criterion: "Per-regime AUC > ARIMA-baseline AUC in ≥ 2 of 3 required regimes (qe_bull, covid, rate_cycle), with bootstrapped 90% CI excluding 0."
materiality_threshold: "ΔAUC > 0.02 per regime"
planned_comparisons: 4       # one aggregate + 3 per-regime
ledger_n_at_preregistration: 73
deflation_threshold: "DSR > 0 at the ledger N at completion"
```

---

*Status: ACTIVE — binding contract for all PRDs and all agent runs.
Updates require a PRD and a code change to any tests that enforce the
practices. Most recent update: 2026-06-17 (initial extraction from
Phase 4A retrospective).*
