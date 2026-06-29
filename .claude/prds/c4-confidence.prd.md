# C4 — Confidence Calibration

> **STATUS: DRAFT — pre-committed design/thresholds need human ratification before implementation (METHODOLOGY §1).**
>
> **Project**: C (Live execution & deployment infrastructure) — sub-project C4.
> **Roadmap**: [`docs/PROJECT_ROADMAP.md`](../../docs/PROJECT_ROADMAP.md) §4 Project C
> (C4 row), §7 "C4 — Confidence calibration" (M1 method shortlist, M2 models emit
> calibrated intervals, M3 per-regime calibration audit) **and the §7 C4↔E4 boundary
> note** (live calibration-drift is C4's scope; E4 owns feature/data-distribution drift).
> **Methodology** (binding): [`docs/METHODOLOGY.md`](../../docs/METHODOLOGY.md) — esp.
> §1 (pre-committed thresholds), §2 (gates-in-code), §4 (**contract before consumer** —
> C4 populates the *existing* C6 registry `confidence_gate` field, it does **not** define a
> parallel confidence schema), §6 (drift contracts, both directions), §9 (honest deviation
> declaration / no silent fallback), §10 (materiality before significance), §14 (OOS-only —
> calibration is judged on the **test** fold, never the train fold), §15/§17 (tests + the
> milestone notebooks as the E2E surface), §20 (post-task review).
> **Parent contract**: [`c6-strategy-registry.prd.md`](c6-strategy-registry.prd.md) —
> C4 is the consumer that *fills* the registry's placeholder `confidence_gate` field
> (`src/quant/execution/strategy_registry.py`: `ConfidenceGate`, pinned `always_pass` /
> inert) and supplies the calibrated **confidence scalar** that C3's sizing seam multiplies
> (the "confidence enters once, at sizing" rule). C6-M1 shipped `confidence_gate` as a
> deliberate placeholder so C4 is built **into** the contract, not retrofitted.
> **Sibling contract**: [`c3-sizing-risk.prd.md`](c3-sizing-risk.prd.md) — C3 owns the
> sizing seam (`sized_weight = vol_target_weight × confidence_scalar`, scalar default `1.0`,
> inert until C4); **C4 supplies the signal, C3 owns where it multiplies**. C4 invents no
> new sizing path.
> **Existing substrate it composes**: the Phase-1 walk-forward harness + the
> `BacktestResult` container (`backtest/harness.py` — `oos_returns` / `oos_forecast_errors`,
> the per-bar OOS series C4 extends with calibrated intervals), the bootstrap/quantile
> machinery (`backtest/statistics.py`), the regime axis (`backtest/regimes.py` /
> `regime_metrics.py` — the per-regime slicing C4-M3's coverage audit reuses), and the
> deployable models (`models/arima_baseline.py`, `models/gbm.py`).
> **Backlog tasks**: `C4-PRD` (this) in
> [`docs/PRIORITIES.yaml`](../../docs/PRIORITIES.yaml); milestone tasks `C4-M1`,
> `C4-M2`, `C4-M3` are created when C4 execution is scheduled (see "Sequencing notes");
> consumers `C3-M3` (the sizing seam C4 feeds) and the Project E console (`E3`/`E4`, which
> *surface* C4's calibration-drift signal).

## Problem

C6 stood up the deployment spine — a strategy registry whose every entry carries a
`confidence_gate` field, and a daily executor that sizes each enabled strategy. But C6 (and
C3) ship that spine with **no notion of how confident a prediction is**. A model emits a
point forecast (ARIMA's one-step mean; GBM's class score); the executor turns the *sign* of
that forecast into a position and sizes it — **a 0.001 forecast and a 0.05 forecast of the
same sign produce the same signed unit position**. Four things therefore do not exist
between "the model emits a point forecast" and "the system sizes by how trustworthy that
forecast is":

1. **No calibrated confidence.** Models emit a point prediction with **no interval and no
   calibrated probability**. `ConfidenceGate.method` is a single-value
   `Literal["always_pass"]` (`execution/strategy_registry.py`) — the gate is **inert**;
   every prediction passes regardless of how uncertain it is. There is no conformal,
   quantile-regression, or bootstrap-CI machinery producing an interval whose stated
   coverage is *honest* (a 90% interval that actually contains the realization 90% of the
   time out-of-sample).
2. **No confidence signal for sizing.** C3 built the sizing *seam*
   (`sized_weight = vol_target_weight × confidence_scalar`) with the scalar pinned to
   `1.0` — **inert until C4 supplies it**. There is nowhere a high-confidence prediction
   becomes a larger position and a low-confidence one a smaller (or zero) position. The
   `confidence_gate` field exists and is inert; the *signal* side of the seam is unbuilt.
3. **No OOS calibration evidence.** Nothing checks whether a model's stated uncertainty is
   *true* on held-out data. A model can be confidently wrong; without a per-regime coverage
   audit (do 90% intervals contain 90% of OOS realizations, in *each* regime?) the system
   would size on a confidence number it has no right to trust — and calibration is exactly
   the kind of property that breaks in the high-vol regime where it matters most.
4. **No live calibration-drift detector.** Even a model calibrated in backtest can decay
   live: a 90% interval that covers 90% in backtest may cover only 70% on this quarter's
   live data — the prediction's stated confidence has gone stale. Nothing computes that
   live-vs-stated coverage gap. (This is **distinct** from feature/data-distribution drift,
   which E4 owns — see "The C4 ↔ E4 calibration-drift boundary" below.)

C4 is the consumer that **fills the C6 registry's placeholder `confidence_gate` field with
real calibration machinery** and supplies the **confidence scalar** C3's sizing seam
consumes. It does **not** define a new schema, a new registry, or a new sizing path — it
extends the *existing* `ConfidenceGate` sub-model and the *existing* `BacktestResult`
container (contract-before-consumer, METHODOLOGY §4: C6-M1 built the contract first
precisely so C4 plugs into it; C3 built the sizing seam first precisely so C4 fills it).
Like C1/C2/C3/C6, C4 makes **no edge claim** — it quantifies and calibrates the uncertainty
of an *existing* forecast; it does not seek alpha and does not change a prediction's sign.
Its gate is a **calibration-coverage + back-compat + drift** gate, **not** a Sharpe gate.

## The C4 ↔ E4 calibration-drift boundary (stated explicitly, METHODOLOGY §4)

This boundary was reconciled during Project E task generation (PRIORITIES C4-PRD notes;
ROADMAP §7 C4 sketch boundary note + the C5-superseded note) and is **binding** for both
this PRD and the E4 PRD:

- **C4 owns CALIBRATION drift — and *computes* it.** A "90% interval no longer covers 90%
  on live data" is a property of the **model's stated uncertainty vs realized outcomes** —
  the *live extension of C4-M3's per-regime calibration audit*. The coverage statistic, the
  rolling live-window comparison, and the drift threshold all live in **C4's calibration
  machinery** (`src/quant/models/calibration.py`, proposed). The console (E3/E4) **surfaces
  / displays** this signal; it does **not** recompute it.
- **E4 owns FEATURE/DATA-distribution drift — a different signal.** A shift in the
  *distribution of the inputs* (a feature's live μ/σ departing from its catalog stats, a
  feed going stale, a gap in the lake) is `E4`'s scope (`E4-data-market-status.prd.md`
  §"Live feature-drift monitor"). It answers "are the inputs still the inputs we trained
  on?" — **upstream** of, and **independent** from, "is the model's stated confidence still
  honest?".
- **Do NOT duplicate calibration-drift logic into E4.** E4 consumes/displays C4's
  calibration-drift output the same way E3 consumes C2/C3's P&L. The two drift signals are
  complementary (feature drift can *cause* calibration drift, but they are measured on
  different quantities and must not be conflated). If E4 needs the calibration-drift number,
  it reads it from C4's machinery — it does not re-implement coverage testing.

## Evidence

From the code and ratified decisions (read at draft time):

| Fact | Source | Implication for C4 |
|---|---|---|
| `ConfidenceGate.method` is `Literal["always_pass"]`; the gate is inert until C4 | `execution/strategy_registry.py` (`ConfidenceGate`) | C4-M2 **extends the Literal** with real methods (e.g. `conformal`, `quantile`) + their params — a deliberate contract change C4 owns (the C6 PRD names it: "C3/C4 *extend the Literal*"). |
| The sizing seam `sized_weight = vol_target_weight × confidence_scalar` exists with the scalar pinned `1.0` (inert until C4) | `c3-sizing-risk.prd.md` §Scope C3-M3; "confidence-scalar mapping (the C4 seam)" Open Question | C4 supplies the **scalar** and the *mapping* from a calibrated interval/probability to it; C3 owns *where* it multiplies. The allocator is unchanged downstream (confidence enters **once**, at sizing — no double-counting). |
| Confidence enters **once, at sizing — not at the combination step** | `c6-strategy-registry.prd.md` §Open Questions; `c3-sizing-risk.prd.md` §Hypothesis | C4's scalar feeds C3's sizing only; the C6 allocator nets the already-confidence-shaped sized positions and clamps. C4 adds no second confidence weighting. |
| `BacktestResult` retains per-bar `oos_returns` / `oos_forecast_errors` for downstream regime-conditional metrics | `backtest/harness.py` (`BacktestResult`) | C4-M2 extends `BacktestResult` with `oos_prediction_intervals` (per-bar OOS interval bounds) so the calibration audit + sizing can read them — mirroring the existing OOS-series fields, not a parallel container. |
| The repo already slices OOS metrics by regime (`regime_metrics.py` + `VIXThresholdDetector`) and has bootstrap/quantile helpers (`statistics.py`) | `backtest/regime_metrics.py`, `backtest/regimes.py`, `backtest/statistics.py` | C4-M3's **per-regime** coverage audit reuses the *existing* regime axis + quantile machinery — no new regime detector, no new statistics module (DRY; §4). |
| In-sample importance/confidence does not transfer; OOS is the only trustworthy signal (§14, ρ = −0.074 finding) | `docs/METHODOLOGY.md` §14; `docs/PHASE_4A_REPORT.md` | Calibration is judged **on the test fold only** (coverage of OOS realizations). A model calibrated on its training fold proves nothing — the audit is OOS by construction. |
| C4 makes no pre-registered edge claim | this PRD | Like C1/C2/C3/C6, C4 does **not** depend on `A-DSR-GATE`; the gate is calibration-coverage + back-compat + drift, not Sharpe/DSR. |
| Roadmap C4 sketch: M1 method shortlist, M2 models emit calibrated intervals, M3 per-regime calibration audit | `docs/PROJECT_ROADMAP.md` §7 C4 | The three-milestone structure below mirrors the ratified sketch, reframed as the registry field C4 populates + the live-drift extension the §7 boundary note assigns to C4. |
| Live calibration-drift is C4's scope, surfaced in E3/E4 but computed by C4; E4 owns feature/data-distribution drift | `docs/PROJECT_ROADMAP.md` §7 C4↔E4 boundary note; `PRIORITIES.yaml` C4-PRD notes; `docs/project-e/E4-data-market-status.prd.md` | C4-M3 ships the live calibration-drift detector; E4 *displays* it and must not duplicate it. |

Structural facts that shape the design:

- **C4 is a field-population sub-project, not a schema sub-project.** The `confidence_gate`
  schema already exists (C6-M1) and the sizing seam already exists (C3-M3). C4's deliverable
  is (a) extending the `ConfidenceGate` `Literal` + params, (b) implementing the matching
  calibration machinery that emits intervals + a confidence scalar, (c) the per-regime OOS
  coverage audit, (d) the live-drift extension. The bidirectional drift test that already
  guards the registry (`tests/test_strategy_registry.py`) is *extended*, not replaced.
- **Calibration is a coverage property, and the repo already measures OOS outcomes
  per-regime.** A calibrated interval at level `α` should contain the realization with
  empirical frequency `≈ 1−α` **on held-out data, in each regime**. The harness already
  produces per-bar OOS realizations and a regime axis; C4 reuses both rather than
  introducing a parallel evaluation path.
- **Confidence and sizing compose multiplicatively, once.** C4's scalar `∈ (0, 1]` defaults
  to `1.0` (an inert / uncalibrated model sizes exactly as C3's vol-target alone does), so
  C4 landing is **back-compat** with the C3/C6 placeholder behaviour by construction (a
  strategy whose `confidence_gate.method` is `always_pass` is unchanged).

## Users

- **Primary**: **C3 (sizing/risk)** and every **enabled registry strategy**. C3's sizing
  function is the single consumption point for C4's calibrated confidence scalar; after C4 a
  strategy's registry entry selects `confidence_gate.method: conformal` (or `quantile`) and
  the executor sizes its predictions by calibrated confidence — a registry-entry change, not
  a code edit (the C6 promise extended to real confidence).
- **Secondary**: the **deployable models** (`models/arima_baseline.py`, `models/gbm.py`) —
  C4 wraps/extends them to emit calibrated intervals alongside the point forecast they
  already produce, without changing the forecast itself.
- **Tertiary (Project E)**: the **live-monitoring console** (`E3`) and the **data/market
  console** (`E4`) *surface* C4's calibration-drift signal (E3's per-strategy model-output
  monitor; E4's alerting channel), but **C4 computes it**. The boundary above is binding:
  E4 does not duplicate calibration-drift logic.
- **Tertiary (operations)**: whoever runs the daily loop — C4 is what makes a low-confidence
  prediction trade *small* (or not at all) and what raises a flag when a model's stated
  confidence stops being honest live.
- **Not for**: position *sizing* mechanics (C3 — C4 supplies the scalar, does not size);
  the cross-strategy capital budget (C6 pinned `1/N`); changing a forecast's *sign* or
  seeking alpha (C4 calibrates uncertainty, it does not predict); **feature/data-distribution
  drift** (E4); the console UI (E3/E4 render; C4 exposes the quantities); live-capital
  trading (paper only — live is a later `broker` flag on the abstracted `ExecutionBridge`);
  intraday (daily cadence is ratified).

## Hypothesis

We believe that **calibrated confidence — conformal or quantile-regression prediction
intervals whose stated coverage is honest out-of-sample, emitted by the existing models,
verified by a per-regime OOS coverage audit, and consumed once at C3's sizing seam as a
confidence scalar, plus a live calibration-drift detector that flags when stated coverage
decays on live data — all expressed as the value of the existing C6 registry
`confidence_gate` field and the C3 sizing scalar — makes the paper book's exposure
proportional to how trustworthy each prediction is** — for **C3's sizing milestone, the
enabled registry strategies, and the Project E console (which surfaces the drift signal)** —
**closing the "every prediction passes the inert confidence gate and sizes identically
regardless of certainty" gap without making any edge claim**.

We'll know we're right when (all thresholds pinned in "Success Metrics" before any compute,
METHODOLOGY §1 — **pending ratification, see banner** — and reproduced in the C4-M1/M2/M3
gate functions, §2):

- **G1 (OOS coverage accuracy)**: over a shared historical replay window (≥ 2 regimes), the
  **empirical OOS coverage** of a method's `α`-level intervals lands within a pinned band of
  the nominal `1−α` — i.e. a 90% interval demonstrably contains ≈ 90% of held-out
  realizations, materially better-calibrated than the inert/uncalibrated baseline.
- **G2 (per-regime coverage holds where it matters)**: the coverage band is met **in each
  regime** (not just pooled) — calibration does not silently collapse in the high-vol regime
  (the §10 cross-regime discipline applied to coverage).
- **G3 (contract drift + placeholder back-compat + live-drift detector)**: the extended
  `ConfidenceGate` schema passes the C6 **bidirectional drift test** (registry ⇄ code, 0
  unresolved); the **`always_pass` / scalar-`1.0`** path is byte-for-byte preserved (a
  strategy without a calibrated gate sizes exactly as C3's vol-target alone — **0
  regressions**); and the **live calibration-drift detector** flags a *seeded* coverage
  decay (a held-out window engineered to under-cover) and stays silent on a well-calibrated
  window (no false alarm).

If **G1/G2 fails** (no shortlisted method achieves honest OOS coverage, pooled or
per-regime), the verdict is **"calibrated confidence as specified is not achievable on this
universe with these models"** — a valid, pre-committed negative that keeps the
`always_pass` inert gate as the deployed default (confidence stays `1.0`, sizing is
vol-target only) under a new ledger entry, rather than shipping a confidence number the
system has no right to trust. If **G3's back-compat axis fails**, C4 has silently changed the
proven C3/C6 sizing path and must not ship until the regression is found.

## Success Metrics

C4 is **infrastructure** (uncertainty quantification + calibration mechanics), so the gate
is a **calibration-coverage + back-compat + drift** gate, **not** a Sharpe gate —
DSR/deflation is undefined here and C4 does **not** depend on `A-DSR-GATE` (mirroring
C1/C2/C3/C6). **All thresholds below are pinned before any compute (METHODOLOGY §1) and
reproduced in the C4-M1/M2/M3 gate functions (§2) — but the specific numbers are DRAFT
proposals pending human ratification (see top banner); ratification freezes them, after
which a change requires a PRD revision + a new ledger entry.**

| # | Claim | Measured on | Statistic | Threshold (pinned — DRAFT, pending ratification) | Reference |
|---|---|---|---|---|---|
| G1a | Intervals achieve honest OOS coverage | a calibrated method over the replay window (≥ 2 regimes), test fold only | \|empirical OOS coverage − nominal (1−α)\| at α = 0.10 | **≤ 0.05 (90% interval covers 85–95% OOS)** | per-bar OOS realizations (`BacktestResult.oos_returns`) |
| G1b | Calibration beats the uncalibrated baseline | same window | calibrated method's coverage error vs the uncalibrated/naive-interval baseline's | **calibrated error < baseline error** (strictly) | uncalibrated baseline (point forecast ± in-sample residual σ) |
| G2 | Coverage holds per-regime, not just pooled | each regime over the window (`regime_metrics`) | max over regimes of \|coverage − (1−α)\| | **≤ 0.10 in every regime** (looser than pooled; noisier per-regime n) | `backtest/regimes.py` + `regime_metrics.py` |
| G3a | Schema drift holds, both directions | the extended registry × code | (unresolved refs, schema/enum mismatches) | **(0, 0)** | `tests/test_strategy_registry.py` (§6) |
| G3b | Inert gate path unchanged (back-compat) | the `always_pass` / scalar-`1.0` universe over the window | sizing regressions vs the C3/C6 placeholder | **exactly 0** | C3/C6 sizing-reconciliation (the `× 1.0` no-op) |
| G3c | Live calibration-drift detector is correct | a seeded under-covering window ∪ a well-calibrated window | (missed drift trips, false-alarm trips) | **(0, 0)** — flags the seeded decay, silent on the calibrated one | C4 calibration-drift detector (this PRD) |

Notes on the metric choices:

- **G1 and G2 are the merge-blocking calibration gates; G3 is the contract-integrity +
  drift-correctness gate.** G3b (back-compat) is non-negotiable: C4 extends a *proven*
  sizing path (the C3 seam at scalar `1.0`) and must not regress it.
- **G1a's ±0.05 band is a materiality bar, DRAFT pending ratification.** Empirical coverage
  is a noisy estimate of a population coverage; ±0.05 is proposed as "the interval is
  demonstrably honest" while leaving room for finite-sample noise. The companion G1b
  (strictly beats the uncalibrated baseline) is the qualitative claim that survives even if
  the absolute band is later re-tuned. **Rationale for the proposal**: a tighter band (e.g.
  ±0.02) risks failing on sampling noise alone; a looser band (±0.10) would pass intervals
  that barely calibrate. The exact number is the central ratification decision.
- **G2's per-regime band is intentionally looser than G1a's pooled band** (±0.10 vs ±0.05):
  per-regime sample sizes are smaller, so the same materiality stance tolerates more
  finite-sample noise. The looseness is pinned, not discovered (§1).
- **Materiality before significance (§10).** G3a/G3b/G3c are deterministic predicates and
  G1/G2 are deterministic OOS replays; there is no statistical-significance axis. The bars
  are pure materiality thresholds pinned in code (pending ratification).
- **No new tolerance is invented for sizing reconciliation.** C4's scalar is `1.0` on the
  back-compat path (G3b), so the C3/C6 reconciliation it must preserve runs under the
  *existing* 1% drift-locked constant (C2-M3 / C6-M2); C4 adds none of its own.

## Scope

**MVP** — the three milestones below, executed in order, reusing the C6 registry +
`confidence_gate` field, the C3 sizing seam, the Phase-1 harness + `BacktestResult`, the
regime axis, and the bootstrap/quantile machinery. **No new model, no new data source, no
new universe, no new sizing path (C3), no cross-strategy budgeting change, no intraday, no
live capital, no feature/data-distribution drift (E4)** — C4 adds only **calibration
machinery**, the **confidence scalar**, the **per-regime coverage audit**, and the **live
calibration-drift detector**, all as the value of the *existing* C6 `confidence_gate` field
+ the C3 scalar.

1. **C4-M1 — Method shortlist + theory writeup.** Pre-commit the calibration method(s):
   **conformal prediction vs. quantile regression vs. bootstrap-CI**, picking **1 for ARIMA,
   1 for GBM** (the roadmap C4-M1 outcome), with a written rationale (`docs/concepts/
   confidence-calibration.md`, mirroring B2-M1's `oos-attribution.md`) covering the
   coverage guarantee each method offers, its OOS-only evaluation (§14), and why it fits
   the model's output type (continuous mean vs class score). **Spec/theory milestone — no
   sizing/compute yet; the gate constants (G1/G2 bands) are frozen here before any coverage
   is measured (§1).**
2. **C4-M2 — Models emit calibrated intervals + the confidence scalar.** Extend
   `ConfidenceGate.method` with the shortlisted method(s) + params, implement the
   calibration machinery (`src/quant/models/calibration.py`, proposed) that wraps a model to
   emit `oos_prediction_intervals` alongside its point forecast, extend `BacktestResult`
   with the per-bar interval field, and implement the **confidence-scalar mapping** (interval
   width / calibrated probability → scalar `∈ (0, 1]`, default `1.0`) that C3's sizing seam
   reads. Ships the **G1 gate function** (OOS coverage accuracy + beats uncalibrated).
   Extends the C6 bidirectional drift test for the new enum + params. Tests land with the
   change (§15); a cross-module E2E notebook exercises model → harness → calibrated intervals
   → confidence scalar → C3 sizing seam on real fixtures and renders the G1 verdict (§17).
   **Must not touch walk-forward split logic** (`backtest/CLAUDE.md`); calibration consumes
   the harness's OOS folds, it does not re-split.
3. **C4-M3 — Per-regime calibration audit + live calibration-drift detector.** Implement the
   **per-regime OOS coverage audit** (do `α`-level intervals contain `1−α` of OOS
   realizations, *in each regime*?) reusing `regime_metrics` + the regime axis, and the
   **live calibration-drift detector** — the *live extension* of that audit: a rolling
   live-window coverage statistic + a pinned drift threshold that flags when live coverage
   decays below the calibrated target. **This detector is C4's; E3/E4 surface its output but
   do not recompute it** (the boundary above). Ships the **G2 gate function** (per-regime
   coverage) **and the G3c gate function** (drift-detector correctness on seeded windows).
   Tests + E2E notebook.

**Out of scope**

- **Position sizing mechanics** — **C3**. C4 supplies the confidence *scalar*; C3 owns the
  `× confidence_scalar` multiply and all vol-target / cap / stop logic. No parallel sizing
  path (§4).
- **Feature / data-distribution drift** — **E4**. C4 computes *calibration* drift (stated vs
  realized coverage); E4 computes *input-distribution* drift. E4 must not duplicate
  calibration-drift logic; it reads C4's output (the binding boundary above).
- **The console UI** — E3/E4 *render* C4's calibration-drift + interval quantities; C4
  exposes them. C4 ships no React/TS.
- **Changing a forecast's sign / seeking alpha** — C4 calibrates the uncertainty *around* an
  existing point forecast; the sign (the C2 `sign()` decision rule) is untouched.
- **Cross-strategy capital budgeting** — C6 pinned equal-weight `1/N`; unchanged by C4.
- **A new confidence schema / registry** — C4 populates the *existing* `confidence_gate`
  field and extends the *existing* `BacktestResult` / drift test. No parallel structures
  (§4).
- **Live capital / new models / data / universe / intraday** — C4 calibrates the *existing*
  models on the *existing* feeds, paper only.

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->

| # | Milestone | Outcome | PRIORITIES task | Depends on |
|---|---|---|---|---|
| 1 | Method shortlist + theory | conformal/quantile/bootstrap shortlisted (1 for ARIMA, 1 for GBM); rationale + OOS-only evaluation written; G1/G2 bands frozen | `C4-M1` | `C4-PRD`, `C6-M1` |
| 2 | Calibrated intervals + confidence scalar | `ConfidenceGate.method` gains the real method(s) + params; calibration machinery emits `oos_prediction_intervals`; `BacktestResult` extended; the confidence scalar feeds C3's sizing seam; G1 (coverage accuracy + beats uncalibrated) passes; drift test extended | `C4-M2` | `C4-M1`, `C3-M3` |
| 3 | Per-regime audit + live drift detector | per-regime OOS coverage audit (G2) passes; live calibration-drift detector flags seeded decay / silent on calibrated window (G3c); E3/E4 surface (do not recompute) the signal | `C4-M3` | `C4-M2` |
| Gate | Honest OOS coverage pooled (G1) AND per-regime (G2) AND contract drift + inert-gate back-compat + drift-detector correctness (G3) all hold | Binary. **Pass** → registry-driven calibrated confidence is in code; C3 sizes by confidence; E3/E4 surface drift. **Fail** → the verdict (which axis failed) sends that milestone back under a new ledger entry; the `always_pass` inert gate (scalar `1.0`, vol-target-only sizing) remains the deployed default. | — | — |

## Pre-committed gate (verbatim — implemented across C4-M1/M2/M3 as `c4_calibration_gate`)

> **DRAFT — the constants below are proposals pending human ratification (top banner,
> METHODOLOGY §1). On ratification they freeze; a change thereafter requires a PRD
> revision + a new ledger entry, not an in-flight override.**

The gate functions are the source of truth; this prose describes them (METHODOLOGY §2).
C4's gate is the conjunction of:

1. **OOS coverage accuracy (G1, C4-M2)** — over the shared replay window (≥ 2 regimes),
   test fold only, `abs(empirical_oos_coverage − (1 − α)) <= COVERAGE_TOLERANCE` at the
   pinned `α = 0.10` (DRAFT default `COVERAGE_TOLERANCE = 0.05`), **and** the calibrated
   method's coverage error is strictly less than the uncalibrated baseline's on the same
   window.
2. **Per-regime coverage (G2, C4-M3)** — the max over regimes of
   `abs(coverage − (1 − α))` is `<= PER_REGIME_COVERAGE_TOLERANCE` (DRAFT default `0.10`),
   so calibration does not collapse in any single regime (§10).
3. **Contract drift + back-compat + drift-detector correctness (G3, C4-M2/M3)** — the
   extended `ConfidenceGate` schema yields **0** unresolved refs / schema mismatches in the
   C6 bidirectional drift test (both directions, §6); the `always_pass` / scalar-`1.0`
   inert path produces **0** sizing regressions vs the C3/C6 placeholder
   (`G3B_MAX_BACKCOMPAT_REGRESSIONS = 0`); and the live calibration-drift detector has **0**
   missed trips on a seeded under-covering window and **0** false alarms on a well-calibrated
   window (`G3C_MISSED_TRIPS = 0`, `G3C_FALSE_ALARMS = 0`).

`α = 0.10`, `COVERAGE_TOLERANCE`, `PER_REGIME_COVERAGE_TOLERANCE`,
`G3B_MAX_BACKCOMPAT_REGRESSIONS`, `G3C_MISSED_TRIPS`, `G3C_FALSE_ALARMS`, the
confidence-once-at-sizing rule, the `confidence_scalar ∈ (0, 1]` default-`1.0` mapping
contract, and the OOS-only coverage definition are all pinned constants (DRAFT, pending
ratification). The sizing reconciliation C4 must preserve on the back-compat path reuses the
*existing* C2-M3 / C6-M2 1% constant under its drift lock; C4 invents no new tolerance.

## Open Questions

- [ ] **Calibration method per model (the C4-M1 decision).** Pinned proposal (DRAFT):
      **split conformal prediction** for GBM (distribution-free finite-sample coverage on
      the OOS fold; wraps the class score) and **quantile / residual-bootstrap intervals**
      for ARIMA (the AR(1) one-step forecast has a natural residual distribution). Bootstrap
      CI is the fallback if conformal's exchangeability assumption is untenable on
      autocorrelated returns. Which method per model is the first ratification decision,
      frozen in C4-M1 before any coverage is measured.
- [ ] **Confidence-scalar mapping (interval/probability → scalar).** Pinned proposal
      (DRAFT): map a calibrated interval to a scalar `∈ (0, 1]` that is **monotone in
      certainty** — e.g. narrower interval (or higher calibrated probability of the realized
      sign) → scalar nearer `1.0`; at the saturating-uncertainty end the scalar approaches a
      pinned **floor** (`CONFIDENCE_SCALAR_FLOOR`, e.g. `0.0` = gate the prediction out, or a
      small positive floor = always trade a token size). The exact functional form + floor
      are pinned in C4-M2; the scalar default is `1.0` (back-compat). C3 owns *where* it
      multiplies; C4 owns the *mapping*.
- [ ] **`α` (interval level) — single vs per-strategy.** Pinned proposal (DRAFT): a single
      `α = 0.10` (90% intervals) across strategies for C4, with a per-strategy
      `confidence_gate.alpha` field available for a later override. A per-strategy default is
      a ratification decision, not a C4 scope expansion.
- [ ] **Live calibration-drift window + threshold.** Pinned proposal (DRAFT): a rolling
      **live window** (e.g. trailing **63 live bars / ~3 months**, mirroring C3's vol
      look-back) over which live coverage is recomputed; drift is flagged when
      `abs(live_coverage − (1 − α))` exceeds a pinned `DRIFT_TOLERANCE` (DRAFT default
      `0.15` — deliberately looser than the backtest G2 band: live windows are shorter and
      noisier, so the alert fires on *material* decay, not sampling noise; §10 alert-fatigue
      mitigation). Window + threshold pinned in C4-M3 before any live coverage is measured.
- [ ] **Calibration set source (conformal split).** Split conformal needs a held-out
      calibration set distinct from train and test. Proposed (DRAFT): carve it from the
      tail of each walk-forward *training* window (never the test fold — that would leak;
      §14), respecting the harness's purge/embargo. The exact split fraction is pinned in
      C4-M2 and must satisfy `backtest/CLAUDE.md` invariant 4 (fold length ≫
      `label_horizon + embargo`); flag rather than silently shrink the train set.
- [ ] **Replay window selection (G1/G2).** The replay window spans ≥ 2 regimes (reusing
      `backtest/regimes.py`), pinned in C4-M1 before any coverage is measured, so calibration
      is not measured against a hand-picked favourable span (§1/§10).

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| C4 silently regresses the proven C3/C6 inert-gate sizing path (G3b fails) | Low | **Very High** | G3b is a 0-regression back-compat gate: the `always_pass` / scalar-`1.0` path is preserved and asserted to size byte-for-byte as C3's vol-target alone before the calibrated branch ships. |
| No shortlisted method achieves honest OOS coverage on this universe (G1/G2 fails) | Medium | Medium | A valid, pre-committed negative (METHODOLOGY §5): the inert `always_pass` gate (scalar `1.0`) stays the deployed default; the method goes back to design under a new ledger entry. The negative is documented, not hidden. |
| Calibration holds pooled but collapses in the high-vol regime (G2 fails) | Medium | High | G2 is a per-regime predicate (§10) — pooled coverage cannot mask a regime where the intervals are dishonest exactly when uncertainty matters most. |
| Conformal exchangeability assumption violated by autocorrelated returns → coverage guarantee void | Medium | High | C4-M1 evaluates the assumption explicitly; bootstrap-CI / quantile fallback is pre-committed; coverage is *empirically* verified OOS (G1) regardless of the method's theoretical guarantee. |
| Calibration-drift logic duplicated into E4 (boundary violated) | Medium | Medium | The binding boundary is stated in this PRD §"C4 ↔ E4 boundary", the E4 PRD, and ROADMAP §7: E4 owns feature/data-distribution drift and *reads* C4's calibration-drift output; it does not recompute coverage. |
| Confidence double-counted (weighted at sizing *and* combination) | Low | Medium | Pinned rule carried from C6/C3: confidence enters **once**, at sizing; the allocator only nets + clamps. C4 supplies one scalar to one seam; it adds no second weighting. |
| Live calibration-drift alert fatigue (fires on sampling noise) | Medium | Medium | `DRIFT_TOLERANCE` is pinned looser than the backtest band, with a minimum live-window size (§10 materiality-before-significance); G3c asserts it stays silent on a well-calibrated window. |
| Calibration leaks the test fold (calibration set overlaps OOS) | Low | **Very High** | The conformal calibration set is carved from the *train* window under purge/embargo (§14, `backtest/CLAUDE.md`); coverage is measured on the untouched test fold. Asserted in C4-M2 tests. |
| C4 ships but no real (non-placeholder) strategy is ever deployed | Medium | Low | Independent by design (the C6/C3 rationale): calibrated confidence accrues to *any* future deployable strategy, regardless of any B verdict; the inert default is harmless until a strategy opts in. |

## Sequencing notes

- **C4 populates the C6 contract + fills the C3 seam; it does not precede or duplicate
  them** (METHODOLOGY §4). C6-M1 shipped `confidence_gate` as a placeholder and C3-M3
  shipped the sizing seam at scalar `1.0` *expressly so* C4 fills them — this PRD is the
  consumer half of that contract-before-consumer split. C4 extends the `Literal` and the
  `BacktestResult` container; it defines no parallel schema.
- **C4-M1/M2/M3 ship their gate constants pinned before any coverage/drift is measured**
  (§1/§2) — and those constants are **DRAFT pending ratification** (top banner) until the
  PRD is ratified. The back-compat path reuses the existing 1% reconciliation constant under
  its drift lock; no new tolerance is invented (§6).
- **C4 must not touch walk-forward split logic.** Calibration consumes the harness's OOS
  folds (and a train-window-tail calibration set under the *existing* purge/embargo); it
  leaves `walkforward.py` / `harness.py` invariants untouched (`backtest/CLAUDE.md`). The
  harness self-tests stay green. Leaking the test fold into the calibration set is the
  cardinal sin here and is explicitly guarded (§14).
- **The C4 ↔ E4 boundary is binding.** C4 computes calibration drift; E3/E4 surface it; E4
  owns feature/data-distribution drift and must not duplicate calibration-drift logic. This
  is restated in §"C4 ↔ E4 boundary", the Risks table, and is consistent with the E4 PRD +
  ROADMAP §7.
- **No new module convention (mostly).** C4 edits existing files
  (`execution/strategy_registry.py`, `backtest/harness.py`, `tests/test_strategy_registry.py`)
  and adds milestone E2E notebooks + a concepts doc (`docs/concepts/confidence-calibration.md`,
  mirroring B2-M1's `oos-attribution.md`). The one *new* module —
  `src/quant/models/calibration.py` — sits under the existing `models/` package
  (convention-*following*, the natural home for model-output calibration, mirroring how
  `backtest/attribution.py` landed for B2-M2); it is named in the C4-M2 deliverable and is
  not a new top-level directory.
- **C4 does NOT depend on `A-DSR-GATE`** (no Sharpe/edge claim → no deflation), mirroring
  C1/C2/C3/C6. It depends only on `C2-M2` (the bridge) and `C6-M1` (the registry contract),
  already encoded in `PRIORITIES.yaml`; C4-M2 additionally on `C3-M3` (the sizing seam it
  fills).
- **Ledger discipline.** C4 is infrastructure, not a research trial — it makes no
  pre-registered edge claim, so it contributes **no** research trials to the deflation `N`.
  A C4 milestone run may record an **audit-only** ledger entry (`n_comparisons = 0`) per the
  A-LEDGER-RUNNERS pattern (mirrors C1/C2/C3/C6).
- **Milestone-task creation (deferred).** Only `C4-PRD` exists in `PRIORITIES.yaml` today;
  the `C4-M1`/`C4-M2`/`C4-M3` backlog tasks are created when C4 execution is scheduled (the
  C4-PRD note says "re-prioritize once C6-M1 lands" — C6-M1 has landed). Per the
  AGENT_OPERATION Step 7 corollary, when those tasks are created their ids **must** be added
  to **`C-CLOSE.depends_on`** (which currently lists `C4-PRD` as the resolvable proxy) and,
  for the milestone(s) the console surfaces, coordinated with the relevant Project E task.
  This PRD does not pre-create them, keeping the C4-PRD task's footprint to its single
  deliverable.
- **Downstream unblock.** `C4-PRD` is the resolvable proxy `C-CLOSE` depends on for the C4
  gate; the real unblock of confidence-aware sizing (C3) and the E3/E4 drift surface follows
  the C4 milestone gates passing.

---
*Status: DRAFT (2026-06-29) — pre-commitment for Project C4. **The thresholds in
"Success Metrics" and "Pre-committed gate" (G1 OOS coverage ±0.05 at α = 0.10 + strictly
beats uncalibrated, G2 per-regime coverage ±0.10, G3 0 drift / 0 back-compat regressions /
0 missed-drift + 0 false-alarm trips) and the pinned design rules (conformal/quantile
method shortlist, confidence-scalar ∈ (0,1] default-1.0 mapping, confidence-once-at-sizing
seam consumed by C3, live calibration-drift window/threshold, the C4-computed /
E3-E4-surfaced calibration-drift boundary) are DRAFT proposals that need human ratification
before implementation (METHODOLOGY §1).** On ratification they freeze; changes thereafter
require a PRD revision and a new ledger entry, not an in-flight override. Next: ratify the
thresholds, then `/plan` turns C4-M1 into an implementation plan.*
