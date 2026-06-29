# C3 — Position Sizing & Risk Management

> **STATUS: RATIFIED (2026-06-29, James Delgado).** Thresholds are now **FROZEN**
> (METHODOLOGY §1) — changing any requires a new PRD + ledger entry, not an edit. All inline
> "(DRAFT / pending ratification)" qualifiers below are superseded by this ratification.
> Two review critiques are carried as build-time notes on the milestone tasks (not gate
> changes): (a) G2b's drawdown-stop test must validate against an **independent hand-computed
> fixture**, not a sibling reference function (C3-M2); (b) the **≤1% reconciliation tolerance
> (G3c) must be confirmed appropriate for `vol_target` sizing** — if integer-share rounding
> on small accounts makes 1% infeasible, that is a finding requiring a new ledger entry, not
> a silent loosening (C3-M1/M3).
>
> **Project**: C (Live execution & deployment infrastructure) — sub-project C3.
> **Roadmap**: [`docs/PROJECT_ROADMAP.md`](../../docs/PROJECT_ROADMAP.md) §4 Project C
> (C3 row), §7 "C3 — Position sizing + risk management" (M1 vol-targeted sizing, M2
> max-position + drawdown stops, M3 live-mode position state).
> **Methodology** (binding): [`docs/METHODOLOGY.md`](../../docs/METHODOLOGY.md) — esp.
> §1 (pre-committed thresholds), §2 (gates-in-code), §4 (**contract before consumer** —
> C3 populates the *existing* C6 registry fields, it does **not** define a parallel
> sizing schema), §6 (drift contracts, both directions), §9 (honest deviation
> declaration / no silent fallback), §10 (materiality before significance), §15/§17
> (tests + the milestone notebooks as the E2E surface), §20 (post-task review).
> **Parent contract**: [`c6-strategy-registry.prd.md`](c6-strategy-registry.prd.md) —
> C3 is the consumer that *fills* the registry's placeholder `sizing_policy` /
> `risk_limits` fields (`src/quant/execution/strategy_registry.py`:
> `SizingPolicy` / `RiskLimits`) and the allocator's `size_strategy` /
> `net_targets` clamp (`scripts/trade_daily.py`). C6-M1 shipped these as deliberate
> placeholders so C3 is built **into** the contract, not retrofitted.
> **Sibling contract**: [`c2-lean-paper.prd.md`](c2-lean-paper.prd.md) — C3 sizes the
> targets the C2 `ExecutionBridge` (`execution/lean_bridge.py`) places and reconciles
> against the Phase-1 `backtest/simulator.py` ground truth (the C2-M3 reconciliation
> machinery + its pinned 1% constant).
> **Existing substrate it composes**: the Phase-1 simulator + metrics
> (`backtest/simulator.py`, `backtest/metrics.py` — Sharpe / max-drawdown / annualised
> vol), the cost model (`docs/concepts/cost-model.md`), the regime axis
> (`backtest/regimes.py`), and the C4 confidence seam (the `confidence_gate` field,
> inert until C4).
> **Backlog tasks**: `C3-PRD` (this) in
> [`docs/PRIORITIES.yaml`](../../docs/PRIORITIES.yaml); milestone tasks `C3-M1`,
> `C3-M2`, `C3-M3` are created when C3 execution is scheduled (see "Sequencing notes");
> consumer `E3-M1` (Project E live-monitoring tiles).

## Problem

C6 stood up the deployment spine — a strategy registry and a daily executor that runs
the *enabled* subset, sizes each strategy, nets per symbol, and places paper orders. But
C6 shipped that spine with a **deliberately trivial placeholder sizing**:
**fully-invested equal-weight** within each strategy's universe, with **permissive,
inert risk limits**. Three things therefore do not exist between "the executor sizes a
position" and "the system sizes positions the way a risk-aware book should":

1. **No risk-aware sizing.** `size_strategy` (`scripts/trade_daily.py`) deploys equal
   notional per symbol regardless of each name's volatility, so a high-vol and a low-vol
   name carry the same dollar exposure — the portfolio's risk is an accident of which
   names happen to be volatile, not a target. `SizingPolicy.method` is a single-value
   `Literal["fully_invested_equal_weight"]`; any other method **raises
   `NotImplementedError`** (the C6 §9 no-silent-fallback guard). There is no
   volatility-targeted, confidence-aware sizing policy.
2. **No binding risk limits.** `RiskLimits` carries `max_position = 1.0` (the full
   long/short unit — the cap never binds) and `max_drawdown_stop = None` (no stop). The
   allocator's clamp in `net_targets` is consequently a no-op. There is no per-symbol
   exposure cap that actually constrains size, and no trailing-drawdown stop that flattens
   a losing strategy. A model that draws down indefinitely keeps trading.
3. **No confidence-consumption seam.** C6 pinned the design rule "confidence enters
   **once**, at sizing" (a high-confidence strategy already deploys a larger position, so
   the allocator only nets + clamps). But the placeholder sizing ignores confidence
   entirely — there is nowhere for C4's calibrated confidence to multiply the size. The
   `confidence_gate` field exists and is inert; the *sizing* side of the seam is unbuilt.

C3 is the consumer that **fills the C6 registry's placeholder fields with real sizing
and risk logic**. It does **not** define a new schema, a new registry, or a new
execution path — it extends the *existing* `SizingPolicy` / `RiskLimits` sub-models and
the *existing* `size_strategy` / `net_targets` allocator (contract-before-consumer,
METHODOLOGY §4: C6-M1 built the contract first precisely so C3 plugs into it). Like C1/C2/C6,
C3 makes **no edge claim** — it transforms positions; it does not seek alpha. Its gate is
a **sizing-correctness + risk-limit-correctness + back-compat** gate, not a Sharpe gate.

## Evidence

From the code and ratified decisions (read at draft time):

| Fact | Source | Implication for C3 |
|---|---|---|
| `SizingPolicy.method` is `Literal["fully_invested_equal_weight"]`; a non-placeholder method raises | `execution/strategy_registry.py:158`; `scripts/trade_daily.py:214` | C3-M1 **extends the Literal** with `vol_target` and fills the raising branch — a deliberate contract change C3 owns (the C6 PRD names it: "C3 *extends the Literal*"). |
| `RiskLimits` defaults are permissive (`max_position = 1.0`, `max_drawdown_stop = None`); the `net_targets` clamp is a no-op | `execution/strategy_registry.py:173-191`; `scripts/trade_daily.py:314-321` | C3-M2 populates real caps + a trailing-drawdown stop; the clamp becomes load-bearing. |
| The allocator's combination rule is "net the (confidence-)sized positions, then clamp"; confidence enters **once, at sizing** | `c6-strategy-registry.prd.md` §Open Questions; `scripts/trade_daily.py:273-322` | C3's sizing is exactly where the C4 confidence scalar multiplies; the allocator is unchanged downstream of sizing (no double-counting). |
| The Phase-1 simulator + `compute_metrics` already compute annualised vol, Sharpe, and max-drawdown | `backtest/metrics.py:32-79` | C3's vol-target and drawdown-stop logic reuse the *existing* risk measures — no new statistics module (DRY; §4). |
| C6-M2 already proved sizing-parity (G2b ≤ 1%) and state round-trip for the **placeholder** sizing | `c6-strategy-registry.prd.md` §Success Metrics; `scripts/trade_daily.py` `sizing_reconciliation_report` | C3 must **preserve** that placeholder behaviour (back-compat) and reconcile its **new** vol-target sizing against the simulator under the same machinery + 1% constant. |
| C3 makes no pre-registered edge claim | this PRD | Like C1/C2/C6, C3 does **not** depend on `A-DSR-GATE`; the gate is sizing-accuracy + risk-correctness + drift + back-compat, not Sharpe/DSR. |
| Roadmap C3 sketch: M1 vol-targeted sizing, M2 max-position + drawdown stops, M3 live-mode position state | `docs/PROJECT_ROADMAP.md` §7 C3 | The three-milestone structure below mirrors the ratified sketch, reframed as the registry fields C3 populates. |

Structural facts that shape the design:

- **C3 is a field-population sub-project, not a schema sub-project.** The schema already
  exists (C6-M1). C3's deliverable is (a) extending two `Literal`/default-bearing
  sub-models, (b) implementing the matching sizing/clamp logic, (c) populating the fields
  for real strategies. The bidirectional drift test that already guards the registry
  (`tests/test_strategy_registry.py`) is *extended*, not replaced.
- **Vol-targeting is the standard risk-normalisation, and the repo already measures
  vol.** Target-volatility sizing scales each position so its *ex-ante* contribution to
  portfolio risk is uniform: `weight ∝ target_vol / realised_vol`. `compute_metrics`
  already annualises return vol; C3 reuses that estimator (and the price history the
  executor already reads) rather than introducing a parallel risk model.
- **Confidence and sizing compose multiplicatively, once.** The pinned C6 rule means
  C3's sized weight is `vol_target_weight × confidence_scalar`, with the confidence
  scalar defaulting to `1.0` until C4 supplies calibrated confidence — so C3's sizing
  seam is **inert with respect to confidence** until C4 lands, exactly mirroring how C6
  made the `confidence_gate` field inert. C3 ships the *seam*; C4 ships the *signal*.

## Users

- **Primary**: the **daily executor** (`scripts/trade_daily.py`) and every **enabled
  registry strategy**. Today they all size fully-invested equal-weight; after C3 a
  strategy's registry entry selects `sizing_policy.method: vol_target` with real
  `risk_limits`, and the executor sizes it accordingly — a registry-entry change, not a
  code edit (the C6 promise extended to real sizing).
- **Secondary**: **C4 (confidence calibration)**. C3's sizing function is the single
  consumption point for C4's calibrated confidence (the "confidence enters once, at
  sizing" rule). C4 supplies the scalar; C3 owns where it multiplies.
- **Tertiary (Project E)**: the **live-monitoring console** (`E3-M1`, which
  `depends_on C3-PRD` as the resolvable proxy for the C3 gate). C3's per-symbol sized
  exposures, the binding cap/stop state, and the realised-vs-target vol are the
  quantities E3's live exposure / per-strategy tiles render.
- **Tertiary (operations)**: whoever runs the daily loop — C3 is what makes the paper
  book's exposure intentional (risk-targeted) and bounded (caps + stops) rather than an
  artefact of equal-weighting.
- **Not for**: confidence *calibration* (C4 — C3 consumes the scalar, does not compute
  it); live-capital trading (paper only — live is a later `broker` flag on the abstracted
  `ExecutionBridge`); cross-strategy capital budgeting (C6 pinned equal-weight `1/N`; a
  smarter budget is a later registry-field swap, not C3); intraday (daily cadence is
  ratified); the console UI (Project E renders; C3 exposes the quantities).

## Hypothesis

We believe that **volatility-targeted, confidence-aware position sizing (replacing the
C6 fully-invested equal-weight placeholder) plus binding per-symbol exposure caps and a
trailing-drawdown stop — all expressed as the values of the existing C6 registry
`sizing_policy` / `risk_limits` fields and the matching allocator logic — makes the paper
book's risk intentional and bounded** — for **the daily executor, the C4 confidence
milestone, and the Project E live-monitoring console** — **closing the "sizing is a
fully-invested equal-weight placeholder, risk limits are inert" gap without making any
edge claim**.

We'll know we're right when (all thresholds pinned in "Success Metrics" before any
compute, METHODOLOGY §1 — **pending ratification, see banner** — and reproduced in the
C3-M1/M2/M3 gate functions, §2):

- **G1 (vol-targeting accuracy)**: over a shared historical replay window (≥ 2 regimes),
  the **realised annualised volatility** of a `vol_target`-sized strategy lands within a
  pinned relative band of its configured `target_vol`, materially closer to target than
  the equal-weight placeholder is — i.e. vol-targeting demonstrably normalises risk.
- **G2 (risk-limit correctness)**: the per-symbol exposure cap and the trailing-drawdown
  stop are **deterministic predicates** — across the replay window, **0** sized positions
  exceed `risk_limits.max_position`, and the drawdown stop flattens the strategy on
  **exactly** the bars where trailing drawdown breaches `max_drawdown_stop` (no early, no
  late, no missed trips).
- **G3 (contract drift + placeholder back-compat + sizing reconciliation)**: the extended
  `SizingPolicy` / `RiskLimits` schema passes the C6 **bidirectional drift test**
  (registry ⇄ code, both directions, 0 unresolved); the **placeholder**
  `fully_invested_equal_weight` path is byte-for-byte preserved (C6's G2b sizing
  reconciliation still passes — **0 regressions**); and the **new** `vol_target` sizing
  reconciles with `backtest/simulator.py`'s capital-based deployment under matched
  assumptions to **≤ 1% relative** (the shared C2-M3 / C6-M2 constant), residual
  decomposed.

If **G1 fails** (vol-targeting does not bring realised vol materially closer to target
than equal-weight), the verdict is **"vol-targeting as specified does not normalise risk
on this universe"** — a valid, pre-committed negative that sends the vol estimator /
look-back back to design under a new ledger entry rather than shipping a sizing policy
that does not do what it claims. If **G3's back-compat axis fails**, C3 has silently
changed the proven C6 placeholder path and must not ship until the regression is found.

## Success Metrics

C3 is **infrastructure** (risk-aware sizing mechanics), so the gate is a **sizing-accuracy
+ risk-correctness + drift + back-compat** gate, **not** a Sharpe gate — DSR/deflation is
undefined here and C3 does **not** depend on `A-DSR-GATE` (mirroring C1/C2/C6). **All
thresholds below are pinned before any compute (METHODOLOGY §1) and reproduced in the
C3-M1/M2/M3 gate functions (§2) — but the specific numbers are DRAFT proposals pending
human ratification (see top banner); ratification freezes them, after which a change
requires a PRD revision + a new ledger entry.**

| # | Claim | Measured on | Statistic | Threshold (pinned — DRAFT, pending ratification) | Reference |
|---|---|---|---|---|---|
| G1a | Vol-targeting hits target risk | a `vol_target` strategy over the replay window (≥ 2 regimes) | \|realised annualised vol − `target_vol`\| / `target_vol` | **≤ 0.25 (within ±25% of target)** | `backtest/metrics.py` annualised vol |
| G1b | Vol-targeting beats equal-weight on risk-normalisation | same window | vol-target's relative vol error vs the equal-weight placeholder's | **vol-target error < equal-weight error** (strictly) | equal-weight baseline (C6 placeholder) |
| G2a | Exposure cap binds, never exceeded | every `(symbol, asof)` in the window | count of sized positions exceeding `risk_limits.max_position` | **exactly 0** | `net_targets` clamp (`trade_daily.py`) |
| G2b | Trailing-drawdown stop trips exactly when it should | the strategy equity path over the window | count of (early ∪ late ∪ missed) stop trips vs the reference trailing-DD predicate | **exactly 0** | `compute_metrics` drawdown; reference predicate in the gate |
| G3a | Schema drift holds, both directions | the extended registry × code | (unresolved refs, schema/enum mismatches) | **(0, 0)** | `tests/test_strategy_registry.py` (§6) |
| G3b | Placeholder path unchanged (back-compat) | the C6 placeholder universe over the window | C6 G2b sizing-reconciliation regressions | **exactly 0** | C6 `sizing_reconciliation_report` |
| G3c | Vol-target sizing ⇄ simulator capital sizing | the vol-target universe over the window | relative per-symbol notional delta, matched assumptions | **≤ 1.0% relative**, residual decomposed | C2-M3 / C6-M2 1% constant (drift-locked) |

Notes on the metric choices:

- **G1 and G2 are the merge-blocking sizing/risk gates; G3 is the contract-integrity
  gate.** G3b (back-compat) is non-negotiable: C3 extends a *proven* path and must not
  regress it.
- **G1's ±25% band is a materiality bar, DRAFT pending ratification.** Realised vol is a
  noisy ex-post estimate of an ex-ante target; ±25% is proposed as "the policy is
  demonstrably targeting risk" while leaving room for estimation noise. The companion
  G1b (strictly beats equal-weight) is the qualitative claim that survives even if the
  absolute band is later re-tuned. **Rationale for the proposal**: a tighter band (e.g.
  ±10%) risks failing on estimation noise alone; a looser band (±50%) would pass a policy
  that barely normalises risk. The exact number is the central ratification decision.
- **G3c reuses the C2-M3 / C6-M2 1% reconciliation constant under its existing drift
  contract** (METHODOLOGY §6) — C3 invents no new tolerance; it reconciles a *new* sizing
  rule against the same simulator ground truth the placeholder reconciled against.
- **Materiality before significance (§10).** G2a/G2b/G3a/G3b are deterministic predicates
  and G1/G3c are deterministic replays; there is no statistical-significance axis. The
  bars are pure materiality thresholds pinned in code (pending ratification).

## Scope

**MVP** — the three milestones below, executed in order, reusing the C6 registry +
allocator, the C2 `ExecutionBridge`, the Phase-1 simulator + metrics, the cost model, and
the regime axis. **No new model, no new data source, no new universe, no confidence
*calibration* (C4), no cross-strategy budgeting change, no intraday, no live capital** —
C3 adds only **real sizing logic**, **binding risk limits**, and the **confidence-
consumption seam**, all as values of the *existing* C6 registry fields.

1. **C3-M1 — Volatility-targeted sizing policy.** Extend `SizingPolicy.method` with
   `vol_target` and its parameters (`target_vol`, vol look-back, realised-vol floor/cap),
   and implement the matching branch in `scripts/trade_daily.py::size_strategy` — sizing
   each position `weight ∝ target_vol / realised_vol`, reusing `backtest/metrics.py`'s
   vol estimator. Ships the **G1 gate function** (vol-targeting accuracy + beats
   equal-weight). Extends the C6 bidirectional drift test for the new enum + params.
   Tests land with the change (§15); a cross-module E2E notebook exercises registry →
   executor → vol-target sizing → simulator reconciliation on real fixtures and renders
   the G1 verdict (§17). **Must not touch walk-forward split logic** (`backtest/CLAUDE.md`).
2. **C3-M2 — Max-position caps + trailing-drawdown stops.** Populate `RiskLimits` with a
   binding per-symbol `max_position` (real share/notional cap, not the unit no-op) and a
   `max_drawdown_stop` (trailing-drawdown stop that flattens the strategy when its
   trailing DD breaches the limit), and make the `net_targets` clamp + an executor-level
   stop check load-bearing. Ships the **G2 gate function** (cap never exceeded; stop trips
   exactly on breach). Tests + E2E notebook; reconciles drawdown against
   `compute_metrics`.
3. **C3-M3 — Confidence-consumption seam + real-sizing state reconciliation.** Wire the
   single confidence-multiplication point into the sizing function (`sized_weight =
   vol_target_weight × confidence_scalar`, scalar defaulting to `1.0` — **inert until
   C4**), and verify the executor's persisted position state round-trips and reconciles
   with the broker's reported holdings **under real vol-target sizing + binding caps**
   (the roadmap C3-M3 "live-mode position state" outcome, now exercised under real sizing
   rather than the C6 placeholder). Ships the **G3 gate function** (drift + back-compat +
   vol-target reconciliation). Tests + E2E notebook.

**Out of scope**

- **Confidence calibration** — **C4**. C3 ships the *seam* (the `× confidence_scalar`
  multiply, inert at `1.0`); C4 supplies the calibrated scalar. No strategy's size is
  shaped by confidence until C4 lands.
- **Cross-strategy capital budgeting** — C6 pinned equal-weight `1/N`; changing it is a
  later registry-field swap, not C3. C3 sizes *within* a strategy's budget slice.
- **A new sizing schema / registry / execution path** — C3 populates the *existing* C6
  fields and extends the *existing* allocator. No parallel structures (§4).
- **Live capital** — paper only; live is a later `broker` config flag.
- **New models / data / universe / intraday** — C3 sizes the *existing* strategies on the
  *existing* feeds.
- **Replacing the Phase-1 simulator** — it remains the reconciliation ground truth
  (G3c); C3 reconciles against it, it does not reimplement it.

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->

| # | Milestone | Outcome | PRIORITIES task | Depends on |
|---|---|---|---|---|
| 1 | Vol-targeted sizing | `SizingPolicy.method` gains `vol_target` + params; `size_strategy` implements it; G1 (vol-targeting accuracy + beats equal-weight) passes; drift test extended | `C3-M1` | `C3-PRD`, `C6-M1` |
| 2 | Caps + drawdown stops | `RiskLimits` populated with a binding `max_position` + trailing `max_drawdown_stop`; the clamp + stop are load-bearing; G2 (0 cap exceedances, exact stop trips) passes | `C3-M2` | `C3-M1` |
| 3 | Confidence seam + real-sizing state | the single confidence-multiply seam wired (inert until C4); persisted state reconciles with broker holdings under real sizing; G3 (drift + back-compat + vol-target reconciliation ≤ 1%) passes | `C3-M3` | `C3-M2`, `C6-M2` |
| Gate | Vol-targeting hits target risk and beats equal-weight (G1) AND caps/stops are correct (G2) AND the contract drift + placeholder back-compat + vol-target reconciliation hold (G3) | Binary. **Pass** → registry-driven, risk-aware, confidence-ready sizing is in code; C4 + E3 unblocked on the sizing side. **Fail** → the verdict (which axis failed) sends that milestone back under a new ledger entry; the placeholder sizing remains the deployed default. | — | — |

## Pre-committed gate (verbatim — implemented across C3-M1/M2/M3 as `c3_sizing_gate`)

> **DRAFT — the constants below are proposals pending human ratification (top banner,
> METHODOLOGY §1). On ratification they freeze; a change thereafter requires a PRD
> revision + a new ledger entry, not an in-flight override.**

The gate functions are the source of truth; this prose describes them (METHODOLOGY §2).
C3's gate is the conjunction of:

1. **Vol-targeting accuracy (G1, C3-M1)** — over the shared replay window (≥ 2 regimes),
   `abs(realised_annual_vol − target_vol) / target_vol <= VOL_TARGET_TOLERANCE`
   (DRAFT default **0.25**), **and** the vol-target strategy's relative vol error is
   strictly less than the equal-weight placeholder's on the same window.
2. **Risk-limit correctness (G2, C3-M2)** — across the window: the count of sized
   positions whose magnitude exceeds `risk_limits.max_position` is **0**
   (`G2A_MAX_CAP_BREACHES = 0`); and the trailing-drawdown stop trips on **exactly** the
   set of bars where trailing drawdown breaches `risk_limits.max_drawdown_stop` — the
   symmetric difference between actual and reference stop-trip bars is **0**
   (`G2B_MAX_STOP_TRIP_ERRORS = 0`).
3. **Contract drift + back-compat + reconciliation (G3, C3-M3)** — the extended
   `SizingPolicy`/`RiskLimits` schema yields **0** unresolved refs / schema mismatches in
   the C6 bidirectional drift test (both directions, §6); the `fully_invested_equal_weight`
   placeholder path produces **0** C6-G2b reconciliation regressions
   (`G3B_MAX_BACKCOMPAT_REGRESSIONS = 0`); and the `vol_target` per-symbol notional
   reconciles with `backtest/simulator.py`'s capital deployment to
   `<= G3C_MAX_RELATIVE_DELTA` (the **0.01 / 1%** constant shared with C2-M3 / C6-M2 under
   its drift contract), residual decomposed into named sources.

`VOL_TARGET_TOLERANCE`, `G2A_MAX_CAP_BREACHES`, `G2B_MAX_STOP_TRIP_ERRORS`,
`G3B_MAX_BACKCOMPAT_REGRESSIONS`, the confidence-once-at-sizing rule, the
`weight ∝ target_vol / realised_vol` sizing formula, and the reuse of the 1% G3c constant
are all pinned constants (DRAFT, pending ratification). `G3C_MAX_RELATIVE_DELTA` is the
*existing* C2-M3 / C6-M2 constant — C3 reuses it under the existing drift lock and invents
no new tolerance.

## Open Questions

- [ ] **Vol estimator + look-back.** Pinned proposal (DRAFT): realised annualised vol
      from daily log returns over a trailing **63-bar (~3-month)** window, reusing
      `backtest/metrics.py`'s annualisation (`sqrt(252)`), with a realised-vol **floor**
      to avoid divide-by-near-zero blowing up size. EWMA vs simple window, and the exact
      look-back / floor / cap, are the first ratification decisions — frozen in C3-M1
      before any sizing is computed.
- [ ] **`target_vol` default + per-strategy override.** Pinned proposal (DRAFT): a
      per-strategy `target_vol` field on `SizingPolicy` (e.g. **10% annualised** default),
      so each registry entry sets its own risk target. The default value is a ratification
      decision.
- [ ] **`max_position` units.** The C6 `max_position` is a per-symbol *target-position
      magnitude* cap (unit = the long/short unit). C3 must decide whether the real cap is
      expressed as a **fraction of strategy capital** (notional cap) or a position-unit
      cap, and reconcile with the existing `net_targets` clamp semantics. Pinned in C3-M2
      before the clamp is made load-bearing.
- [ ] **Trailing-drawdown stop semantics.** Proposed (DRAFT): a trailing peak-to-current
      drawdown on the *strategy's* equity path; on breach the strategy flattens (all
      targets → 0) and stays flat until a pinned **re-entry rule** (e.g. flat for the rest
      of the run, or re-arm after recovery to a fraction of peak). The re-entry rule and
      whether the stop is per-strategy or per-symbol are pinned in C3-M2.
- [ ] **Confidence-scalar mapping (the C4 seam).** C3 fixes *where* confidence multiplies
      (`sized_weight = vol_target_weight × confidence_scalar`, scalar ∈ (0, 1], default
      `1.0`). The *mapping* from C4's calibrated confidence to the scalar is a C4
      decision; C3-M3 ships the inert seam and asserts it is a no-op at scalar `1.0`
      (back-compat with the C6 placeholder when C4 is absent).
- [ ] **Reconciliation window selection (G1/G3c).** The replay window spans ≥ 2 regimes
      (reusing `backtest/regimes.py`), pinned in C3-M1 before any vol/reconciliation is
      measured, so accuracy is not measured against a hand-picked favourable span (§1/§10).

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| C3 silently regresses the proven C6 placeholder sizing path (G3b fails) | Low | **Very High** | G3b is a 0-regression back-compat gate against C6's `sizing_reconciliation_report`; the placeholder branch is preserved and asserted unchanged before the new branch ships. |
| Vol-targeting does not normalise risk on this universe (G1 fails) | Medium | Medium | A valid, pre-committed negative (METHODOLOGY §5): the estimator/look-back goes back to design under a new ledger entry; the placeholder remains the deployed default. The negative is documented, not hidden. |
| Drawdown stop trips incorrectly (early/late/missed) and flattens a healthy strategy or fails to flatten a sick one (G2b fails) | Medium | High | G2b is a deterministic exact-trip predicate against a reference trailing-DD computation (`compute_metrics`); the symmetric difference must be 0. The stop is tested on crafted equity paths before any live use. |
| Vol-target sizing cannot be reconciled with the simulator (G3c fails) | Medium | High | G3c reuses the C2-M3 / C6-M2 reconciliation harness + its 1% constant under the existing drift lock; a failure surfaces a real sizing/cost mismatch — the milestone's value, not a defect. |
| Confidence double-counted (weighted at sizing *and* combination) | Low | Medium | Pinned rule carried from C6: confidence enters **once**, at sizing; the allocator only nets + clamps. C3-M3 asserts the seam is a no-op at scalar `1.0` and that `net_targets` re-applies nothing. |
| Divide-by-near-zero realised vol blows up position size | Medium | High | A pinned realised-vol **floor** (and an optional size cap via `max_position`) bounds the `target_vol / realised_vol` ratio; tested on low-vol fixtures. |
| Scope creep into C4 (computing confidence) or into cross-strategy budgeting | Medium | Medium | Out-of-scope is explicit; C3 ships the inert confidence *seam* and sizes *within* the C6-pinned `1/N` budget. |
| C3 ships but no real (non-placeholder) strategy is ever deployed | Medium | Low | Independent by design (the C6 rationale): risk-aware sizing accrues to *any* future deployable strategy and to the placeholder's own risk-bounding, regardless of any B verdict. |

## Sequencing notes

- **C3 populates the C6 contract; it does not precede or duplicate it** (METHODOLOGY §4).
  C6-M1 shipped `SizingPolicy` / `RiskLimits` as placeholders *expressly so* C3 fills
  them — this PRD is the consumer half of that contract-before-consumer split. C3 extends
  the `Literal` and defaults; it defines no parallel schema.
- **C3-M1/M2/M3 ship their gate constants pinned before any sizing/risk is measured**
  (§1/§2) — and those constants are **DRAFT pending ratification** (top banner) until the
  PRD is ratified. G3c reuses the existing 1% constant under its drift lock; no new
  tolerance is invented (§6).
- **C3 must not touch walk-forward split logic.** Sizing/risk logic consumes forecasts,
  prices, and equity paths; it leaves `walkforward.py` / `harness.py` purge+embargo
  invariants untouched (`backtest/CLAUDE.md`). The harness self-tests stay green.
- **No new module convention.** C3 edits existing files (`execution/strategy_registry.py`,
  `scripts/trade_daily.py`, `tests/test_strategy_registry.py`) and adds milestone E2E
  notebooks — all convention-*following*. No new top-level directory or `src/quant/`
  package.
- **C3 does NOT depend on `A-DSR-GATE`** (no Sharpe/edge claim → no deflation), mirroring
  C1/C2/C6. It depends only on `C2-M2` (the bridge) and `C6-M1` (the registry contract),
  already encoded in `PRIORITIES.yaml`; C3-M3 additionally on `C6-M2` (the executor +
  state round-trip it extends).
- **Ledger discipline.** C3 is infrastructure, not a research trial — it makes no
  pre-registered edge claim, so it contributes **no** research trials to the deflation
  `N`. A C3 milestone run may record an **audit-only** ledger entry (`n_comparisons = 0`)
  per the A-LEDGER-RUNNERS pattern (mirrors C1/C2/C6).
- **Milestone-task creation (deferred).** Only `C3-PRD` exists in `PRIORITIES.yaml` today;
  the `C3-M1`/`C3-M2`/`C3-M3` backlog tasks are created when C3 execution is scheduled
  (the C3-PRD note already says "re-prioritize once C6-M1 lands" — C6-M1 has landed). Per
  the AGENT_OPERATION Step 7 corollary, when those tasks are created their ids **must** be
  added to **`C-CLOSE.depends_on`** (which currently lists `C3-PRD` as the resolvable
  proxy) and to **`E3-M1.depends_on`** (whose note explicitly mandates this: "when C3
  sizing milestones (C3-M*) are created their ids MUST be added"). This PRD does not
  pre-create them, keeping the C3-PRD task's footprint to its single deliverable.
- **Downstream unblock.** `C3-PRD` is the resolvable proxy `E3-M1` and `C-CLOSE` depend
  on for the C3 gate; the real unblock of the E3 sizing tiles follows the C3 milestone
  gates passing.

---
*Status: RATIFIED (2026-06-29, James Delgado) — pre-commitment for Project C3. The
thresholds in "Success Metrics" and "Pre-committed gate" (G1 vol-target tolerance ±25% +
strictly beats equal-weight, G2 0 cap breaches / 0 stop-trip errors, G3 0 drift / 0
back-compat regressions / ≤ 1% vol-target reconciliation) and the pinned design rules
(vol-target sizing formula, confidence-once-at-sizing seam, trailing-drawdown stop,
equal-weight capital budget unchanged from C6) are now FROZEN (METHODOLOGY §1) — changes
require a new PRD + ledger entry, not an in-flight override. Two review critiques are carried
as build-time notes on the milestone tasks (drawdown-stop test independence; ≤1% tolerance
fit for vol-target). Milestone tasks C3-M1/M2/M3 created 2026-06-29. Next: `/plan` turns
C3-M1 into an implementation plan.*
