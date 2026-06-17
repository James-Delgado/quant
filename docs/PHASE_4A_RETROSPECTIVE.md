# Phase 4A — Methodology Retrospective

*Companion to [`PHASE_4A_REPORT.md`](PHASE_4A_REPORT.md) (the verdict).
This document is the **lessons-learned narrative** — the story of which
practices made Phase 4A's negative result trustworthy, and which corners
need tightening for the next PRD.*

> **The 10 practices that worked have been extracted to
> [`METHODOLOGY.md`](METHODOLOGY.md) as the canonical, binding contract
> for every future PRD and every agent run.** This document narrates
> *why* each rule exists; `METHODOLOGY.md` is the rule itself. Agents
> should read `METHODOLOGY.md` directly, not this file, to understand
> the contract.

---

## What worked — narrative summary

The 10 practices below were the load-bearing pieces of the Phase 4A
discipline. Each is now a numbered rule in `METHODOLOGY.md`; the
short headlines are kept here for narrative continuity, but the
authoritative text and reference examples live there.

1. **Pre-committed thresholds before any compute** (`METHODOLOGY.md` §1)
2. **Exit gates quoted verbatim in code, not prose** (§2)
3. **Contamination audits when sequencing milestones** (§3) — the M5
   re-sequencing ahead of M3 was the project's single most valuable
   planning-time catch
4. **Conditional sub-milestones with explicit skip paths** (§5) — M2.5
   skipped cleanly
5. **Drift-test contracts in both directions** (§6) — `set(produced) ==
   set(catalog)` named the offender either way
6. **Per-arm checkpoints + checkpoint-only verdict notebook** (§7) — the
   90-min compute pass and the seconds-long verdict notebook were
   decoupled by design
7. **Invariant-parity audits recorded in code, not prose** (§8) — caught
   a real `run_label_ablation` bug before it entered the gate
8. **Honest declarations of protocol deviations** (§9) — the vol_scaled
   unit conversion that was skipped is openly declared with bounded
   impact analysis
9. **Borda-style cross-regime composite rankings** (§10)
10. **Materiality thresholds before significance thresholds** (§10) —
    M5's 5% sign-flip + 0.1 |ΔSharpe| bars had to trip before any
    DM p-value was consulted

---

## Near-misses and corner-cuts to tighten

1. **Milestone numbering was initially wrong.** M5 only got ahead of M3
   because of the planning audit, not the initial PRD. The PRD's
   "Sequencing notes" was retrofitted. **Future PRDs should include an
   explicit "what could contaminate downstream milestones?" pass before
   committing to the order.**

2. **The catalog (M4) came after M3 ablation rather than before.** Logically
   the catalog is the contract; ablations should write against it. The
   correct ordering is M4 → M3, not M3 → M4. **Future similar pairs should
   build the contract before the consumer.**

3. **M3 ran on a 5-symbol × 8-year slice only; full-panel re-ablation
   never happened.** M6 tested label schemes at full panel, not features
   at full panel. The M3 survivors (`xs_rank_vol_21d`, `trend_regime`)
   carry forward with `tested_edge` based on slice-level evidence only.
   **Cost: medium. Mitigation in future PRDs: pair every slice-level
   ablation with a deferred full-panel confirmation milestone, even if
   compute-gated.**

4. **Trial-count tracking was per-milestone, not cumulative.** The trials
   registry exists prose-only in `PHASE_4A_REPORT.md` §7 and is hand-rolled.
   Phase 5 specs a machine-writable ledger; we should consider standing
   it up earlier so the Bailey-López de Prado N is accumulated across PRDs,
   not estimated post-hoc. **Build the ledger before the next PRD; populate
   retroactively from the Phase 4A milestones.**

5. **Deflated Sharpe is discussed but not enforced.** PHASE_4A_REPORT §7
   computes a back-of-envelope DSR but the gate function does not
   incorporate it. **Future gates should include DSR as a second-stage
   test (gate-pass requires both pre-committed Sharpe threshold AND DSR
   above zero), and the threshold should be set against the cumulative
   ledger N.**

---

## The biggest methodological finding

**SHAP-vs-OOS-ablation Spearman ρ = −0.074 (M3, n=7 features).** In-sample
feature importance does not predict out-of-sample contribution on this
problem. The M5 forensics confirmed the asymmetry survives a corrected
FRED join (macro features still dominate IS; OOS gap unchanged). M3 showed
the asymmetry generalizes to non-macro features.

**Implications:**

- Any feature-engineering workflow that uses IS importance (SHAP,
  permutation importance on the train set, tree feature_importances_) as a
  guide for what to try is leaning on a signal that does not transfer.
- The Phase 5 Agent F design (in `PHASE_5_AGENTS.md`) currently assumes
  catalog metadata + glossary + ablation. If the agent reaches for SHAP
  for triage, the loop is corrupted at the source. **Agent F needs an
  OOS-only attribution method (per-fold permutation importance computed
  on the test slice, or per-feature ablation as the sole signal) before
  Phase 5 can begin.**
- This is the largest unresolved methodology question coming out of
  Phase 4A and should be in scope for the next research PRD even before
  the target-reframing question is asked.

---

## Open methodology questions for the next PRD

- **Per-feature attribution on a non-transferring IS signal.** Permutation
  importance computed OOS? K-fold ablation as the canonical signal? A
  hold-out-feature audit run on the full panel as part of every PRD's
  exit gate?
- **Trial-count ledger as a first-class artifact.** Where does it live
  (`data/ledger.yaml`?), who writes to it, how is it audited?
- **Slice-vs-full-panel discipline.** What's the explicit rule for when
  a slice-level verdict is allowed to stand vs. requires full-panel
  confirmation?
- **Materiality thresholds for the next problem.** Phase 4A's were derived
  from the Phase 3 ±0.1 Sharpe granularity. A target-reframing PRD or a
  drawdown-classification PRD needs different materiality units
  (classification metrics, expected-shortfall improvements).

---

## What this retrospective means for future PRDs

The 10 practices above + 4 upgrades are codified in
[`METHODOLOGY.md`](METHODOLOGY.md) as the binding contract for every
research PRD and every agent run. The four open methodology questions
become work items in `docs/PRIORITIES.yaml` — `A-LEDGER` (the trial-count
ledger) is rank-1, the OOS-attribution method is Project B2's full PRD.

The Phase 4A negative result was honest because the discipline was
tight. The discipline only stays tight if the contract is named,
enforced in CI, and read by every contributor (human or agent) before
they start. That's what the extraction to `METHODOLOGY.md` is for.
