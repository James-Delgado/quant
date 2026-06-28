# B3 — Alternative Data: Options-Implied Surfaces

> **Project**: B (Predictive research, post-4A) — sub-project B3.
> **Roadmap**: [`docs/PROJECT_ROADMAP.md`](../../docs/PROJECT_ROADMAP.md) §4 Project B
> (B3 row), §6 (sequential-within-B rationale), §7 (B3 conditional-on-B1 note).
> **Methodology** (binding): [`docs/METHODOLOGY.md`](../../docs/METHODOLOGY.md).
> **Activating verdict**: [`docs/B1_REPORT.md`](../../docs/B1_REPORT.md) §4 — B1's
> pre-committed NO-GO on all four targets trips the B3 skip-path (METHODOLOGY §5).
> **Backlog tasks**: `B3-M1`, `B3-M2`, `B3-M3` (to be appended on ratification) in
> [`docs/PRIORITIES.yaml`](../../docs/PRIORITIES.yaml).

## Problem

Two pre-committed negatives now bound the search space:

1. **Phase 4A** proved next-bar (and 1-day signed) **return** is structurally
   unlearnable from the current public feature set on the Dow-30 + SPY/QQQ/IWM
   sandbox ([`PHASE_4A_REPORT.md`](../../docs/PHASE_4A_REPORT.md) §3: no GBM arm
   beat ARIMA(1,0,0) in any required regime, DM p = 1.0000 everywhere).
2. **B1** held the equity-only feature set fixed and varied the **target** — across
   four pre-committed prediction objects (21-day drawdown, 21-day realized vol,
   5-day directional, 21-day cumulative direction) and all three required regimes
   on the full 33-symbol × ~22-year panel, **no target cleared** materiality +
   significance + deflation ([`B1_REPORT.md`](../../docs/B1_REPORT.md) §3). The
   binding failure was *materiality*: the GBM does not beat a naive per-target
   baseline by the pinned margin.

B1's §4 draws the binding inference verbatim: *"the constraint B1 isolates is the
**information set** or the **universe**, not the target framing."* B3 tests the
**information-set** half of that fork. The question is no longer *what should we
predict?* (B1 answered: nothing in the equity-only set is learnable) but *does a
class of information the equity-only feature set never contained — the
**options-implied** view of forward risk — unlock any of those same targets?*

Options markets price forward volatility, skew (crash risk), and directional
positioning (put-call flow) that spot OHLCV + macro + sentiment features cannot
express. The textbook claim is that **implied** quantities lead **realized** ones:
the VIX term structure encodes the market's forward-vol expectation; the Cboe SKEW
index encodes tail-risk pricing; put-call ratios encode hedging/sentiment flow.
These are exactly the objects most mechanically linked to B1's two *least-bad*
targets — realized vol (T2, the Borda leader) and drawdown (T1) — which failed on
equity-only features but are the natural consumers of an implied-vol signal.

B3 is the **cheapest possible** test of this hypothesis. It uses **only the free,
market-level Cboe daily settlement series** (VIX complex, SKEW, put-call ratios) —
no paid per-symbol options vendor, no new universe. If even the free implied-vol
view adds no extractable edge over equity-only features on any target, that is a
high-value negative that argues the binding constraint is the **universe** (→ B4)
or requires **paid per-symbol surfaces** (a separately-gated, cost-bearing
follow-on) — and B3 establishes it before spending an alt-data budget, exactly the
discipline ROADMAP §6 demands ("spending on a Cboe ingestor before knowing whether
the target is correct is exactly the mistake Phase 4A documented" — B1 has now
answered the target question).

## Evidence

From [`B1_REPORT.md`](../../docs/B1_REPORT.md) §3 (full 33-symbol panel, 179,420
OOS rows, OOS span 2004-06-18 → 2026-03-31, all three regimes; N = 74 at B1 close):

| B1 Target | Primary metric | Materiality (regimes met / need 2) | Deflation | Gate |
|---|---|---:|---|:--:|
| `drawdown_21d` (T1) | ROC-AUC | 0 / 3 | skill-z = −5.88 | FAIL |
| `realized_vol_21d` (T2) | MAE | 1 / 3 | skill-z = −31.86 | FAIL |
| `directional_5d` (T3) | AUC + Sharpe | 0 / 3 | DSR = 0.000 | FAIL |
| `directional_21d` (T4) | AUC + Sharpe | 0 / 3 | DSR = 0.009 | FAIL |

Structural findings that motivate adding options-implied data specifically:

- **The two vol/risk targets failed against *vol-state* baselines, not against
  options.** B1's T2 lost to a RiskMetrics EWMA(λ=0.94) persistence forecast; T1
  lost to an EWMA-vol-implied drawdown proxy ([`B1_REPORT.md`](../../docs/B1_REPORT.md)
  §3.2). Both baselines are **backward-looking** realized-vol persistence. The
  implied-vol surface is the canonical **forward-looking** complement: if realized
  vol is already well-forecast by its own persistence, the marginal information
  must come from a *different* source — the options market's forward expectation is
  the textbook candidate, and it was absent from B1's feature set entirely.
- **The directional targets are ARIMA-dominated** ([`B1_REPORT.md`](../../docs/B1_REPORT.md)
  §3.1–3.2): return *direction* at 5- and 21-day horizons is captured by the AR(1)
  trend prior. Put-call ratios and skew are a *sentiment/positioning* signal
  orthogonal to trend — whether they add incremental directional edge is an open,
  testable question, but a lower-prior one than the vol/risk targets.
- **The harness already carries one implied-vol feature** — `vix_regime` is derived
  from VIX in the regime detector (`backtest/regimes.py`, `VIXThresholdDetector`).
  That single level is used only to *partition* regimes, never as a *predictive
  feature*, and the term structure / skew / flow dimensions are entirely unused.
  B3 promotes the implied-vol view from a regime tag to a feature group.

The honest counter-evidence (carried into Risks): the free Cboe series are
**market-level** — one value per date, shared across all symbols — so they enter
the panel as **panel-constant columns**, structurally identical to the FRED macro
features (`features/engineering.py`, `FRED_PUBLICATION_LAGS`). Phase 4A found the
macro features added little cross-sectional signal. B3's hypothesis is specifically
that the implied-vol *regime/forward-risk* information is more predictive of the
*vol/drawdown* targets than macro levels were of *return* — a different
(target, data) pairing, not a re-run of the same bet.

## Users

- **Primary**: the researcher, working in `notebooks/14_*` / `16_*` and the
  existing harness / ablation / runner substrate.
- **Secondary (future)**: the Phase-5 continuous-research agent pair (Agent F
  expanded to *all* candidate-generation artifact types, including **alternative
  data feature groups**, per ROADMAP §4 Project D). B3's `ingest/cboe.py`, the
  options-implied catalog records, and `b3_gate_report` become contracts those
  agents read and call when proposing new data sources.
- **Not for**: production traders, live capital. B3 is offline research only. (B3
  *feeds* Project C's deployment loop a candidate (target, feature-set) tuple if
  one clears; it does not deploy. The options ingestor, if it clears, would later
  be promoted into the C1 same-day pipeline — out of scope here.)

## Hypothesis

We believe that **adding a free, market-level options-implied feature group — VIX
term structure (VIX / VIX3M / VIX9D and their spreads), the Cboe SKEW index, and
Cboe put-call ratios (equity, index, total) — to the existing M6 equity-only
feature set produces an extractable, deflation-surviving incremental edge on at
least one of the four B1 targets**, on the same Dow-30 + ETF universe, for **the
researcher (and the future continuous-agent pair)**.

We'll know we're right when **at least one (target, augmented-feature-set) arm's
GBM beats the *same target's M6 equity-only GBM baseline* on that target's primary
metric by the pre-committed incremental margin in ≥ 2 of the 3 required regimes
(qe_bull, covid, rate_cycle), the paired 90% block-bootstrap CI of the incremental
delta excludes 0 in ≥ 1 required regime, AND the incremental result survives
deflation (DSR > 0, or the vol-target skill-z analog > 0, at the cumulative ledger
N at completion).** All three conditions are required. The comparison baseline is
the **equity-only model on the same target** — B3 measures the *marginal value of
the options data*, an add-one feature-group ablation (METHODOLOGY §14, OOS-only
attribution), not a fresh target-vs-naive comparison.

If no target clears, the verdict is **"no extractable edge from free market-level
options-implied data on this universe and these targets"** → the conditional
trigger for B4 (universe shift) and a flag that any further alt-data spend must be
on **paid per-symbol implied-vol surfaces** under a new, separately-gated PRD.

## Success Metrics

Per-target primary metric, baseline, and pre-committed incremental thresholds.
**All numeric thresholds are pinned here before any compute touches B3
(METHODOLOGY §1); they are reproduced verbatim in `b3_gate_report` (METHODOLOGY §2)
as the source of truth.** The four targets and their metrics are inherited
unchanged from B1's `TARGET_CATALOG` (`features/targets.py`) — B3 varies only the
**feature set**, so the targets, horizons, and per-target metric callables are
already instrumented and parity-tested. Significance is the same paired
**stationary block bootstrap** (21-day blocks) of the per-regime **incremental**
metric delta (augmented − equity-only), reusing
`backtest/statistics.bootstrap_metric_delta_ci`.

| # | Target (inherited from B1 `TARGET_CATALOG`) | Primary OOS metric | B3 baseline | Incremental materiality (per required regime) | Significance | Deflation |
|---|---|---|---|---|---|---|
| T1 | `drawdown_21d` — `P(max drawdown > 5% over next 21 bars)` | ROC-AUC | **M6 equity-only GBM** on T1 | Δ(AUC<sub>aug</sub> − AUC<sub>base</sub>) ≥ **0.02** | paired block-bootstrap 90% CI of the incremental ΔAUC excludes 0 in ≥ 1 required regime | skill-z analog > 0 at ledger N |
| T2 | `realized_vol_21d` — log realized vol | MAE on log realized vol | **M6 equity-only GBM** on T2 | Δ(MAE<sub>base</sub> − MAE<sub>aug</sub>) ≥ **5%** relative reduction | paired block-bootstrap 90% CI of the incremental ΔMAE excludes 0 in ≥ 1 required regime | skill-z analog > 0 at ledger N |
| T3 | `directional_5d` — `sign(ret_5d)` | ROC-AUC **and** tradeable Sharpe of `sign(pred)` | **M6 equity-only GBM** on T3 | Δ(AUC) ≥ **0.02** **and** Δ(Sharpe) ≥ **0.10**, both incremental vs the equity-only arm | paired block-bootstrap 90% CI excludes 0 (on whichever metric is gated) in ≥ 1 required regime | DSR > 0 at ledger N (Sharpe arm) |
| T4 | `directional_21d` — `sign(ret_21d)` | ROC-AUC **and** tradeable Sharpe of `sign(pred)` | **M6 equity-only GBM** on T4 | Δ(AUC) ≥ **0.02** **and** Δ(Sharpe) ≥ **0.10**, both incremental vs the equity-only arm | paired block-bootstrap 90% CI excludes 0 in ≥ 1 required regime | DSR > 0 at ledger N (Sharpe arm) |

Notes on the metric choices (consistent with B1; the only change is the baseline):

- **The baseline is the equity-only model, not a naive predictor.** B1 already
  established that none of the four targets beats its *naive* baseline. B3's
  question is strictly *marginal*: does the options group move the **equity-only
  GBM** by the pinned increment? Pinning the same ΔAUC ≥ 0.02 / ΔSharpe ≥ 0.10 /
  ΔMAE ≥ 5% bars (carried from B1 / the METHODOLOGY pre-registration example) keeps
  the materiality scale commensurable across B-cycle sub-projects.
- **A non-tradeable target (T1, T2) gates on its forecast-skill metric only** and
  deflates with the skill-z analog (the same machinery B1-M2 added). **Directional
  targets (T3, T4) must clear *both* incremental AUC *and* incremental Sharpe** —
  an options group that improves ranking but not the tradeable signal is not a
  deployment edge.
- **Composite ranking** across the four targets and across regimes uses the
  balanced **Borda** method (METHODOLOGY §10), never cherry-picking the winning
  regime. As in B1, the Borda leader still has to clear the gate outright; Borda
  ranks the least-bad incremental margin, not an edge.

## Scope

**MVP** — three milestones, executed in order, reusing the existing substrate
(harness, regime detector, ablation orchestrator, runner pattern, ledger, catalog,
and the B1 `TARGET_CATALOG`). The **only new ingredient is a market-level
options-implied feature group**; the universe, targets, model class, and gate
machinery are inherited.

1. **B3-M1 — Cboe ingestor + options-implied feature group + catalog + gate
   function.** Three deliverables land together (contract before consumer,
   METHODOLOGY §2/§4):
   - `src/quant/ingest/cboe.py` — pulls the **free Cboe daily settlement** series
     from their public CSV endpoints: VIX, VIX3M, VIX9D (term structure), the SKEW
     index, and the equity / index / total put-call ratios. Writes to the lake via
     the existing `storage/lake.write_processed` path with a pandera schema
     (`schemas.py`), mirroring `fred_macro.py`. **Point-in-time integrity is the
     load-bearing concern**: each series gets a pinned publication lag (Cboe settle
     is EOD same-session, available next morning — so a date-`t` value is a
     *day-`t` observation* usable for a `t+1` decision, lag 0 relative to the
     OHLCV bar; this is asserted, not assumed, and added to an options-lag map
     analog of `FRED_PUBLICATION_LAGS`). Per-series history start dates differ
     (VIX 1990, SKEW 1990, VIX3M ~2007, VIX9D ~2011, P/C ratios ~2003/2006); M1
     documents each and the pre-start NaN-handling policy.
   - The feature group wired into `features/engineering.build_features()` as
     **lagged panel-constant columns** (the FRED join pattern), plus derived
     spreads/ratios (e.g. `vix_term_slope = VIX3M − VIX`, `vix_short_ratio =
     VIX9D / VIX`) chosen *a priori* in M1, never after seeing a result.
   - Catalog records for every new column in `features/catalog.yaml` +
     `features/catalog.py` under the existing **bidirectional drift test**
     (`tests/test_catalog.py`, METHODOLOGY §6).
   - **`b3_gate_report`** in `backtest/regime_metrics.py` — a thin wrapper over
     `b1_gate_report` that fixes the comparison to *augmented vs equity-only* and
     pins the incremental thresholds above, reading the deflation N from
     `quant.ledger.cumulative_trial_count()`. Tests land with the module
     (METHODOLOGY §15).
2. **B3-M2 — Feature-group ablation matrix on the 5-symbol × 8-year slice.**
   `notebooks/14_b3_options_ablation.ipynb`: for each of the four B1 targets, an
   **add-one feature-group ablation** — `{M6 equity-only (base), M6 + options
   group (augmented)}` — reusing `backtest/ablation.make_add_one_sets`. Verdict via
   `b3_gate_report` on the incremental delta; Borda composite across regimes. Slice
   verdict is **provisional** (METHODOLOGY §11); any target showing incremental
   per-regime edge carries to M3.
3. **B3-M3 — Full-panel confirmation of slice-winners (compute-gated).**
   `scripts/run_b3_arms.py` (mirrors `run_b1_arms.py`: per-arm parquet checkpoints,
   idempotent, `--smoke` mode, `--log-ledger` writing one ledger entry per arm via
   `quant.ledger.append_ledger_entry`, so the deflation N stays current).
   `notebooks/16_b3_exit_gate.ipynb` is checkpoint-only (METHODOLOGY §7).
   `docs/B3_REPORT.md` is the written verdict (the §20/§21 analog of
   `B1_REPORT.md`).

**Out of scope**

- **Paid per-symbol implied-vol surfaces.** Per-symbol IV skew/term structure
  requires a paid options-data vendor (OptionMetrics IvyDB, ORATS, or equivalent).
  B3 tests *only* the free market-level Cboe series. A surfaced need for per-symbol
  surfaces is a **finding** that motivates a separate, cost-gated PRD (provisionally
  "B3.5 — per-symbol options surfaces"), not a B3 deliverable. This bound is the
  central cost-discipline pre-commitment (ROADMAP §6).
- **Intraday / live options data.** Daily EOD settle only, matching the daily
  cadence ratified in ROADMAP §8. Same-day live ingestion (promotion into the C1
  pipeline) is deferred until and unless B3 clears.
- **New targets or model classes.** B3 reuses the B1 `TARGET_CATALOG` and the GBM +
  ARIMA baselines unchanged. The only varied axis is the feature set.
- **Universe changes.** Dow-30 + ETF sandbox is fixed for B3; universe shift is B4.
- **The continuous-agent harness (Phase 5).** B3 leaves agent-consumable artifacts
  (`ingest/cboe.py`, options catalog records, `b3_gate_report`, ledger entries) but
  builds no agents.

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->

| # | Milestone | Outcome | PRIORITIES task | Depends on |
|---|---|---|---|---|
| 1 | Cboe ingestor + options feature group + catalog + gate | Free Cboe series are PIT-lagged panel columns in `build_features`; every column has a catalog record under the bidirectional drift test; `b3_gate_report` exists in code with all incremental thresholds pinned, *before* any ablation runs | `B3-M1` | `B3-PRD` |
| 2 | Options feature-group ablation matrix (slice) | Researcher knows, on the 5×8 slice, whether adding the options group to the equity-only set shows incremental per-regime edge on any of the four targets; provisional Borda composite | `B3-M2` | `B3-M1` |
| 3 | Full-panel confirmation (compute-gated) | Slice-winners re-evaluated on the full 33-symbol panel under the runner pattern; ledger auto-updated; `docs/B3_REPORT.md` records the binary verdict | `B3-M3` | `B3-M2` |
| Gate | At least one (target, augmented) arm clears incremental materiality + significance + deflation in ≥ 2 required regimes | Binary. **Pass** → that (target, feature-set) tuple is a B-cycle artifact (Phase-5 Trigger 1) and a candidate for Project C deployment (and the options ingestor a C1 promotion candidate). **Fail** → "no extractable edge from free market-level options data"; triggers B4 and flags paid per-symbol surfaces as the only remaining alt-data avenue. | — | — |

## Pre-committed gate (verbatim — implemented in B3-M1 as `b3_gate_report`)

The gate function is the source of truth; this prose describes it (METHODOLOGY §2).
For a single (target, augmented-arm) result it returns `gate_passed: bool` computed
as the conjunction of:

1. **Incremental materiality** — the **augmented − equity-only** primary-metric
   delta meets the per-target incremental threshold in the Success Metrics table,
   in ≥ `min_pass` (= 2) of the required regimes `(qe_bull, covid, rate_cycle)`.
   The equity-only arm is the *same target's* M6 GBM, run in the same matrix so the
   delta is paired per regime.
2. **Significance** — the paired stationary-block-bootstrap (21-day blocks) 90% CI
   of that **incremental** delta **excludes 0** in ≥ 1 of the required regimes.
3. **Deflation** — deflated Sharpe (Bailey & López de Prado 2014) > 0, or the
   vol-target skill-z analog > 0, with the deflation N read from
   `quant.ledger.cumulative_trial_count()` at evaluation time. For directional
   targets this applies to the Sharpe arm.

`min_pass`, the required-regime tuple, the per-target incremental thresholds, the
bootstrap block length, and the deflation source are all function arguments with
the defaults pinned above — changing any of them after a result is visible
invalidates the run and requires a new ledger entry (METHODOLOGY §1). `b3_gate_report`
wraps `b1_gate_report`; the *only* semantic change is that the baseline metric is
the equity-only arm's value (an incremental delta), not a naive predictor's.

## Open Questions

- [ ] **Which derived options features, pinned a priori?** M1 commits the raw
      series (VIX, VIX3M, VIX9D, SKEW, equity/index/total P/C) plus a *fixed,
      pre-declared* set of derived transforms (term-structure slope `VIX3M − VIX`,
      short-ratio `VIX9D / VIX`, SKEW level, P/C z-score over a trailing window).
      The transform list is frozen in M1 before M2; adding a transform after seeing
      M2 is a new experiment under a new ledger entry, not a free parameter.
- [ ] **Publication-lag for Cboe settle: 0 or 1 trading day?** Pinned to **lag 0**
      (a date-`t` settle is a day-`t` observation used for a `t+1` decision, exactly
      like the day-`t` OHLCV close). M1 asserts this against the harness's next-bar
      fill convention and the `backtest/CLAUDE.md` purge/embargo invariants; if the
      assertion fails, the conservative `t+1` lag is taken and recorded — resolved
      in M1, never mid-matrix.
- [ ] **Short VIX9D / VIX3M history shrinks the early panel.** VIX9D starts ~2011,
      VIX3M ~2007; the `qe_bull` regime (2009–2015) is partially and the pre-2007
      span fully uncovered for the term-structure features. M1 reports per-feature
      coverage per regime; the NaN-handling policy (drop-row vs forward-impute vs
      regime-restricted evaluation) is pinned in M1 before M2, and any regime with
      insufficient augmented coverage is flagged (not silently dropped from the
      `min_pass` denominator).
- [ ] **Do market-level (panel-constant) features add cross-sectional signal at
      all?** This is the core risk (see Risks). The hypothesis is that the *vol/risk*
      targets (T1, T2) consume a forward-vol *level/regime* signal that does not need
      cross-sectional variation — a market-wide implied-vol spike is itself
      predictive of every symbol's drawdown. M2's per-regime diagnostics test this
      directly; a null result here is the expected, pre-committed negative.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Market-level (panel-constant) features add no cross-sectional edge, mirroring Phase 4A's FRED-macro finding | High | High | This is a *valid, pre-committed* outcome and the cheapest way to learn it. B3's bet is specifically (implied-vol → vol/drawdown targets), a different (data, target) pairing than (macro → return). If it fails, the negative cleanly redirects to B4 / paid per-symbol surfaces and cost ≈ one free ingestor + one slice + one full panel. |
| Look-ahead leakage from mis-pinned options publication lag | Low | Very High | M1 pins lag 0 and **asserts** it against the next-bar fill + `backtest/CLAUDE.md` invariants 3–4; harness self-tests (random→~0 edge, leaky→caught) must stay green. A failed assertion forces the conservative `t+1` lag before any matrix runs. |
| Short VIX9D/VIX3M/SKEW history biases per-regime coverage (esp. qe_bull) | Medium | Medium | M1 reports per-feature × per-regime coverage; NaN policy pinned before M2; under-covered regimes flagged, not silently excluded from `min_pass`. |
| "Looks learnable" because implied vol mechanically tracks the realized-vol *target* (T2) within-window | Medium | High | T2's gate is **incremental skill vs the equity-only EWMA-beating model**, and the bootstrap is OOS per fold under purge/embargo — a within-sample mechanical correlation cannot inflate an OOS incremental delta that survives deflation. Directional targets must additionally clear tradeable Sharpe. |
| Multiple-testing inflation across 4 targets × 2 arms × 3 regimes, stacked on B1's N | High | High | Every arm appends a ledger entry (the `--log-ledger` runner integration); the DSR N (≥ 74 entering B3) grows with the matrix, so the deflation bar rises as B3 tests more — the gate auto-penalizes the cumulative B-cycle search width, not just B3's. |
| Free Cboe CSV endpoints change format / move | Medium | Low | The ingestor validates against a pandera schema and fails loudly on shape drift; the lake snapshot is the durable artifact, so a later endpoint change does not invalidate a completed run. |
| Scope creep into paid per-symbol surfaces mid-project | Medium | Medium | Hard out-of-scope bound, pinned above. Per-symbol surfaces require a separate cost-gated PRD; a surfaced need is a *finding*, appended as a follow-up task, never an in-flight expansion. |

## Sequencing notes

- **B3-M1 ships the ingestor, the catalog records, and the gate function before
  B3-M2 runs the matrix** (METHODOLOGY §2/§4). The thresholds in this PRD are the
  spec; `b3_gate_report` is the contract; the matrix is the consumer. No
  incremental delta is computed against an unwritten gate.
- **B3 reuses the B1 `TARGET_CATALOG` and gate machinery unchanged.** The targets,
  metric callables, deflation analogs (`b1_gate_report`, the skill-z helper), and
  the runner/ledger pattern all exist and are parity-tested. B3's net-new surface
  is `ingest/cboe.py` + the options feature columns + the `b3_gate_report` wrapper
  — deliberately small, because B1 built the evaluation substrate.
- **Conditional skip paths (METHODOLOGY §5, binding):**
  - If **B3 gate passes** on any (target, augmented) arm → that tuple satisfies
    Phase-5 Trigger 1; B4 stays gated unless separately motivated; the Cboe
    ingestor becomes a C1 same-day-pipeline promotion candidate.
  - If **B3 gate fails on all four targets** → draft **B4** (universe shift) per its
    conditional note in `PRIORITIES.yaml` (B4 was already gated on *both* B1 and B3
    surfacing no edge — B1 is done, B3 closes the second condition). The verdict
    additionally records that any further alt-data spend must target **paid
    per-symbol implied-vol surfaces** under a new PRD. A failed B3 cannot be revived
    by alternative justification without a new PRD.
- The gate is calibrated as an **incremental** lift over the equity-only model, not
  over buy-and-hold or a naive baseline — B1 already established the equity-only
  models do not beat their naive baselines, so B3's only meaningful question is the
  *marginal* value of the options data. This keeps B3 commensurable with the M3 /
  B2 OOS-attribution framing (the value of a feature group is its OOS incremental
  lift, METHODOLOGY §14).

---
*Status: DRAFT (2026-06-28) — pre-commitment for Project B3, activated by B1's
NO-GO verdict ([`B1_REPORT.md`](../../docs/B1_REPORT.md) §4). Thresholds in "Success
Metrics" and "Pre-committed gate" are frozen on ratification; changes require a PRD
revision and a new ledger entry, not an in-flight override. Scope is bounded to
**free market-level Cboe data**; paid per-symbol surfaces are a separate gated PRD.
Next: append `B3-M1`/`B3-M2`/`B3-M3` to `PRIORITIES.yaml` and `/plan` turns B3-M1
into an implementation plan.*
