# B1 — Target Reframing

> **Project**: B (Predictive research, post-4A) — sub-project B1.
> **Roadmap**: [`docs/PROJECT_ROADMAP.md`](../../docs/PROJECT_ROADMAP.md) §4 Project B,
> §7 "B1 — Target reframing", §8 ratified decision 2.
> **Methodology** (binding): [`docs/METHODOLOGY.md`](../../docs/METHODOLOGY.md).
> **Verdict context**: [`docs/PHASE_4A_REPORT.md`](../../docs/PHASE_4A_REPORT.md).
> **Backlog tasks**: `B1-M1`, `B1-M2`, `B1-M3` in
> [`docs/PRIORITIES.yaml`](../../docs/PRIORITIES.yaml).

## Problem

Phase 4A proved, with a pre-committed binary gate, that **next-bar (and
1-day signed) return is structurally unlearnable** from the current public
feature set on the Dow-30 + SPY/QQQ/IWM sandbox: across three label schemes
(signed, vol-scaled, triple-barrier), regime-aware features, a corrected
FRED publication-lag join, and regime-conditional evaluation, no GBM arm
beat ARIMA(1,0,0) in any required regime, with Diebold-Mariano p = 1.0000
everywhere (`docs/PHASE_4A_REPORT.md` §3). The report's own risk-table
direction was explicit: the next move is "a fundamentally different
label/target framing — *not* Track A (transformers)."

The unresolved question is **whether the failure is the universe, the
feature set, or the target**. B1 isolates the **target** variable. Phase 4A
held the target essentially fixed (all three schemes are transforms of
*return* — sign, vol-scaled magnitude, or a barrier-touch event on the
return path) and varied features and labels. It never asked whether a
*different prediction object* — drawdown risk, realized volatility, or a
longer directional horizon — is more learnable from the same information
set. Successful quant shops run a **portfolio of (target, model, universe,
regime) tuples** (ROADMAP §3.4); Phase 4A explored one target. B1 is the
target axis of that portfolio.

If B1 surfaces no edge on any of four targets, that is itself a high-value
negative: it argues the binding constraint is the *information set* (→ B3
alternative data) or the *universe* (→ B4), not the target framing — and it
does so without spending a Cboe/alt-data ingestor budget first.

## Evidence

From `docs/PHASE_4A_REPORT.md` (full 33-symbol × ~22-year panel, 87 folds,
corrected FRED joins):

| Arm | Aggregate OOS Sharpe | qe_bull | covid | rate_cycle | DM p (req. regimes) |
|---|---:|---:|---:|---:|---:|
| ARIMA(1,0,0) control | **+0.423** | +1.059 | +0.403 | +0.405 | — |
| GBM signed (primary) | −0.336 | −0.029 | −1.280 | −0.442 | 1.0000 |
| GBM vol_scaled | −0.339 | −0.183 | −1.564 | −0.607 | 1.0000 |
| GBM triple_barrier | +0.177 | −0.215 | −1.140 | +0.322 | (Sharpe-only) |

Structural findings that motivate target reframing:

- **The GBM learns a mean-reversion signal it cannot monetize on a trending
  universe** (`PHASE_4A_REPORT` §5). Return *direction* at short horizons is
  dominated by trend continuation that ARIMA's AR(1) prior already captures;
  the GBM's edge, if any, is not in the return-sign object.
- **IS importance does not transfer OOS** (M3 SHAP-vs-ablation Spearman
  ρ = −0.074). This is a property of *this model class on this data and
  target*, not a leakage artifact (M5 confirmed the IS dominance survives the
  lag fix, DM p = 0.72). A different target may have a different IS→OOS
  transfer profile — that is exactly what B1 measures.
- **Volatility and drawdown are more autocorrelated and more
  regime-structured than return sign.** Realized vol clusters (ARCH effects);
  drawdown risk is conditionally predictable from vol state. These are the
  textbook "more learnable than return direction" objects, and the harness
  already tags the regime axis they depend on (`vix_regime`, `vol_regime_ratio`).

## Users

- **Primary**: the researcher, working in `notebooks/10_*` and `11_*` and the
  existing harness/ablation/runner substrate.
- **Secondary (future)**: the Phase-5 continuous-research agent pair (Agent F
  expanded to *all* candidate-generation artifact types — including prediction
  targets, per ROADMAP §4 Project D). B1's `features/targets.py` and
  `b1_gate_report` become contracts those agents read and call.
- **Not for**: production traders, live capital. B1 is offline research only.
  (B1 *feeds* Project C's deployment loop a candidate target if one clears;
  it does not deploy.)

## Hypothesis

We believe that **at least one of four pre-committed prediction targets
other than next-bar return — 21-day drawdown classification, 21-day realized
volatility, 5-day directional, or 21-day cumulative direction — is
structurally more learnable on the Dow-30+ETF sandbox** than the Phase-4A
return target, for **the researcher (and the future continuous-agent pair)**.

We'll know we're right when **at least one target's GBM beats its
pre-committed baseline on that target's primary metric in ≥ 2 of the 3
required regimes (qe_bull, covid, rate_cycle), the per-regime materiality
threshold is met, the paired 90% bootstrap CI of the delta excludes 0 in
≥ 1 required regime, AND the result survives deflation (DSR > 0 at the
cumulative ledger N at completion).** All four conditions are required; this
is the same "materiality before significance, then deflation" discipline as
Phase 4A, generalized to non-Sharpe metrics.

If no target clears, the verdict is **"no extractable edge from this feature
set on any of the four targets"** → conditional trigger for B3 (alt data) and
B4 (universe shift) per the skip paths below.

## Success Metrics

Per-target primary metric, baseline, and pre-committed thresholds. **All
numeric thresholds are pinned here before any compute touches B1
(METHODOLOGY §1); they are reproduced verbatim in `b1_gate_report`
(METHODOLOGY §2) as the source of truth.** Significance is a paired
**stationary block bootstrap** (21-day blocks, the T1 convention) of the
per-regime metric delta vs the baseline, reusing
`backtest/statistics.bootstrap_sharpe_delta_ci` (generalized from Sharpe to
the target's metric).

| # | Target | Type | Primary OOS metric | Baseline | Materiality (per required regime) | Significance | Deflation |
|---|---|---|---|---|---|---|---|
| T1 | 21-day drawdown: `P(max drawdown > 5% over next 21 bars)` | binary classification | ROC-AUC | climatology base-rate predictor + ARIMA-vol-implied DD probability | ΔAUC ≥ **0.02** vs the better baseline | paired block-bootstrap 90% CI of ΔAUC excludes 0 in ≥ 1 required regime | DSR > 0 at ledger N |
| T2 | 21-day realized volatility (log-vol) | regression | MAE on log realized vol | EWMA(λ=0.94) vol forecast + ARIMA-on-log-vol | ΔMAE ≥ **5%** relative reduction vs the better baseline | paired block-bootstrap 90% CI of ΔMAE excludes 0 in ≥ 1 required regime | DSR-analog (skill z-score > 0) at ledger N |
| T3 | 5-day directional: `sign(ret_5d)` | binary classification | ROC-AUC **and** tradeable Sharpe of `sign(pred)` | majority-class + ARIMA(1,0,0) sign | ΔAUC ≥ **0.02** vs the better baseline **and** ΔSharpe ≥ **0.10** vs ARIMA | paired block-bootstrap 90% CI excludes 0 (on whichever metric is gated) in ≥ 1 required regime | DSR > 0 at ledger N (on the Sharpe arm) |
| T4 | 21-day cumulative direction: `sign(ret_21d)` | binary classification | ROC-AUC **and** tradeable Sharpe of `sign(pred)` | majority-class + ARIMA(1,0,0) 21-bar sign | ΔAUC ≥ **0.02** vs the better baseline **and** ΔSharpe ≥ **0.10** vs ARIMA | paired block-bootstrap 90% CI excludes 0 in ≥ 1 required regime | DSR > 0 at ledger N (on the Sharpe arm) |

Notes on the metric choices (resolving METHODOLOGY §"Open questions" →
"materiality thresholds for non-Sharpe targets"):

- **Classification targets (T1, T3, T4)** are gated on **ΔAUC**, not accuracy
  — accuracy is base-rate-sensitive and uninformative on imbalanced drawdown
  labels. ΔAUC ≥ 0.02 is the materiality cut pre-committed in METHODOLOGY's
  pre-registration schema example; B1 adopts it as the standing classification
  materiality bar.
- **Directional targets (T3, T4)** additionally carry a **tradeable Sharpe**
  arm via `sign(pred)` through the existing simulator, so they remain
  commensurable with the Phase-4A ARIMA gate and can feed Project C. A
  directional target must clear **both** ΔAUC and ΔSharpe to count — a model
  that ranks well (AUC) but cannot monetize (Sharpe) is not an edge for
  deployment.
- **The vol target (T2)** is *not* a return signal; its edge is "predicts
  realized vol better than EWMA/ARIMA-on-vol." It feeds C4 (confidence
  calibration) and C3 (vol-targeted sizing), not a direction. Its deflation
  uses a skill-z-score analog (forecast-skill standard error from the block
  bootstrap) since Sharpe is undefined for a vol forecast.
- **Composite ranking** across the four targets and across regimes uses the
  balanced **Borda** method (METHODOLOGY §10), never cherry-picking the
  winning regime.

## Scope

**MVP** — the three milestones below, executed in order, reusing the existing
substrate (harness, regime detector, ablation orchestrator, runner pattern,
ledger, catalog). No new data sources; same 25-column M6 feature set as the
*input*; only the **target/label** changes.

1. **B1-M1 — Candidate target catalog + gate function.** A
   `features/targets.py` module producing the four targets as point-in-time
   label series (drawdown-event, log-realized-vol, 5-day sign, 21-day sign),
   each with a horizon constant that the purge/embargo logic consumes (the
   horizon-coupling invariant in `backtest/CLAUDE.md`). **The pre-committed
   `b1_gate_report` gate function ships in this milestone, before any
   ablation runs** (METHODOLOGY §2, §4 — contract before consumer). It extends
   `phase4a_gate_report` to (a) accept a per-target metric callable
   (AUC / MAE / Sharpe), (b) apply the per-target materiality + significance
   thresholds pinned above, and (c) read the deflation N from
   `quant.ledger.cumulative_trial_count()` and require DSR > 0 (the A-DSR-GATE
   deliverable; B1-M2 `depends_on` A-DSR-GATE so the DSR second stage exists
   before the matrix runs). Tests land with the module (METHODOLOGY §15).
2. **B1-M2 — Per-target ablation matrix on the 5-symbol × 8-year slice.**
   `notebooks/10_b1_target_ablation.ipynb`: each of the four targets ×
   {ARIMA-or-baseline control, GBM, naive baseline} on the standard slice,
   verdict via `b1_gate_report`, Borda composite across regimes. Slice verdict
   is **provisional** (METHODOLOGY §11 slice+full-panel discipline); any target
   showing per-regime edge carries to M3 for confirmatory full-panel testing.
3. **B1-M3 — Full-panel confirmation of slice-winners (compute-gated).**
   `scripts/run_b1_arms.py` (mirrors `run_phase4a_arms.py`: per-arm parquet
   checkpoints, idempotent, `--smoke` mode, **writes a ledger entry per arm via
   `quant.ledger.append_ledger_entry`** — the A-LEDGER-RUNNERS integration, so
   the deflation N stays current automatically). `notebooks/11_b1_exit_gate.ipynb`
   is checkpoint-only (METHODOLOGY §7 — verdict from checkpoints, never re-fits).
   `docs/B1_REPORT.md` is the written verdict (the §20/§21 analog of
   `PHASE_4A_REPORT.md`).

**Out of scope**

- **New data sources / ingestors** — B1 uses the current OHLCV+FRED+SEC+sentiment
  feature set unchanged. A surfaced need for new data is a *finding* feeding
  B3, not a B1 deliverable.
- **Universe changes** — Dow-30+ETF sandbox is fixed for B1; universe shift is
  B4.
- **New model classes** — B1 reuses GBM + ARIMA baselines. Regime-conditional
  ensembling / state-space models (PHASE_4A_REPORT §6 candidate 3) are a
  separate PRD.
- **Meta-labeling, confidence calibration, sizing** — C3/C4 consume B1's vol
  target if it clears; B1 does not build them.
- **Transformers / Track A** — deferred until a B-cycle gate clears
  (ROADMAP §4 Project D triggers).
- **The continuous-agent harness (Phase 5)** — B1 leaves agent-consumable
  artifacts (`targets.py`, `b1_gate_report`, ledger entries) but builds no
  agents.

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->

| # | Milestone | Outcome | PRIORITIES task | Depends on |
|---|---|---|---|---|
| 1 | Candidate target catalog + gate function | Four targets are point-in-time label series in `features/targets.py`; `b1_gate_report` exists in code with all thresholds pinned, *before* any ablation runs | `B1-M1` | `B1-PRD` |
| 2 | Per-target ablation matrix (slice) | Researcher knows, on the 5×8 slice, which (if any) of the four targets shows per-regime edge over its baseline; provisional Borda composite | `B1-M2` | `B1-M1`, `A-DSR-GATE` |
| 3 | Full-panel confirmation (compute-gated) | Slice-winners re-evaluated on the full 33-symbol panel under the runner pattern; ledger auto-updated; `docs/B1_REPORT.md` records the binary verdict | `B1-M3` | `B1-M2`, `A-LEDGER-RUNNERS` |
| Gate | At least one target clears materiality + significance + deflation in ≥ 2 required regimes | Binary. **Pass** → that (target, model) tuple is a B-cycle artifact (Phase-5 Trigger 1) and a candidate for Project C deployment. **Fail** → "no extractable edge from this feature set on any of the four targets"; triggers B3/B4 conditional skip paths. | — | — |

## Pre-committed gate (verbatim — implemented in B1-M1 as `b1_gate_report`)

The gate function is the source of truth; this prose describes it
(METHODOLOGY §2). For a single (target, arm) result it returns
`gate_passed: bool` computed as the conjunction of:

1. **Materiality** — the target's primary-metric delta vs its pre-committed
   baseline meets the per-target threshold in the table above, in
   ≥ `min_pass` (= 2) of the required regimes `(qe_bull, covid, rate_cycle)`.
2. **Significance** — the paired stationary-block-bootstrap (21-day blocks)
   90% CI of that delta **excludes 0** in ≥ 1 of the required regimes.
3. **Deflation** — deflated Sharpe (Bailey & López de Prado 2014) > 0, or the
   vol-target skill-z analog > 0, with the deflation N read from
   `quant.ledger.cumulative_trial_count()` at evaluation time (the A-DSR-GATE
   deliverable). For directional targets this applies to the Sharpe arm.

`min_pass`, the required-regime tuple, the per-target metric thresholds, the
bootstrap block length, and the deflation source are all function arguments
with the defaults pinned above — changing any of them after a result is
visible invalidates the run and requires a new ledger entry (METHODOLOGY §1).

## Open Questions

- [ ] **Is 21-day drawdown imbalanced enough to need a calibrated threshold?**
      If `P(>5% DD)` base rate is very low on this universe, AUC is still valid
      but the operating point matters for any downstream use. B1-M1 reports the
      base rate per regime; the operating-point choice is deferred to whichever
      consumer (C3 risk stops) uses it.
- [ ] **Should the vol target predict log-vol or vol directly?** Pinned to
      **log realized vol** here (variance-stabilizing, standard in the
      literature); revisit only with a new ledger entry if M2 shows pathological
      residuals.
- [ ] **Does the 5-day vs 21-day directional horizon interact with the
      embargo?** Both horizons exceed the 1-day Phase-4A label; `backtest/CLAUDE.md`
      invariant 4 (test-fold length ≫ `label_horizon + embargo`) must be
      re-checked in B1-M1 — flag rather than silently shrink the training set.
- [ ] **Baseline for the drawdown target.** Pinned to the better of
      (climatology base-rate, ARIMA-vol-implied DD probability); if neither is a
      fair baseline, that is an M1 finding, resolved before M2 with a new ledger
      entry — not mid-matrix.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| All four targets fail; B1 is another clean negative | Medium | Medium | This is a *valid, pre-committed* outcome — it cleanly redirects to B3/B4 and saves alt-data spend. The skip paths below make the negative actionable, not a dead end. |
| Vol/drawdown targets "look learnable" only because the metric is easier, not because there is tradeable edge | Medium | High | T2's edge is explicitly *forecast skill vs EWMA/ARIMA-on-vol*, not a Sharpe claim; it is not promoted to a strategy without a downstream consumer (C3/C4). Directional targets must clear **both** AUC and Sharpe. |
| Horizon change breaks the purge/embargo coupling (silent leakage) | Low | Very High | B1-M1 re-derives the embargo from the new `label_horizon` and asserts `backtest/CLAUDE.md` invariants 3–4; harness self-tests (random→~0 edge, leaky→caught) must stay green. |
| Slice winner does not survive full panel (the M3 near-miss Phase 4A flagged) | Medium | Medium | METHODOLOGY §11 is built into the milestone structure: slice verdict is provisional; B1-M3 is the mandatory confirmatory full-panel run, no exceptions. |
| Multiple-testing inflation across 4 targets × 4 arms × 3 regimes | High | High | Every arm appends a ledger entry (A-LEDGER-RUNNERS); the DSR N grows with the matrix, so the deflation bar rises as B1 tests more — the gate auto-penalizes its own search width. |
| ΔAUC 0.02 / ΔMAE 5% thresholds are mis-calibrated | Medium | Medium | Pinned before compute; if M1 base-rate diagnostics show a threshold is degenerate, fix it *before* M2 under a new ledger entry (never after seeing a result). |

## Sequencing notes

- **B1-M1 ships the gate function before B1-M2 runs the matrix** (METHODOLOGY
  §2, §4). The thresholds in this PRD are the spec; the function is the
  contract; the matrix is the consumer. No result is computed against an
  unwritten gate.
- **B1-M2 `depends_on` A-DSR-GATE** (already encoded in `PRIORITIES.yaml`):
  the DSR second stage must exist in `regime_metrics.py` before the matrix is
  scored, so deflation is applied from the first ablation, not retrofitted.
- **B1-M3 `depends_on` A-LEDGER-RUNNERS**: the full-panel runner writes ledger
  entries automatically, keeping the deflation N current for B1 itself and for
  every downstream PRD.
- **Conditional skip paths (METHODOLOGY §5, binding):**
  - If **B1 gate passes** on any target → that tuple satisfies Phase-5
    Trigger 1; B3 and B4 stay `blocked`/low-priority unless separately
    motivated.
  - If **B1 gate fails on all four targets** → draft **B3** (options-implied
    alt data) per its conditional note in `PRIORITIES.yaml`; **B4** (universe
    shift) follows only if B1+B3 both surface no edge. These triggers are
    pre-committed; a failed B1 cannot be revived by alternative justification
    without a new PRD.
- The gate is calibrated against **ARIMA / climatology / EWMA baselines**, not
  buy-and-hold — consistent with Phase 4A. Beating buy-and-hold on a bull
  universe is a separate, harder problem than demonstrating edge over the
  simplest predictive baseline for each target object.

---
*Status: DRAFT (2026-06-23) — pre-commitment for Project B1. Thresholds in
"Success Metrics" and "Pre-committed gate" are frozen on ratification;
changes require a PRD revision and a new ledger entry, not an in-flight
override. Next: `/plan` turns B1-M1 into an implementation plan.*
