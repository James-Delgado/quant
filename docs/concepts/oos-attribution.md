# Out-of-Sample Feature Attribution

> **Living reference.** Companion to `docs/concepts/regime-evaluation.md`,
> `docs/concepts/label-schemes.md`, and `docs/concepts/evaluation-standards.md`.
> This document is the **concept contract** for Project B2 (METHODOLOGY §4 —
> build contracts before consumers). It states the problem B2 exists to solve,
> the three shortlisted attribution methods, the pre-committed decision to
> implement two and defer the third, the exact OOS-permutation algorithm, and
> the validation protocol that `B2-M2` executes. **B2-M1 (this doc) writes no
> code**; it fixes the design that `src/quant/backtest/attribution.py` implements
> at B2-M2. Do **not** retune a threshold here to make a method pass its gate.

---

## Why OOS-only attribution

The single largest **methodological** finding of Phase 4A — distinct from its
no-edge **research** verdict — is that **in-sample feature importance does not
transfer out-of-sample on this problem**. M3 measured the rank correlation
between SHAP importance (computed in-sample) and per-fold ablation lift
(measured out-of-sample) across the 7 M3 candidate features and found

> **Spearman ρ = −0.074**

(`docs/PHASE_4A_RETROSPECTIVE.md` §"The biggest methodological finding";
`docs/PHASE_4A_REPORT.md`; `notebooks/08_phase4a_feature_ablation.ipynb`). The
M5 forensics confirmed the asymmetry survives the corrected FRED join — macro
features still dominate IS, the OOS gap is unchanged (DM p = 0.72). In plain
terms: the cheapest, most common signal for deciding *"which feature is worth
keeping"* — SHAP, tree `feature_importances_`, permutation importance computed
on the **train** set — is, on this data and model class, **anti-correlated to
slightly-worse-than-random** as a predictor of OOS contribution.

This is binding, not a curiosity. **METHODOLOGY §14 (OOS-only attribution)**
pins the rule:

> In-sample feature importance (SHAP, `feature_importances_`, permutation
> importance on the train set) is informational only. Decisions about which
> features to keep, drop, or propose are driven by OOS attribution (per-fold
> ablation, OOS permutation importance) — *the* canonical finding of M3 is that
> IS importance does not transfer (Spearman ρ = −0.074).

But §14 names only **one** trustworthy signal today — per-feature ablation —
which costs `O(n_features)` full walk-forward backtests, and offers no way to
record per-feature attribution evidence in the catalog. B2 closes both gaps. It
does **not** ask *"is there edge?"* (that is B1); it asks **"can we attribute OOS
performance to individual features cheaply and trustworthily?"** It is a
methodology-substrate sub-project — the B-project analog of `A-LEDGER`.

The downstream consumer that makes this blocking is the **Phase-5
continuous-research harness**. ROADMAP §4 Project D **Trigger 2** is, verbatim,
*"B2's OOS attribution method is in code with B2-M3 catalog integration
shipped."* The retrospective is explicit: *"If [Agent F] reaches for SHAP for
triage, the loop is corrupted at the source."* Until B2 ships, Phase 5 cannot
start.

---

## The three candidate methods

Each method produces a **per-feature ranking** of out-of-sample contribution.
The reference signal against which the others are judged is per-fold ablation,
because it measures the exact thing we care about — marginal OOS contribution —
by construction.

### 1. OOS permutation importance (test-fold permutation)

**What it measures.** For each already-fit per-fold model and each feature *f*,
permute *f*'s column in the **test** matrix `X_test` (destroying its
relationship with the target while preserving its marginal distribution),
re-`predict`, and record the degradation in the OOS metric. The per-feature
importance is the degradation averaged across folds; the ranking is what the
gate scores.

**Strengths.** It reuses the per-fold *already-fit* models — **no re-fit**. Cost
is `O(n_features)` predict-passes per fold, roughly two orders of magnitude
cheaper than ablation's `O(n_features)` full backtests. It is computed entirely
on the test slice, so it is a genuine OOS signal (unlike train-set permutation,
which is part of the broken IS family).

**Limits.** A single permutation is noisy; the importance is averaged over
`n_repeats` permutations per feature (pinned in B2-M1; report the per-feature
standard error). Permutation also breaks feature correlations, so a feature that
is redundant with another can be scored as unimportant even when the pair
matters — the same caveat that applies to all permutation importance. **The open
question is whether it agrees with ablation**; that agreement is exactly what B2
measures.

### 2. Per-fold ablation as the canonical OOS signal (M3-style, systematized)

**What it measures.** Leave-one-out / add-one feature ablation measured **per OOS
fold**: re-run the walk-forward backtest with feature *f* removed (or added) and
measure the change in the OOS metric. The per-feature *lift* is the canonical
OOS attribution signal — it produced the M3 numbers and is trusted by
construction.

**Relation to existing code.** This systematizes the machinery already in
`src/quant/backtest/ablation.py`:
`run_feature_ablation(feature_sets, model, ...)` runs one
`run_portfolio_backtest` per feature set with all other kwargs held verbatim;
`make_add_one_sets(baseline, candidates)` and `make_leave_one_out_sets(cols)`
build the `{set_name: [columns]}` maps it consumes. B2-M2 wraps these in a thin
`per_fold_ablation_attribution(...)` that returns a per-feature OOS-lift ranking
— **no new split logic** (the `backtest/CLAUDE.md` leakage invariants stay
untouched).

**Strengths.** It is the ground truth — it measures marginal OOS contribution
directly, with the harness's purge/embargo controls intact.

**Limits.** **Cost.** One full walk-forward backtest per ablated feature. This is
precisely why a validated *cheap* proxy (method 1) is worth building.

### 3. Conformal feature relevance (deferred)

**What it measures.** A conformal-prediction-based notion of relevance: roughly,
how much a feature changes the calibrated predictive set / nonconformity scores
on held-out data, yielding a distribution-free relevance signal with coverage
guarantees.

**Strengths.** Theory-attractive — distribution-free, with finite-sample
validity under exchangeability, and a different statistical lens than
permutation or ablation (a genuine third cross-check).

**Limits.** It is the **least established in quantitative finance** and the
**highest implementation risk** of the three; the exchangeability assumption is
awkward under the temporal dependence and regime structure these series have.
Two methods (1 + 2) already give the cross-check B2 needs, so conformal relevance
is shortlisted but **not** in the B2 MVP.

| Method | Cost | OOS? | Role in B2 |
|---|---|---|---|
| OOS permutation importance | `O(n_features)` predicts (cheap) | Yes (test slice) | **Implemented** — the cheap proxy under test |
| Per-fold ablation | `O(n_features)` backtests (expensive) | Yes (per fold) | **Implemented** — the canonical reference signal |
| Conformal feature relevance | implementation-heavy | Yes | **Deferred** — conditional follow-on only |

---

## The pick (pre-commitment)

**B2 implements 2 of the 3 methods**: per-fold ablation as the canonical signal,
plus OOS permutation importance validated against it. **Conformal feature
relevance is deferred** as a conditional follow-on — drafted only if the
validation gate fails, or as a separate enhancement PRD.

This is a **pre-commitment** in the sense of METHODOLOGY §1 (pin thresholds and
design before any compute touches the experiment) and §5 (conditional
sub-milestones with explicit, binding skip criteria). It is ratified in the B2
PRD (`.claude/prds/b2-oos-attribution.prd.md`, "Scope" and "Sequencing notes")
and is synced into METHODOLOGY's open-questions section by task
`A-METH-OOSATTR-SYNC` (which marks the "OOS attribution method" open question
resolved-in-PRD without touching any numbered rule §1–§21).

The conditional path (METHODOLOGY §5, binding):

- **If the validation gate passes** → a validated cheap OOS attribution method is
  in code + catalog; Phase-5 Trigger 2 is met; no conformal-relevance work is
  initiated unless separately motivated.
- **If the validation gate fails** → per-fold ablation remains the **sole**
  canonical OOS signal (METHODOLOGY §14 unchanged), and conformal feature
  relevance becomes the next attempt at a cheap proxy under a new PRD. A failed
  gate **cannot** be revived by retuning the threshold after the fact.

---

## The OOS-permutation algorithm (frozen for B2-M2)

The algorithm B2-M2 implements, fixed here so the implementation is the consumer
of a written contract (METHODOLOGY §4):

1. Run a walk-forward that **retains `(fold_model, X_test)` per fold**, reusing
   the *exact* split generator + purge/embargo from
   `src/quant/backtest/walkforward.py` / `harness.py` — no re-implementation of
   split logic. (This is the "fair-comparison" design: ablation and permutation
   score the identical fold structure. Surfaced as an Open Question in the PRD —
   `BacktestResult` does not currently expose per-fold models/test matrices, so
   `attribution.py` runs its own lightweight walk-forward that retains the
   artifacts rather than touching the load-bearing harness.)
2. For each fold, compute the **baseline OOS metric** from the fold model's
   predictions on `X_test`.
3. For each feature *f* and each of `n_repeats` permutations: shuffle column *f*
   of `X_test` only, re-`predict` with the **already-fit** fold model, and record
   the degraded OOS metric. The feature's per-fold importance is
   `baseline − mean(permuted)`.
4. Average importances across folds; rank features. Report per-feature standard
   error across repeats.

**Degradation metric (pinned).** The scalar OOS metric permuted against is the
**same metric the ablation lift uses** — OOS Sharpe of the simulated
`sign(pred)` strategy (the Phase-4A convention) — so the agreement gate compares
like with like. Forecast-error MSE is reported as a secondary diagnostic only.

### Point-in-time / leakage note (hard invariant)

Permutation touches **only `X_test`** and reuses the fold model fit on the
training window; no test-set information ever flows into a fit. The private
walk-forward in `attribution.py` adds **no new split path** — it reuses the
purge/embargo generator wholesale, so the six `backtest/CLAUDE.md` invariants and
the harness self-tests (random → ≈ 0 edge, leaky → caught) stay green. Building a
private walk-forward that silently re-introduces a leakage bug is the
highest-impact risk in B2; option (a) above exists specifically to avoid it.

---

## Validation protocol (the sanity test)

The deliverable is a **method and its validation**, not a strategy edge. The gate
is therefore an *agreement* gate — does the cheap method reproduce the trusted
one? — not a Sharpe gate. All thresholds are pinned here before any compute
(METHODOLOGY §1) and are reproduced verbatim in `b2_attribution_gate(...)` at
B2-M2 (METHODOLOGY §2). Significance for the rank correlation is a **permutation
test** (≥ 10,000 random relabelings of one ranking) of the Spearman ρ against the
ρ = 0 null.

| # | Claim | Measured on | Statistic | Materiality (pinned) | Significance |
|---|---|---|---|---|---|
| **G1** | OOS permutation importance agrees with per-fold ablation | M6 25-column feature set, 5×8 slice (matches nb08) | Spearman ρ between the two rankings | **ρ ≥ 0.50** | permutation-test **p < 0.05** |
| **G2** | The systematized ablation reproduces M3 (port-correctness) | the 7 nb08 candidate features | Spearman ρ vs nb08's published lifts | **ρ ≥ 0.90** | — (reproducibility) |
| **G3** | The IS contrast still holds (sanity floor) | same 7 features | Spearman ρ between SHAP (IS) and ablation (OOS) | reported, expected **≤ 0.1** | — |

- **G1 is the gate.** ρ ≥ 0.50 is the agreement bar — the midpoint between "no
  relationship" (0) and "strong agreement" (≈ 0.7+), chosen so a *directionally
  useful but noisy* method passes while a method no better than IS importance
  (ρ ≈ 0) fails. It is the **explicit inverse of the broken IS signal**:
  ρ = −0.074 → ρ ≥ 0.50.
- **G2 guards the port.** Because per-fold ablation is the *reference* signal,
  B2's re-implementation must reproduce M3's numbers before it can be trusted as
  ground truth for G1. A high ρ here is a software-correctness check, **not** a
  research finding. It blocks merge.
- **G3 is the sanity floor.** Re-deriving the SHAP-vs-ablation ρ on the same 7
  features confirms the problem B2 exists to solve is still present in the
  harness as used (reproduces ρ = −0.074). If G3 came back high, the premise
  would be wrong and B2 should stop. G3 is **reported** by the gate function for
  context but is not part of the pass/fail conjunction.

Materiality precedes significance throughout (METHODOLOGY §10): G1's ρ ≥ 0.50 bar
is checked first; only then is the permutation-test p-value consulted. The
validation is pinned to the **5-symbol × 8-year slice** to match the nb08 M3
reference exactly — a like-for-like reproduction is the whole point of G2. A
full-panel agreement check is a *desirable* confirmation flagged as a follow-up,
not an MVP gate (B2 validates a method, not an edge). **Deflation does not
apply** — B2 makes no Sharpe/return claim, so DSR is undefined and B2 does not
depend on `A-DSR-GATE`; the ledger still records B2's runs with `n_comparisons =
1` (the single validated method) for the audit trail.

---

## Catalog integration plan

B2-M3 adds an `attribution_status` field to `FeatureRecord`
(`src/quant/features/catalog.py`) and to every entry in
`src/quant/features/catalog.yaml`, with enum
`{none, ablation_only, oos_permutation, both, agreed}` and default `none`:

| Value | Meaning |
|---|---|
| `none` | no OOS attribution computed for this feature (default; existing entries stay valid) |
| `ablation_only` | per-fold ablation lift computed; permutation not run |
| `oos_permutation` | OOS permutation importance computed; ablation not run |
| `both` | both signals computed but they **disagree** (rank/sign mismatch) |
| `agreed` | both computed and **consistent** |

The exact per-feature agreement rule that separates `both` from `agreed` (e.g.
same sign of lift AND both in the top/bottom tercile) is pinned in B2-M3 before
the field is populated, under a new ledger entry if it changes after results are
seen. Because the default is `none`, existing catalog entries need no edit to
remain valid; the drift test in `tests/test_catalog.py` is **extended, not
rewritten**, and asserts the new field's contract in both directions
(`set(produced) == set(catalog)`, naming offenders either way — METHODOLOGY §6).

**This milestone is Phase-5 Trigger 2.** The `attribution.py` API and the catalog
`attribution_status` field are the contracts the Phase-5 continuous-research
agent reads and calls (ROADMAP §4 Project D); shipping them in code with catalog
integration is what unblocks D-GATE's second trigger.

---

## Where it lands in code (forward pointers)

`B2-M1` (this doc) is the concept contract and writes **no code**. The
implementation follows:

| Milestone | Lands in | What |
|---|---|---|
| **B2-M2** | `src/quant/backtest/attribution.py` (+ tests, METHODOLOGY §15) | `per_fold_ablation_attribution(...)`, `oos_permutation_importance(...)`, `b2_attribution_gate(...)` implementing G1–G3 verbatim with pinned defaults; cross-module E2E notebook (METHODOLOGY §17) |
| **B2-M3** | `src/quant/features/catalog.{py,yaml}` (+ extended `tests/test_catalog.py`) | `attribution_status` field, drift-tested both ways, populated for attributed features — **Phase-5 Trigger 2** |

The gate function is the source of truth (METHODOLOGY §2); `rho_threshold`
(0.50), `alpha` (0.05), `n_permutations` (≥ 10,000), and `reproduction_threshold`
(0.90) are pinned defaults — changing any after a result is visible invalidates
the run and requires a new ledger entry (METHODOLOGY §1).

---

## Update protocol

The thresholds and design in this document are intended to be stable; they are
pinned before B2 compute runs. To change them:

1. Open a PR that explains the new value or method, citing the source that
   supersedes it.
2. Re-run the B2 validation on the same 5×8 slice and include the before/after
   G1/G2/G3 numbers.
3. Do **not** revise a threshold to make a method pass — that is post-hoc tuning
   of the evaluation harness, which destroys the value of pre-commitment. The
   same discipline applies to the T1–T6 gates, the VIX thresholds, and the
   `LDP_DEFAULT` label parameters.

---

## References

- López de Prado, M. (2018). *Advances in Financial Machine Learning.* Wiley.
  (Chapter 8: Feature Importance — MDI/MDA, and the case for out-of-sample
  importance over in-sample.)
- Bailey, D.H., & López de Prado, M. (2014). The Deflated Sharpe Ratio.
  *Journal of Portfolio Management.* (Why multiple-trial deflation matters —
  here, why B2's non-Sharpe agreement gate is exempt.)
- Diebold, F.X., & Mariano, R.S. (1995). Comparing Predictive Accuracy.
  *Journal of Business & Economic Statistics*, 13(3), 253–263.
- Phase 4A evidence: `docs/PHASE_4A_RETROSPECTIVE.md` §"The biggest
  methodological finding"; `docs/PHASE_4A_REPORT.md`;
  `notebooks/08_phase4a_feature_ablation.ipynb`.

---

*Sister documents:
[regime-evaluation.md](regime-evaluation.md),
[label-schemes.md](label-schemes.md),
[evaluation-standards.md](evaluation-standards.md),
[purging-and-embargo.md](purging-and-embargo.md).
Primary reference: `.claude/prds/b2-oos-attribution.prd.md`.
Binding methodology: `docs/METHODOLOGY.md` §14 (OOS-only attribution), §1, §2,
§4, §5, §6, §10.*
</content>
</invoke>
