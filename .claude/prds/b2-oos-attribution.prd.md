# B2 — OOS-Only Attribution Method

> **Project**: B (Predictive research, post-4A) — sub-project B2.
> **Roadmap**: [`docs/PROJECT_ROADMAP.md`](../../docs/PROJECT_ROADMAP.md) §4 Project B,
> §7 "B2 — OOS-only attribution method", §8 ratified decisions 5 & 7.
> **Methodology** (binding): [`docs/METHODOLOGY.md`](../../docs/METHODOLOGY.md) — esp. §14
> (OOS-only attribution), §4 (contract before consumer), §6 (drift contracts).
> **Problem evidence**: [`docs/PHASE_4A_RETROSPECTIVE.md`](../../docs/PHASE_4A_RETROSPECTIVE.md)
> §"The biggest methodological finding".
> **Backlog tasks**: `B2-M1`, `B2-M2`, `B2-M3` in
> [`docs/PRIORITIES.yaml`](../../docs/PRIORITIES.yaml).

## Problem

Phase 4A's single largest **methodological** finding — distinct from its
no-edge **research** verdict — is that **in-sample feature importance does not
transfer out-of-sample on this problem**. M3 measured the rank correlation
between SHAP importance (computed in-sample) and per-fold ablation lift
(measured out-of-sample) across 7 candidate features and found **Spearman
ρ = −0.074** (`docs/PHASE_4A_RETROSPECTIVE.md`; the M5 forensics confirmed the
asymmetry survives the corrected FRED join — macro features still dominate IS,
the OOS gap is unchanged, DM p = 0.72). In plain terms: the cheapest, most
common signal for deciding "which feature is worth keeping" — SHAP, tree
`feature_importances_`, permutation importance on the train set — is, on this
data and model class, **anti-correlated to slightly-worse-than-random** as a
predictor of OOS contribution.

This is not a curiosity; it is a **blocker for two consumers**:

1. **Every future B/C feature decision.** METHODOLOGY §14 already pins the rule
   ("IS importance is informational only; decisions are driven by OOS
   attribution") but names only **one** trustworthy signal today — per-feature
   ablation — which costs `O(n_features)` full backtests. There is no
   *validated*, cheaper OOS attribution method, and no way to record per-feature
   attribution evidence in the catalog.
2. **The Phase-5 continuous-research harness.** Roadmap §4 Project D **Trigger 2**
   is, verbatim, *"B2's OOS attribution method is in code with B2-M3 catalog
   integration shipped."* The retrospective is explicit: *"If [Agent F] reaches
   for SHAP for triage, the loop is corrupted at the source. Agent F needs an
   OOS-only attribution method ... before Phase 5 can begin."* Until B2 ships,
   Phase 5 cannot start regardless of what Project B1 surfaces.

B2 does **not** ask "is there edge?" (that is B1). B2 asks: **"can we attribute
OOS performance to individual features cheaply and trustworthily?"** It is a
methodology-substrate sub-project — the B-project analog of `A-LEDGER` — that
unblocks honest feature triage for everything downstream.

## Evidence

From M3 (`docs/PHASE_4A_RETROSPECTIVE.md`, `notebooks/08_phase4a_feature_ablation.ipynb`),
on the 5-symbol × 8-year slice, GBM preview (`n_iter=10`), `signed_returns`
labels, the 7 M3 candidate features:

| Signal | What it measures | Correlation with OOS ablation lift |
|---|---|---|
| SHAP importance (in-sample) | IS attribution | Spearman **ρ = −0.074** |
| Per-fold ablation lift (out-of-sample) | OOS attribution | **(this is the reference)** |

Structural facts that shape the method choice:

- **Per-fold ablation already exists and is trusted.** `backtest/ablation.py::run_feature_ablation`
  + `make_add_one_sets` / `make_leave_one_out_sets` produced the M3 lifts. It is
  the canonical OOS signal by construction (it measures the exact thing we care
  about: marginal OOS contribution). Its weakness is **cost** — one full
  walk-forward backtest per ablated feature.
- **OOS permutation importance is the obvious cheap candidate.** It reuses the
  per-fold *already-fit* models (no re-fit): for each test fold and each
  feature, permute that feature's column in the **test** matrix, re-`predict`,
  and measure the degradation in the OOS metric. Cost is `O(n_features)`
  predict-passes, not `O(n_features)` backtests — roughly two orders of
  magnitude cheaper than ablation. **The open question is whether it agrees with
  ablation** — i.e. whether it is a faithful, cheap proxy for the gold standard.
  That agreement is precisely what B2 measures.
- **Conformal feature relevance** (the third shortlisted method) is
  theory-attractive but the least established in quantitative finance and the
  highest implementation risk; two methods already give the cross-check B2 needs.

The "inverse of the broken IS signal" framing in `PRIORITIES.yaml` is the design
target: IS importance scored ρ = −0.074 against OOS ablation; B2 wants a method
that scores **ρ ≥ 0.5** against the same OOS ablation reference.

## Users

- **Primary**: the researcher, deciding which features to keep/drop/propose in
  B1, B3, C-series work — today forced to choose between expensive ablation and
  untrustworthy SHAP.
- **Secondary (the blocking consumer)**: the **Phase-5 continuous-research
  agent pair** (Agent F, expanded to all candidate-generation artifact types per
  ROADMAP §4 Project D). B2's `attribution.py` API and the catalog
  `attribution_status` field are the contracts Agent F reads and calls; B2-M3 is
  **Phase-5 Trigger 2** in code.
- **Not for**: production traders or live capital. B2 is offline research
  tooling. It produces no prediction, no Sharpe claim, and trades no capital.

## Hypothesis

We believe that **OOS permutation importance (test-fold permutation, reusing the
per-fold fitted models) produces a feature-importance ranking that agrees with
the per-fold-ablation OOS signal** — for **the researcher and the future
continuous-agent pair** — closing the gap that IS importance (ρ = −0.074) cannot.

We'll know we're right when, run on the **M6 final 25-column feature set**,
OOS permutation importance ranks features at **Spearman ρ ≥ 0.50** against
per-fold ablation lifts, with the rank-correlation **distinguishable from the
ρ = 0 null at p < 0.05** (permutation test of the correlation). All numeric
thresholds are pinned in "Success Metrics" below before any compute touches B2
(METHODOLOGY §1) and reproduced verbatim in `b2_attribution_gate` (METHODOLOGY §2).

If OOS permutation importance does **not** clear ρ ≥ 0.50, the verdict is
**"no validated cheap proxy; per-fold ablation remains the sole canonical OOS
signal"** — itself a valid, pre-committed outcome: it keeps ablation as the only
trusted method (METHODOLOGY §14 unchanged) and feeds the conditional path of
implementing conformal feature relevance as the deferred third method (see
"Sequencing notes").

## Success Metrics

The deliverable is a **method** and its **validation**, not a strategy edge.
The gate is therefore an *agreement* gate (does the cheap method reproduce the
trusted one?), not a Sharpe gate. **All numeric thresholds are pinned here
before any compute (METHODOLOGY §1) and are the source of truth reproduced in
`b2_attribution_gate` (METHODOLOGY §2).** Significance for the rank correlation
is a **permutation test** (≥ 10,000 random relabelings of one ranking) of the
Spearman ρ against the ρ = 0 null.

| # | Claim | Measured on | Statistic | Materiality (pinned) | Significance | Reference |
|---|---|---|---|---|---|---|
| G1 | OOS permutation importance agrees with per-fold ablation | M6 25-column feature set, 5×8 slice (matches nb08) | Spearman ρ between the two feature rankings | **ρ ≥ 0.50** | permutation-test p < 0.05 | per-fold ablation lift = ground truth |
| G2 | The systematized ablation reproduces the M3 result (port-correctness) | the 7 nb08 candidate features | Spearman ρ vs nb08's published per-feature lifts | **ρ ≥ 0.90** | — (reproducibility check, not a research claim) | `notebooks/08_phase4a_feature_ablation.ipynb` |
| G3 | The IS contrast still holds (sanity floor) | same 7 features | Spearman ρ between SHAP (IS) and ablation (OOS) | reported, expected **≤ 0.1** | — | reproduces ρ = −0.074 |

Notes on the metric choices (resolving METHODOLOGY §"Open questions" →
"OOS attribution method"):

- **G1 is the gate.** ρ ≥ 0.50 is pre-committed as the agreement bar — the
  midpoint between "no relationship" (0) and "strong agreement" (≈ 0.7+), chosen
  so a method that is *directionally useful but noisy* still passes while a
  method no better than IS importance (ρ ≈ 0) fails. It is the explicit inverse
  of the broken IS signal (ρ = −0.074 → ρ ≥ 0.50).
- **G2 guards the port.** Because per-fold ablation is the *reference* signal,
  B2's systematized re-implementation must reproduce M3's numbers before it can
  be trusted as ground truth for G1. A high ρ here is a software-correctness
  check (did we re-implement ablation faithfully?), **not** a research finding;
  it is reported as such.
- **G3 is the sanity floor.** Re-deriving the SHAP-vs-ablation ρ on the same
  features confirms the problem B2 exists to solve is still present in the
  harness as used; if G3 came back high, the premise would be wrong and B2
  should stop.
- **Deflation does not apply.** B2 makes no Sharpe/return claim, so DSR is
  undefined; B2 does **not** depend on `A-DSR-GATE`. The ledger still records
  B2's runs (see Sequencing notes) for the audit trail, with `n_comparisons`
  set to the number of *validated methods* (1 — OOS permutation; ablation is the
  reference, not a tested claim), so B2 contributes minimally to the
  cross-PRD deflation `N` rather than inflating it.

## Scope

**MVP** — the three milestones below, executed in order, reusing the existing
substrate (harness, walk-forward splits, `run_feature_ablation`, regime
detector, catalog, ledger, `backtest/statistics`). **No new data, no new model
class, no new universe** — B2 attributes performance on the *existing* M6
feature set; only the *attribution machinery* is new.

1. **B2-M1 — Method shortlist + theory writeup.** `docs/concepts/oos-attribution.md`:
   states the ρ = −0.074 problem, the three shortlisted methods with their math
   (OOS permutation importance, per-fold ablation as canonical signal, conformal
   feature relevance), the **decision to implement the first two and defer the
   third** with rationale, the exact OOS-permutation algorithm (test-fold
   permutation reusing per-fold fitted models), and the validation protocol that
   B2-M2 executes. No code; this is the concept contract (METHODOLOGY §4 —
   contract before consumer).
2. **B2-M2 — Implementation + sanity test.** `src/quant/backtest/attribution.py`
   exposing (a) `per_fold_ablation_attribution(...)` (a thin, reusable wrapper
   over `run_feature_ablation` returning a per-feature OOS-lift ranking — the
   canonical signal), (b) `oos_permutation_importance(...)` (test-fold
   permutation reusing per-fold models, returning a per-feature ranking), and
   (c) `b2_attribution_gate(...)` implementing G1–G3 verbatim with all
   thresholds as pinned defaults. Tests land with the module (METHODOLOGY §15);
   the sanity test asserts G1 (ρ ≥ 0.50) and G2 (ρ ≥ 0.90) on small synthetic +
   slice fixtures. A cross-module E2E notebook
   (`notebooks/12_b2_oos_attribution.ipynb`, number to be confirmed against the
   live notebook sequence at build time) exercises the method on the real M6
   feature set and renders the verdict (METHODOLOGY §17).
3. **B2-M3 — Documentation + catalog integration.** Add an `attribution_status`
   field to `FeatureRecord` (`src/quant/features/catalog.py`) and to every entry
   in `src/quant/features/catalog.yaml`, enum
   `{none, ablation_only, oos_permutation, both, agreed}`, default `none`. Extend
   `tests/test_catalog.py` to assert the new field's drift contract in both
   directions (METHODOLOGY §6). Populate the field for the features B2-M2
   attributed; the rest stay `none`. This milestone is **Phase-5 Trigger 2**.

**Out of scope**

- **Conformal feature relevance** — the deferred third method. Implementing it is
  a *conditional* follow-on (drafted only if G1 fails, or as a separate enhancement
  PRD), not a B2 MVP deliverable.
- **New data / ingestors / universe / model classes** — B2 attributes the
  existing M6 GBM on the existing feature set. Any surfaced need is a *finding*,
  not a B2 deliverable.
- **Acting on attributions** — B2 ranks features; it does **not** retrain,
  re-select, or propose a new feature set. Using B2's rankings to change the
  model matrix is a downstream B1/B3 decision.
- **Regime-conditional attribution** — B2 validates aggregate-OOS attribution
  first. Per-regime attribution is a natural extension flagged in Open Questions,
  not an MVP requirement.
- **The continuous-agent harness (Phase 5)** — B2 ships the *contract* Agent F
  needs (the API + the catalog field) but builds no agents.

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->

| # | Milestone | Outcome | PRIORITIES task | Depends on |
|---|---|---|---|---|
| 1 | Method shortlist + theory | `docs/concepts/oos-attribution.md` states the problem, the 3 methods, the 2 chosen + 1 deferred, the OOS-permutation algorithm, and the validation protocol | `B2-M1` | `B2-PRD` |
| 2 | Implementation + sanity test | `attribution.py` + `b2_attribution_gate` exist; the sanity test confirms OOS permutation agrees with ablation at ρ ≥ 0.50 (G1) on the M6 feature set, with the port reproducing M3 (G2) | `B2-M2` | `B2-M1` |
| 3 | Catalog integration | `attribution_status` field added to `catalog.{py,yaml}`, drift-tested both ways; populated for attributed features. **Phase-5 Trigger 2 satisfied.** | `B2-M3` | `B2-M2` |
| Gate | OOS permutation importance reproduces the ablation ranking at Spearman ρ ≥ 0.50 (p < 0.05) on the M6 feature set | Binary. **Pass** → a validated cheap OOS attribution method is in code + catalog; Trigger 2 met. **Fail** → ablation remains the sole canonical signal; conformal-relevance follow-on is triggered. | — | — |

## Pre-committed gate (verbatim — implemented in B2-M2 as `b2_attribution_gate`)

The gate function is the source of truth; this prose describes it
(METHODOLOGY §2). Given two per-feature ranking dicts — `permutation_rank` and
`ablation_rank` over a common feature set — and the SHAP-vs-ablation contrast,
it returns `gate_passed: bool` computed as the conjunction of:

1. **Agreement materiality (G1)** — `spearman_rho(permutation_rank, ablation_rank)
   >= rho_threshold` (default `0.50`).
2. **Agreement significance (G1)** — the permutation test of that Spearman ρ
   against the ρ = 0 null has `p < alpha` (default `alpha = 0.05`,
   `n_permutations >= 10_000`).
3. **Port reproducibility (G2)** — when run against the published nb08 lifts
   over the 7 M3 candidates, `spearman_rho(systematized_ablation, nb08_lifts)
   >= reproduction_threshold` (default `0.90`). (Asserted in the sanity test
   that gates the merge; reported alongside the verdict.)

`rho_threshold`, `alpha`, `n_permutations`, `reproduction_threshold`, and the
feature set are all function arguments with the defaults pinned above — changing
any of them after a result is visible invalidates the run and requires a new
ledger entry (METHODOLOGY §1). G3 (the SHAP-vs-ablation contrast) is reported by
the function for context but is not part of the pass/fail conjunction.

## Open Questions

- [ ] **Does the harness expose per-fold fitted models + test matrices?** OOS
      permutation importance reuses the *already-fit* per-fold model and the test
      `X` (no re-fit). `run_portfolio_backtest`'s `BacktestResult` currently
      surfaces `oos_returns` / `oos_forecast_errors` but **not** the per-fold
      models or test matrices. **Resolved before B2-M2 code, surfaced in B2-M1**:
      either (a) `attribution.py` runs its own lightweight walk-forward that
      retains `(fold_model, X_test)` per fold and computes *both* signals from
      the identical fold structure (the fair-comparison design, preferred), or
      (b) the harness is extended to optionally retain fold artifacts. Option (a)
      avoids touching the load-bearing harness and keeps ablation and permutation
      on identical folds; flag rather than silently re-fit.
- [ ] **Metric for "degradation" under permutation.** Permutation importance
      needs a scalar OOS metric to degrade. Pinned to the **same metric the
      ablation lift uses** (OOS Sharpe of the simulated `sign(pred)` strategy, the
      Phase-4A convention) so G1 compares like with like; an alternative
      (forecast-error MSE) is reported as a secondary diagnostic only. Confirmed
      in B2-M1, frozen before B2-M2.
- [ ] **Slice vs full panel for the validation (METHODOLOGY §11).** G1/G2 are
      pinned to the **5-symbol × 8-year slice** to match the nb08 M3 reference
      exactly (a like-for-like reproduction is the whole point of G2). A
      full-panel agreement check is a *desirable* confirmation but is **not** an
      MVP gate, because B2 validates a *method*, not an edge — the slice is the
      correct comparison surface for "does the cheap proxy match the gold
      standard." A full-panel extension is flagged as a follow-up, not deferred
      silently.
- [ ] **`attribution_status` semantics for `both` vs `agreed`.** `both` = both
      signals computed for the feature but they disagree (rank/sign mismatch);
      `agreed` = both computed and consistent. The exact per-feature agreement
      rule (e.g. same sign of lift AND both in the top/bottom tercile) is pinned
      in B2-M3 before the field is populated, under a new ledger entry if it
      changes after results are seen.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| OOS permutation importance does **not** agree with ablation (G1 fails) | Medium | Medium | This is a *valid, pre-committed* outcome (METHODOLOGY §5): ablation stays the sole canonical signal, and the conformal-relevance third method becomes the conditional follow-on. The negative is documented, not hidden. |
| G1 passes on the slice but the cheap method diverges at full panel | Medium | Medium | Slice is the correct *validation* surface (like-for-like vs nb08); the full-panel check is a flagged follow-up. The catalog `attribution_status` records *which* signal(s) backed each feature, so a later full-panel disagreement is visible, not silent. |
| Building a private walk-forward in `attribution.py` re-introduces a leakage bug the main harness already solved | Low | **Very High** | Option (a) reuses the *exact* split generator and purge/embargo from `walkforward.py`/`harness.py` — no re-implementation of split logic (`backtest/CLAUDE.md` invariants). Harness self-tests (random → ~0 edge, leaky → caught) must stay green; attribution code adds no new split path. |
| Permutation importance is itself noisy (single permutation per feature) | Medium | Medium | Average over `n_repeats` permutations per feature (pinned in B2-M1); report the per-feature standard error. The *ranking* (what G1 scores) is more stable than point estimates. |
| The systematized ablation does not reproduce nb08 (G2 fails) | Low | High | G2 is a software-correctness gate that **blocks merge** — a failed reproduction means the port is wrong and must be fixed before G1 is even meaningful. Pinned at ρ ≥ 0.90. |
| Catalog field addition breaks the existing 27-entry drift test | Low | Low | `attribution_status` ships with default `none` so existing YAML entries need no edit to remain valid (pydantic default); the drift test is *extended*, not rewritten, and asserts both directions (METHODOLOGY §6). |
| B2 ships but Phase-5 Trigger 1 (a B-cycle gate pass) never fires | Medium | Low | Independent by design: B2 satisfies Trigger 2 only; D-GATE requires **both** triggers (ROADMAP §8 decision 7). B2's value (validated cheap attribution for all B/C feature work) does not depend on Phase 5 starting. |

## Sequencing notes

- **B2-M1 ships the concept contract before B2-M2 writes code** (METHODOLOGY §4).
  The theory doc fixes the OOS-permutation algorithm and the validation protocol;
  the implementation is the consumer.
- **B2-M2 ships `b2_attribution_gate` with G1–G3 pinned before any attribution
  is scored** (METHODOLOGY §2). No ρ is computed against an unwritten gate.
- **B2-M3 is built last and is Phase-5 Trigger 2.** The catalog field is the
  contract Agent F reads; per METHODOLOGY §4 it could arguably precede M2, but the
  field's *enum semantics* (`both`/`agreed`) depend on M2's two-signal output, so
  M2 → M3 is the correct order here (the contract's shape is known from M1; its
  value semantics need M2).
- **B2 does NOT depend on `A-DSR-GATE`** (no Sharpe claim → no deflation). It
  depends only on `A-LEDGER` (done), already encoded in `PRIORITIES.yaml`.
- **Ledger discipline.** Each B2-M2/M3 run that produces a verdict appends a
  ledger entry via `quant.ledger` (the A-LEDGER-RUNNERS pattern), with
  `n_comparisons = 1` (the single validated method) and `verdict` from
  `b2_attribution_gate`. B2 is largely an infrastructure sub-project, so its
  contribution to the cross-PRD deflation `N` is intentionally small.
- **Conditional follow-on (METHODOLOGY §5, binding):**
  - If **G1 passes** → a validated cheap OOS attribution method is in code +
    catalog; Trigger 2 is met; no conformal-relevance work is initiated unless
    separately motivated.
  - If **G1 fails** → draft a **conformal-feature-relevance** follow-on (the
    deferred third method) as the next attempt at a cheap proxy; per-fold
    ablation remains the sole canonical signal in the interim. A failed G1 cannot
    be revived by re-tuning `rho_threshold` after the fact — that requires a new
    PRD and a new ledger entry.
- **Project-B closeout.** Project B has no `B-CLOSE` task yet (only B1/B2 are
  drafted). When one is created, B2-M3 must be added to its `depends_on`
  (AGENT_OPERATION "Project closeout" corollary / METHODOLOGY §21). Flagged as a
  discovered follow-up.

---
*Status: DRAFT (2026-06-23) — pre-commitment for Project B2. Thresholds in
"Success Metrics" and "Pre-committed gate" (ρ ≥ 0.50, p < 0.05, ρ ≥ 0.90) are
frozen on ratification; changes require a PRD revision and a new ledger entry,
not an in-flight override. Next: `/plan` turns B2-M1 into an implementation plan.*
