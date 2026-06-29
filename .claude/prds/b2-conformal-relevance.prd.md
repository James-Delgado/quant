# B2 — Conformal Feature Relevance (cheap-OOS-attribution, attempt 2)

> **STATUS: DRAFT — pre-committed thresholds need human ratification before any compute (METHODOLOGY §1).**
>
> This PRD pins a numeric gate (ρ ≥ 0.50, p < 0.05, coverage ±0.05, ρ ≥ 0.90)
> for a method that has **not been run**. Pinning thresholds is consequential
> (METHODOLOGY §1) and the gate is the source of truth a future
> `b2_conformal_gate` will reproduce verbatim (METHODOLOGY §2). Therefore the
> thresholds below are **proposed, pending human ratification** — they are *not*
> locked until a human signs off. Do **not** schedule compute against this PRD
> until it is ratified.

> **Project**: B (Predictive research, post-4A) — sub-project B2, conditional follow-on.
> **Roadmap**: [`docs/PROJECT_ROADMAP.md`](../../docs/PROJECT_ROADMAP.md) §4 Project B,
> §7 "B2 — OOS-only attribution method", §8 ratified decisions 5 & 7.
> **Methodology** (binding): [`docs/METHODOLOGY.md`](../../docs/METHODOLOGY.md) — esp. §1
> (pre-commit thresholds), §2 (gate verbatim in code), §5 (conditional sub-milestones
> + binding skip path), §14 (OOS-only attribution), §4 (contract before consumer),
> §6 (drift contracts).
> **Concept contract**: [`docs/concepts/oos-attribution.md`](../../docs/concepts/oos-attribution.md)
> §3 "Conformal feature relevance (deferred)" and §"The pick (pre-commitment)".
> **Parent PRD**: [`.claude/prds/b2-oos-attribution.prd.md`](b2-oos-attribution.prd.md)
> ("Sequencing notes" → "Conditional follow-on").
> **Trigger evidence**: B2 G1 FAILED on the real 5×8 slice — Spearman
> ρ(permutation, ablation) = **−0.004** (p = 0.505), ledger entry
> `ledger-2026-06-27-0005`.
> **Backlog task**: `B2-CONFORMAL-PRD` in
> [`docs/PRIORITIES.yaml`](../../docs/PRIORITIES.yaml) (this PRD is its deliverable).

## Problem

Project B2 set out to validate a **cheap** out-of-sample feature-attribution
method against the only trusted-by-construction OOS signal — per-fold ablation —
because per-fold ablation costs `O(n_features)` full walk-forward backtests and is
too expensive to run on every feature decision. B2's MVP picked **OOS permutation
importance** as the cheap proxy under test (the parent PRD; `docs/concepts/oos-attribution.md`
§"The pick"), with per-fold ablation as the ground-truth reference.

**That proxy failed its pre-committed gate.** On the real 5-symbol × 8-year slice,
the systematized B2-M2 run measured the Spearman rank correlation between OOS
permutation importance and per-fold ablation lift at

> **Spearman ρ = −0.004 (permutation-test p = 0.505)**

far below the pinned **ρ ≥ 0.50** bar (ledger `ledger-2026-06-27-0005`; consumed
by the B2-M3 catalog population run). The G3 sanity floor confirmed the underlying
problem is still present and is *why* the proxy failed: IS importance does not
transfer (G3 SHAP-vs-ablation ρ = 0.039, reproducing the M3 ρ = −0.074 finding).
**OOS permutation importance is not a validated cheap proxy for per-fold ablation
on this problem.**

Per METHODOLOGY §5 (conditional sub-milestones carry a **binding** skip/trigger
path) and the parent B2 PRD's "Conditional follow-on" clause quoted verbatim:

> *If **G1 fails** → draft a **conformal-feature-relevance** follow-on (the
> deferred third method) as the next attempt at a cheap proxy; per-fold ablation
> remains the sole canonical signal in the interim. A failed G1 cannot be revived
> by re-tuning `rho_threshold` after the fact — that requires a new PRD and a new
> ledger entry.*

This PRD **is that new PRD**. It proposes **conformal feature relevance** — the
third and last method on the B2-M1 shortlist (`docs/concepts/oos-attribution.md`
§3), deferred at MVP as theory-attractive but the highest implementation risk —
as the second attempt at a cheap, trustworthy OOS-attribution proxy.

**What does *not* change.** Per-fold ablation **remains the sole canonical OOS
attribution signal** (METHODOLOGY §14, unchanged). Nothing in this PRD downgrades
ablation; conformal relevance must *agree with* ablation to be adopted, exactly as
permutation had to. And **Phase-5 Trigger 2 is already satisfied** — B2-M3 shipped
`attribution.py` (`per_fold_ablation_attribution`, the validated ablation
reference) and the catalog `attribution_status` field (ROADMAP §4 Project D
Trigger 2). The cheap proxy failing does *not* un-ship that contract. Conformal
relevance is therefore a **research enhancement** (a cheaper triage signal for
all downstream B/C feature work), not a Phase-5 gate.

## Evidence

From B2-M2/M3 (`src/quant/backtest/attribution.py`,
`notebooks/12_b2_oos_attribution.ipynb` per the live sequence, ledger
`ledger-2026-06-27-0005`), on the 5-symbol × 8-year slice, GBM preview
(`n_iter=10`), `signed_returns` labels, the M6 25-column model-input feature set:

| Signal | What it measures | Spearman ρ vs per-fold ablation (OOS) | Verdict |
|---|---|---|---:|
| SHAP / `feature_importances_` (in-sample) | IS attribution | **−0.074** (M3) | broken (METHODOLOGY §14) |
| OOS permutation importance (test-fold permute, reuse fold models) | OOS attribution, cheap | **−0.004** (p = 0.505) | **G1 FAILED** (B2) |
| Per-fold ablation lift (out-of-sample) | OOS attribution, expensive | **(this is the reference)** | canonical (§14) |
| **Conformal feature relevance** | OOS attribution, cheap (proposed) | **— (this PRD measures it)** | under test |

Structural facts that shape this attempt:

- **The reference and the cross-checks already exist and are validated.**
  B2-M2 shipped `per_fold_ablation_attribution(...)` (the canonical OOS-lift
  ranking) and validated it against nb08's published M3 lifts at the pinned
  ρ ≥ 0.90 reproduction bar (B2 G2). This conformal PRD **reuses that exact
  reference** as ground truth — it does *not* re-port ablation, removing the
  single largest leakage/correctness risk from the prior attempt.
- **Why permutation failed is informative for conformal.** OOS permutation breaks
  a feature's marginal relationship with the target on the *test* slice while
  reusing a model fit on the *train* slice. On a regime-structured, mean-reverting
  universe where the GBM's fitted response surface does not transfer (the Phase-4A
  finding), permuting an input the model barely uses OOS produces near-zero,
  noise-dominated degradation — hence ρ ≈ 0. A conformal signal that scores
  relevance via the **calibrated predictive-set width / nonconformity quantile on
  held-out data** is a *different statistical lens* (distribution-free, error-bar
  based) and may capture marginal OOS contribution where a point-prediction
  permutation does not. That is the hypothesis; it is not assumed.
- **Conformal's known weakness is this exact data.** Standard split conformal
  assumes **exchangeability**, which temporally-dependent, regime-switching return
  series violate (`docs/concepts/oos-attribution.md` §3). The method choice must
  therefore use a **time-series-aware conformal variant** (block / weighted /
  ensemble-batch conformal) on the existing purged walk-forward fold structure,
  not vanilla i.i.d. split conformal. This is the central design risk, addressed
  in Scope and Risks.

The design target is unchanged from the parent PRD and is the **explicit inverse
of the broken IS signal**: IS importance scored ρ = −0.074 against OOS ablation;
permutation scored ρ = −0.004; conformal relevance must score **ρ ≥ 0.50** against
the same OOS ablation reference to be adopted.

## Users

- **Primary**: the researcher, deciding which features to keep/drop/propose in
  B1, B3, C-series work — today, after the permutation failure, forced back onto
  expensive per-fold ablation as the *only* trusted OOS signal. A validated
  conformal proxy restores a cheap triage option.
- **Secondary**: the **Phase-5 continuous-research agent pair** (Agent F). Note
  Trigger 2 is **already met** (B2-M3 shipped the `attribution.py` API + catalog
  field); conformal relevance, if validated, becomes an *additional*
  `attribution_status` provenance value Agent F can record — not a new trigger.
- **Not for**: production traders or live capital. Like all of B2, this is offline
  research tooling. It produces no prediction, no Sharpe claim, and trades no
  capital.

## Hypothesis

We believe that **conformal feature relevance — measured as the change in a
time-series-aware conformal predictor's calibrated set width / nonconformity
quantile when a feature is withheld, computed on the held-out OOS slice reusing
the per-fold fitted models — produces a feature-importance ranking that agrees
with the per-fold-ablation OOS signal** — for **the researcher and the future
continuous-agent pair** — closing the gap that IS importance (ρ = −0.074) and OOS
permutation importance (ρ = −0.004) both failed to close.

We'll know we're right when, run on the **M6 25-column model-input feature set**
(the same surface as B2 G1; `docs/concepts/oos-attribution.md` §"The attributed
set"), conformal feature relevance ranks features at **Spearman ρ ≥ 0.50** against
per-fold ablation lifts, with the rank-correlation **distinguishable from the
ρ = 0 null at p < 0.05** (permutation test of the correlation), **and** the
conformal predictor demonstrates valid OOS coverage (empirical coverage within
±0.05 of nominal) so the relevance signal is meaningful. All numeric thresholds
are **proposed, pending ratification** in "Success Metrics" below (METHODOLOGY §1)
and, once ratified, will be reproduced verbatim in `b2_conformal_gate`
(METHODOLOGY §2).

If conformal feature relevance does **not** clear ρ ≥ 0.50, the verdict is
**"no validated cheap OOS-attribution proxy among the three shortlisted methods;
per-fold ablation remains the sole canonical OOS signal"** — itself a valid,
pre-committed outcome (METHODOLOGY §5). Because conformal was the **last** method
on the B2-M1 shortlist, a second failure is a *terminal* result for the cheap-proxy
search as scoped: any further attempt requires a brand-new method shortlist under
a brand-new PRD (see "Sequencing notes").

## Success Metrics

> **DRAFT — thresholds below are proposed, pending human ratification
> (METHODOLOGY §1).** They pre-commit a gate for an unrun method; a human must
> ratify them before any compute touches this experiment. Once ratified they
> freeze and become the source of truth reproduced verbatim in `b2_conformal_gate`
> (METHODOLOGY §2); thereafter changing any of them after a result is visible
> invalidates the run and requires a new PRD + ledger entry.

The deliverable is a **method** and its **validation**, not a strategy edge. The
gate is therefore an *agreement* gate (does the cheap method reproduce the trusted
one?), not a Sharpe gate — identical in shape to the parent B2 gate, so that
"conformal relevance" and "OOS permutation" are judged on the *same bar* against
the *same reference*. Significance for the rank correlation is a **permutation
test** (≥ 10,000 random relabelings of one ranking) of the Spearman ρ against the
ρ = 0 null.

| # | Claim | Measured on | Statistic | Materiality (proposed, pending ratification) | Significance | Reference |
|---|---|---|---|---|---|---|
| **G1** | Conformal feature relevance agrees with per-fold ablation | M6 25-column model-input set, 5×8 slice (matches B2 G1 / nb08) | Spearman ρ between the two feature rankings | **ρ ≥ 0.50** | permutation-test **p < 0.05** | `per_fold_ablation_attribution` lift = ground truth |
| **G2** | The conformal predictor achieves valid OOS coverage (method-correctness) | the same OOS folds | empirical coverage of the conformal sets vs nominal `1 − α_conf` | **within ±0.05 of nominal** | — (validity check, blocks merge) | conformal coverage guarantee |
| **G3** | The ablation reference reproduces B2-M2 / M3 (port-correctness) | the 7 nb08 candidate features | Spearman ρ vs B2-M2's validated lifts (themselves ρ ≥ 0.90 vs nb08) | **ρ ≥ 0.90** | — (reproducibility check) | `notebooks/08_phase4a_feature_ablation.ipynb`, B2-M2 |
| **G4** | The IS contrast still holds (sanity floor) | same 7 features | Spearman ρ between SHAP (IS) and ablation (OOS) | reported, expected **≤ 0.1** | — | reproduces ρ = −0.074 / B2 G3 = 0.039 |

Notes on the metric choices (this *re-uses* the parent B2 gate design verbatim
where possible — only G2, conformal-coverage validity, is new):

- **G1 is the gate, and the bar is deliberately unchanged.** ρ ≥ 0.50 is the same
  agreement bar OOS permutation had to clear — the midpoint between "no
  relationship" (0) and "strong agreement" (≈ 0.7+), chosen so a *directionally
  useful but noisy* method passes while a method no better than the broken IS
  signal (ρ ≈ 0) fails. **Holding the bar fixed across the two cheap-proxy attempts
  is itself a methodology safeguard**: it prevents lowering the bar to "rescue"
  conformal after permutation failed at ρ = −0.004 (METHODOLOGY §1 — a failed gate
  cannot be revived by re-tuning the threshold).
- **G2 is new and conformal-specific.** Conformal's entire value proposition is its
  coverage guarantee; if the implementation does not even achieve nominal coverage
  on these temporally-dependent OOS folds, the relevance signal derived from set
  width is meaningless. G2 is a **method-correctness gate that blocks merge** —
  it answers "did we implement a *valid* conformal predictor on this data?" before
  G1 ("does its relevance ranking agree with ablation?") is allowed to matter. It
  is the conformal analog of the parent gate's port-reproducibility check.
- **G3 guards the reference (now cheaper than before).** Because per-fold ablation
  is the *reference* signal, the conformal milestone must confirm it is using the
  **already-validated** B2-M2 `per_fold_ablation_attribution` (which itself passed
  ρ ≥ 0.90 vs nb08). This is an integration/reproducibility check, **not** a new
  research finding.
- **G4 is the sanity floor.** Re-deriving the SHAP-vs-ablation ρ on the same 7
  features confirms the problem this PRD exists to solve is still present in the
  harness as used; if G4 came back high, the premise would be wrong and the
  conformal attempt should stop. Reported by the gate function for context, **not**
  part of the pass/fail conjunction.
- **Deflation does not apply.** Like the parent B2, conformal relevance makes no
  Sharpe/return claim, so DSR is undefined and this PRD does **not** depend on
  `A-DSR-GATE`. The ledger still records each run for the audit trail with
  `n_comparisons = 1` (the single cheap method under test; ablation is the
  reference, not a tested claim), so this attempt contributes minimally to the
  cross-PRD deflation `N`.

## Scope

**MVP** — the three milestones below, executed in order, **reusing the existing
substrate** (harness, walk-forward splits, `per_fold_ablation_attribution`, the
B2-M2 fair-comparison fold structure, regime detector, catalog, ledger,
`backtest/statistics`). **No new data, no new model class, no new universe** — this
attributes performance on the *existing* M6 feature set; only the *attribution
machinery* (the conformal layer) is new.

1. **B2-CONF-M1 — Conformal method writeup + algorithm freeze.** Extend
   `docs/concepts/oos-attribution.md` (or a sibling `conformal-relevance.md`,
   decided in M1 — flagged in Open Questions) to: state the permutation G1
   failure as the trigger, specify the **exact time-series-aware conformal
   variant** chosen (block / weighted / ensemble-batch conformal on the purged
   walk-forward folds), the exact relevance statistic (change in calibrated set
   width / nonconformity quantile when a feature is withheld, reusing the per-fold
   fitted models so the cost stays `O(n_features)` predicts, *not* backtests), and
   the validation protocol B2-CONF-M2 executes. **No code**; this is the concept
   contract (METHODOLOGY §4 — contract before consumer). The exchangeability
   mitigation is fixed here, before any code, so the implementation cannot quietly
   fall back to vanilla split conformal.
2. **B2-CONF-M2 — Implementation + sanity test.** Extend
   `src/quant/backtest/attribution.py` with (a) `conformal_feature_relevance(...)`
   (the conformal relevance ranking, reusing the B2-M2 per-fold
   `(fold_model, X_test)` fair-comparison structure — **no new split path**), and
   (b) `b2_conformal_gate(...)` implementing G1–G4 verbatim with all thresholds as
   pinned defaults (**only after ratification**). It **reuses** B2-M2's
   `per_fold_ablation_attribution` as the ground-truth reference — no ablation
   re-port. Tests land with the module (METHODOLOGY §15); the sanity test asserts
   G1 (ρ ≥ 0.50), G2 (coverage within ±0.05), and G3 (ρ ≥ 0.90) on small synthetic
   + slice fixtures. A cross-module E2E notebook
   (`notebooks/13_b2_conformal_relevance.ipynb`, number to be confirmed against the
   live notebook sequence at build time) exercises the method on the real M6
   feature set and renders the verdict (METHODOLOGY §17).
3. **B2-CONF-M3 — Catalog provenance (conditional on G1 pass).** *Only if G1
   passes*: extend the `attribution_status` enum on `FeatureRecord`
   (`src/quant/features/catalog.py`) to record conformal provenance (e.g. a
   `conformal` value, or a `+conformal` agreement marker — exact enum extension
   pinned in M3 before population, under a new ledger entry if it changes after
   results are seen). Extend `tests/test_catalog.py` to assert the new value's
   drift contract in both directions (METHODOLOGY §6). If G1 **fails**, M3 is
   **skipped** (the binding skip path, METHODOLOGY §5) — there is no validated
   conformal signal to record — and the verdict is documented instead.

**Out of scope**

- **A fourth attribution method.** Conformal is the last method on the B2-M1
  shortlist. If it fails too, surfacing a *new* candidate is a separate PRD, not a
  deliverable here.
- **Re-porting per-fold ablation.** B2-M2 already validated the ablation reference
  (ρ ≥ 0.90 vs nb08). This PRD consumes it; it does not rebuild it.
- **New data / ingestors / universe / model classes** — conformal relevance
  attributes the existing M6 GBM on the existing feature set. Any surfaced need is
  a *finding*, not a deliverable.
- **Acting on attributions** — this ranks features; it does **not** retrain,
  re-select, or propose a new feature set. Using the rankings to change the model
  matrix is a downstream B1/B3 decision.
- **Regime-conditional attribution** — aggregate-OOS attribution is validated
  first; per-regime conformal attribution is a flagged extension, not MVP.
- **The continuous-agent harness (Phase 5)** — Trigger 2 is already met by B2-M3;
  this PRD builds no agents and does not re-gate Phase 5.

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->

| # | Milestone | Outcome | PRIORITIES task | Depends on |
|---|---|---|---|---|
| 1 | Conformal method writeup + algorithm freeze | The concept doc states the trigger, the exact time-series-aware conformal variant, the relevance statistic, and the validation protocol — before any code (METHODOLOGY §4) | `B2-CONF-M1` | `B2-CONFORMAL-PRD` (ratified) |
| 2 | Implementation + sanity test | `conformal_feature_relevance` + `b2_conformal_gate` exist; the sanity test confirms conformal relevance agrees with ablation at ρ ≥ 0.50 (G1) with valid coverage (G2) on the M6 set, reusing the validated ablation reference (G3) | `B2-CONF-M2` | `B2-CONF-M1` |
| 3 | Catalog provenance (conditional on G1 pass) | `attribution_status` records conformal provenance, drift-tested both ways; populated for attributed features. **Skipped if G1 fails.** | `B2-CONF-M3` | `B2-CONF-M2` (G1 pass) |
| Gate | Conformal feature relevance reproduces the ablation ranking at Spearman ρ ≥ 0.50 (p < 0.05) with valid OOS coverage on the M6 feature set | Binary. **Pass** → a validated cheap OOS-attribution method finally exists in code + catalog. **Fail** → ablation remains the sole canonical signal; the three-method shortlist is exhausted (terminal for the cheap-proxy search as scoped). | — | — |

## Pre-committed gate (verbatim — to be implemented in B2-CONF-M2 as `b2_conformal_gate`)

> **DRAFT — the thresholds in this gate are proposed, pending human ratification
> (METHODOLOGY §1).** This prose specifies the function a *future, ratified*
> milestone will implement; the function — not this prose — becomes the source of
> truth once it ships (METHODOLOGY §2). No ρ is computed against this gate until
> the PRD is ratified and the function exists.

Given a per-feature ranking dict — `conformal_rank` — and the validated
`ablation_rank` from `per_fold_ablation_attribution`, plus the conformal
coverage record and the SHAP-vs-ablation contrast, `b2_conformal_gate(...)`
returns `gate_passed: bool` computed as the conjunction of:

1. **Agreement materiality (G1)** — `spearman_rho(conformal_rank, ablation_rank)
   >= rho_threshold` (proposed default `0.50`).
2. **Agreement significance (G1)** — the permutation test of that Spearman ρ
   against the ρ = 0 null has `p < alpha` (proposed defaults `alpha = 0.05`,
   `n_permutations >= 10_000`).
3. **Conformal validity (G2)** — `abs(empirical_coverage − (1 − alpha_conf))
   <= coverage_tol` (proposed defaults `alpha_conf = 0.10`,
   `coverage_tol = 0.05`). Blocks merge: an invalid conformal predictor makes the
   relevance ranking meaningless.
4. **Reference reproducibility (G3)** — when the systematized ablation reference
   is run against the published nb08 / B2-M2 lifts over the 7 M3 candidates,
   `spearman_rho(ablation_reference, reference_lifts) >= reproduction_threshold`
   (proposed default `0.90`). (Asserted in the sanity test that gates the merge;
   reported alongside the verdict.)

`rho_threshold`, `alpha`, `n_permutations`, `alpha_conf`, `coverage_tol`,
`reproduction_threshold`, and the feature set are all function arguments with the
proposed defaults above — **once ratified**, changing any of them after a result
is visible invalidates the run and requires a new PRD + ledger entry
(METHODOLOGY §1). G4 (the SHAP-vs-ablation contrast) is reported by the function
for context but is **not** part of the pass/fail conjunction.

## Open Questions

- [ ] **Which time-series-aware conformal variant?** Vanilla split conformal
      assumes exchangeability, which these regime-switching, autocorrelated return
      series violate. Candidate variants: block/clustered conformal on the purged
      folds, weighted conformal (covariate-shift weights), or ensemble-batch
      prediction-interval methods (EnbPI; adaptive conformal inference). **Pinned
      in B2-CONF-M1 before any code**, with the exchangeability mitigation stated
      explicitly so the implementation cannot silently fall back to i.i.d. split
      conformal. Flag rather than default.
- [ ] **Relevance statistic: set-width change vs nonconformity-quantile change.**
      Conformal relevance can be measured as the change in calibrated prediction-set
      width, or the change in the nonconformity-score quantile, when a feature is
      withheld. **Pinned to one in B2-CONF-M1**, with the other reported as a
      secondary diagnostic, so G1 compares a single ranking like-for-like.
- [ ] **"Withheld" = permute or ablate the column?** To stay *cheap* (the whole
      point — replacing `O(n_features)` backtests), the conformal signal must reuse
      the per-fold fitted models, so the feature is **permuted/zeroed in the
      held-out set, not ablated by re-fit**. This inherits the permutation caveat
      (broken feature correlations); whether the *conformal* lens nonetheless
      recovers agreement where raw permutation did not (ρ = −0.004) is precisely the
      hypothesis under test. Confirmed in B2-CONF-M1, frozen before M2.
- [ ] **Does the B2-M2 fair-comparison harness already expose what conformal
      needs?** Conformal relevance, like OOS permutation, needs per-fold
      `(fold_model, X_test)` plus a calibration split. B2-M2 built a private
      lightweight walk-forward retaining `(fold_model, X_test)` (the
      `docs/concepts/oos-attribution.md` §"OOS-permutation algorithm" design).
      Whether a *calibration* slice can be carved from the existing fold structure
      **without** introducing a new split path is resolved in B2-CONF-M1 — reuse
      the purge/embargo generator wholesale; no re-implementation of split logic
      (`backtest/CLAUDE.md` invariants).
- [ ] **Doc location.** Extend `docs/concepts/oos-attribution.md` in place vs. a
      new `docs/concepts/conformal-relevance.md`. Decided in B2-CONF-M1; a new
      top-level doc convention would be surfaced for approval (AGENT_OPERATION
      "New file/directory conventions").

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Conformal feature relevance also fails G1 (ρ < 0.50) | **Medium-High** | Medium | This is a *valid, pre-committed, terminal* outcome (METHODOLOGY §5): per-fold ablation remains the sole canonical OOS signal (§14 unchanged), the three-method shortlist is exhausted, and any further cheap-proxy search needs a new PRD. The negative is documented, not hidden, and B2-CONF-M3 is skipped per the binding skip path. Two consecutive cheap-proxy failures is itself a strong methodological finding about this problem. |
| The exchangeability violation makes conformal coverage invalid (G2 fails) | **High** if vanilla split conformal is used | High | B2-CONF-M1 **pins a time-series-aware variant before any code**; G2 (coverage within ±0.05) blocks merge precisely so an invalid predictor cannot reach G1. If no variant achieves valid coverage on this data, that is the verdict — conformal is not applicable here. |
| Pre-committing a gate for an unrun method binds prematurely | Medium | **High** | **This PRD is DRAFT**: the banner at the top and the Success-Metrics/gate section both state the thresholds are *proposed, pending human ratification* (METHODOLOGY §1). No compute runs until a human ratifies. The bar is held identical to the parent B2 gate specifically to prevent threshold-shopping after the permutation failure. |
| Building a private conformal walk-forward re-introduces a leakage bug | Low | **Very High** | Reuse the B2-M2 fair-comparison fold structure and the *exact* split generator + purge/embargo from `walkforward.py` / `harness.py` — no new split path (`backtest/CLAUDE.md` invariants). Harness self-tests (random → ≈ 0 edge, leaky → caught) must stay green. The calibration slice is carved from the existing fold, not a new split. |
| Conformal relevance is noisy (single permutation/calibration draw) | Medium | Medium | Average over `n_repeats` permutations / calibration resamples per feature (pinned in B2-CONF-M1); report per-feature standard error. The *ranking* (what G1 scores) is more stable than point estimates. |
| Lowering ρ ≥ 0.50 to "rescue" conformal after permutation failed | Low | **High** | Forbidden by METHODOLOGY §1 and called out explicitly: the G1 bar is **held fixed** across both attempts; a failed gate cannot be revived by re-tuning the threshold. Any change requires a new PRD + ledger entry. |
| Catalog enum extension breaks the existing drift test | Low | Low | The new `attribution_status` value ships only on G1 pass, with existing values preserved; the drift test in `tests/test_catalog.py` is *extended*, not rewritten, asserting both directions (METHODOLOGY §6). |

## Sequencing notes

- **Ratification gate (METHODOLOGY §1).** This PRD is DRAFT. Its thresholds bind
  **only after a human ratifies them**. B2-CONF-M1 does not start until ratification;
  no compute runs against an unratified gate.
- **B2-CONF-M1 ships the concept contract before B2-CONF-M2 writes code**
  (METHODOLOGY §4). The theory doc fixes the conformal variant, the relevance
  statistic, and the validation protocol; the implementation is the consumer.
- **B2-CONF-M2 ships `b2_conformal_gate` with G1–G4 pinned before any relevance is
  scored** (METHODOLOGY §2). No ρ is computed against an unwritten gate.
- **B2-CONF-M3 is conditional on G1 pass** (METHODOLOGY §5, binding). On G1 fail it
  is skipped and the verdict is documented; it cannot be revived by alternative
  justification without a new PRD.
- **Reuses, does not rebuild, the B2-M2 reference.** `per_fold_ablation_attribution`
  is the validated ground truth (B2 G2, ρ ≥ 0.90 vs nb08); this PRD consumes it.
- **Does NOT depend on `A-DSR-GATE`** (no Sharpe claim → no deflation). It depends
  only on B2-M2 (done), as encoded in `PRIORITIES.yaml` for the parent task.
- **Ledger discipline.** Each B2-CONF-M2/M3 run that produces a verdict appends a
  ledger entry via `quant.ledger`, with `n_comparisons = 1` (the single cheap
  method under test) and `verdict` from `b2_conformal_gate`. This is largely an
  infrastructure attempt, so its contribution to the cross-PRD deflation `N` is
  intentionally small.
- **Phase-5 independence.** Trigger 2 is **already satisfied** by B2-M3 (the
  `attribution.py` API + catalog field shipped); this PRD does not re-gate Phase 5.
  Its value (a validated *cheap* attribution signal) is an enhancement to honest
  feature triage, not a Phase-5 dependency.
- **Conditional follow-on (METHODOLOGY §5, binding):**
  - If **G1 passes** → a validated cheap OOS attribution method finally exists in
    code + catalog; ablation stays canonical but conformal becomes the cheap triage
    signal; no further cheap-proxy work is initiated unless separately motivated.
  - If **G1 fails** → per-fold ablation remains the **sole** canonical signal
    (METHODOLOGY §14 unchanged); the three-method shortlist
    (IS-importance → permutation → conformal) is **exhausted**. A failed G1 cannot
    be revived by retuning `rho_threshold`; any further attempt requires a new
    method shortlist under a new PRD + ledger entry.
- **Project-B closeout.** This PRD's task (`B2-CONFORMAL-PRD`) is **intentionally
  NOT in `B-CLOSE.depends_on`** — like `B3-PRD`/`B4-PRD`, it is a *conditional*
  research follow-on that may terminate `skipped` (if conformal is judged not worth
  the implementation risk after M1, or if G1 fails). Wiring it into closeout would
  let an unrun optional confirmation deadlock the Project-B verdict
  (`docs/PRIORITIES.yaml` `B2-CONFORMAL-PRD.notes`; AGENT_OPERATION "Project
  closeout" corollary). The downstream B2-CONF-M1/M2/M3 tasks, *if* created on
  ratification, follow the standard Step 7 corollary at that time.

---
*Status: DRAFT (2026-06-29) — conditional follow-on to Project B2, triggered by
the B2 G1 failure (ρ = −0.004, p = 0.505; ledger `ledger-2026-06-27-0005`). The
proposed thresholds in "Success Metrics" and "Pre-committed gate" (ρ ≥ 0.50,
p < 0.05, coverage ±0.05, ρ ≥ 0.90) **need human ratification before they bind or
any compute runs** (METHODOLOGY §1). Per-fold ablation remains the sole canonical
OOS signal in the interim (METHODOLOGY §14 unchanged). Next on ratification:
`/plan` turns B2-CONF-M1 into an implementation plan.*
