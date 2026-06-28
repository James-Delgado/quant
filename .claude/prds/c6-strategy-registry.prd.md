# C6 — Strategy Registry & Daily Executor

> **Project**: C (Live execution & deployment infrastructure) — sub-project C6.
> **Roadmap**: [`docs/PROJECT_ROADMAP.md`](../../docs/PROJECT_ROADMAP.md) §4 Project C
> (C6 row), §8 ratified decision 8 (strategy registry & daily executor).
> **Methodology** (binding): [`docs/METHODOLOGY.md`](../../docs/METHODOLOGY.md) — esp.
> §1 (pre-committed thresholds), §2 (gates-in-code), §4 (**contract before consumer** —
> the core rationale for C6 preceding C3/C4), §6 (drift contracts, both directions),
> §9 (honest deviation declaration), §15/§17 (tests + the runner as the E2E surface).
> **Parent contracts**: [`c2-lean-paper.prd.md`](c2-lean-paper.prd.md) — C6 consumes the
> settled `ExecutionBridge` boundary (`src/quant/execution/lean_bridge.py`:
> `daily_signal` / `AlpacaPaperBridge` / `TargetOrder` / position-state persistence) and
> the C2-M3 reconciliation/loop primitives (`scripts/reconcile_paper_backtest.py`:
> `run_paper_loop`). [`c1-live-data.prd.md`](c1-live-data.prd.md) — C6 consumes the
> same-day reader + freshness monitor (`scripts/monitor_freshness.py`).
> **Existing substrate it composes**: the model classes (`src/quant/models/`:
> `ARIMABaseline`, `GBMModel`, `BuyAndHoldBaseline`), the **feature catalog**
> (`src/quant/features/catalog.{py,yaml}`), the **target catalog** (`TARGET_CATALOG` in
> `src/quant/features/targets.py`), the daily ingest flow (`src/quant/flows/daily.py`).
> **Backlog tasks**: `C6-PRD`, `C6-M1`, `C6-M2` in
> [`docs/PRIORITIES.yaml`](../../docs/PRIORITIES.yaml); consumer
> `E-STRATEGIES-PANEL` (Project E console).

## Problem

C1 made "today's data" real and parity-proven; C2 made "today's order" real and
reconciled against the backtest. But the execution path C2 built runs **exactly one
hardcoded strategy** — the ARIMA(1,0,0) placeholder is baked directly into
`lean_bridge.daily_signal` (`forecast = ARIMABaseline().fit(...).predict_one_step()`).
There is no way to register *more than one* deployable strategy, to choose among them,
to size them by confidence, or to run them on an unattended schedule. Three things do
not exist between "the bridge can place an order" and "the system trades a portfolio of
vetted models every day":

1. **No strategy registry.** A deployable strategy is not just a model — it is a full
   pipeline spec (`model + feature_set + target + universe + decision_rule +
   sizing_policy + confidence_gate + risk_limits + enabled + provenance`). The repo has
   registries for *features* (`features/catalog.yaml`) and *targets* (`TARGET_CATALOG`)
   but **none for deployable strategies**. Swapping or adding a strategy today means
   editing `daily_signal` source — there is no contract a new model plugs into, and
   nothing stops an un-vetted model from being "deployed."
2. **No multi-strategy allocator.** Even with several models, nothing turns a set of
   per-strategy predictions into one portfolio of target positions: no capital
   budgeting across strategies, no confidence-aware sizing, no per-symbol netting when
   two strategies want the same name, no risk-cap clamp. C3 (sizing) and C4 (confidence)
   are currently framed for a *single* strategy; the layer that *composes* them across a
   portfolio is unowned.
3. **No daily orchestrator.** The pieces — ingest (`flows/daily.py`), freshness gate
   (`monitor_freshness.py`), signal (`daily_signal`), order (`AlpacaPaperBridge`),
   state persistence — exist as separate callables. Nothing chains them into one
   idempotent, cron-safe "trade today" entrypoint. The ingest flow is scheduled
   (`daily_ingest.serve(cron="30 22 * * 1-5")`) but stops at data; it never reaches an
   order.

C6 is **infrastructure**, like C1 and C2: it does **not** seek edge, calibrate
confidence (C4), or compute risk-aware sizing (C3). It stands up the **registry +
allocator + daily executor spine** with the deliberately trivial ARIMA placeholder, so
the multi-strategy machinery is forced into existence and proven *before* there is a
second strategy to run. It is the layer that makes "deploy a model" a **registry entry**,
not a code edit — the explicit ROADMAP §3 promise ("deploy a model to paper is a wiring
change, not a new project").

## Evidence

From the code and ratified decisions (read at draft time):

| Fact | Source | Implication for C6 |
|---|---|---|
| The strategy is hardcoded: `daily_signal` calls `ARIMABaseline()` inline | `execution/lean_bridge.py:187` | "Run a different model" = source edit. A registry is the missing indirection. |
| No `Strategy`/`Allocator`/registry abstraction exists anywhere in `src/` | grep of `src/quant/` (only a comment in `targets.py` referencing C3/C4) | C6 introduces the first deployment-side registry; nothing to refactor away. |
| Registries-as-contracts are the established repo pattern | `features/catalog.{py,yaml}` + `tests/test_catalog.py`; `TARGET_CATALOG` (`targets.py`) | C6-M1 mirrors that pattern: YAML registry + typed loader + bidirectional drift test (§6). |
| The `ExecutionBridge` is already model-outside-the-engine + broker-agnostic | `execution/lean_bridge.py` (C2-M2) | The allocator emits `TargetOrder`s into the *settled* bridge; C6 adds no broker code. |
| The C2 placeholder trades a fixed 1 share, not capital-sized | `lean_bridge.PLACEHOLDER_QTY`; open `C2-M2-SIZING-PARITY` | C6-M2's fully-invested equal-weight sizing closes this gap (sizing-parity gate). |
| The daily ingest flow stops at data; nothing chains to an order | `flows/daily.py` (`daily_ingest.serve(cron=…)`) | C6-M2 is the missing `ingest → … → order → persist` orchestrator. |
| C3/C4 are framed single-strategy; their PRDs are not yet drafted | `PRIORITIES.yaml` (C3-PRD/C4-PRD now `depends_on: C6-M1`) | The registry schema is the contract C3 (`sizing_policy`/`risk_limits`) and C4 (`confidence_gate`) populate — contract-before-consumer (§4). |
| C6 makes no pre-registered edge claim | this PRD | Like C1/C2, C6 does **not** depend on `A-DSR-GATE`; the gate is contract + drift + parity + liveness, not Sharpe/DSR. |

Structural facts that shape the design:

- **A strategy is the deployment-side analog of Project B's research tuple.** ROADMAP
  §4 Project B describes research as "a portfolio of (target, model, universe)". The
  strategy registry is the **promoted-to-live subset** of exactly those tuples — the
  ones that cleared a gate and were enabled. It *references* the existing feature and
  target catalogs rather than duplicating them (DRY; §4).
- **Confidence enters once, at sizing — not at the combination step.** C4 produces
  calibrated confidence per strategy; C3 consumes it to size. A high-confidence strategy
  therefore *already* deploys a larger position, so the allocator needs no separate
  confidence weighting — it nets the (already confidence-shaped) sized positions. Equal-
  weight is only the coarse *capital-budget* knob across strategies.
- **The placeholder's job is to drive the plumbing, not to make money.** Its P&L is
  uninteresting by design (ARIMA on a few names). C6 deploys it at fully-invested
  equal-weight purely to exercise the sizing/netting/order path end-to-end on paper.

## Users

- **Primary**: the **C3 (sizing/risk) and C4 (confidence) milestones**. Both now
  `depends_on C6-M1`: their `sizing_policy`/`risk_limits` (C3) and `confidence_gate`
  (C4) are *fields the registry holds*, so they are built **into** the C6 contract
  rather than standalone and retrofitted. C6-M1's schema is the contract they consume.
- **Secondary**: every **future deployable B-model**. When a B sub-project clears its
  gate, it ships as a *new registry entry* (`enabled: true`, `provenance: ledger-…`) —
  no execution code changes. C6 is what makes that true.
- **Tertiary (operations)**: whoever runs the daily loop. After C6-M2, `trade_daily.py`
  on a cron *is* the live paper loop; its position state and the registry are the
  operator's source of truth.
- **Tertiary (Project E)**: the console. The registry's serializable view-model feeds
  the strategy-portfolio panel (in-use + idle strategies) — `E-STRATEGIES-PANEL`.
- **Not for**: live-capital trading (paper only — live is a later config flag on the
  already-abstracted `ExecutionBridge`); position sizing/risk *logic* (C3 — C6 ships a
  placeholder fully-invested sizing); confidence *calibration* (C4 — the
  `confidence_gate` field is inert until C4 populates it); intraday (daily cadence is
  ratified); the monitoring dashboard (Project E3).

## Hypothesis

We believe a **strategy registry** (the full pipeline spec per strategy, referencing the
existing feature/target catalogs, under a bidirectional drift contract) **plus a daily
cron executor** (that runs the *enabled* subset, sizes each, nets per symbol, and places
paper orders) **makes execution multi-strategy and registry-driven** — so deploying a
model becomes a registry entry, not a code edit — **for the C3/C4 milestones and every
future deployable B-model**, **closing the "execution is a single hardcoded placeholder"
gap without making any edge claim**.

We'll know we're right when (all thresholds pinned in "Success Metrics" before any
compute, METHODOLOGY §1, and reproduced in the C6-M1/M2 gate functions, §2):

- **G1 (registry contract / drift)**: the registry schema is enforced by a drift test
  that resolves every strategy's `model_ref`/`feature_set_ref`/`target_ref` against the
  existing catalogs **in both directions** and enforces the **provenance gate** (no
  `enabled: true` without `provenance`, placeholder exempt) — **0 unresolved references,
  0 provenance violations**.
- **G2 (allocator correctness + single-strategy parity)**: the daily allocator is
  deterministic given a fixed registry + as-of, and on the **single enabled placeholder**
  reproduces the existing C2 execution path's per-symbol target **exactly** (the
  allocator's target == `derive_target_position` of the same forecast) — **0 mismatches**
  — while the fully-invested equal-weight sizing reconciles with the Phase-1 simulator's
  capital-based per-symbol allocation to within a **pinned tolerance** (closing
  `C2-M2-SIZING-PARITY`).
- **G3 (daily-loop liveness)**: `trade_daily.py` completes **≥ 5 consecutive clean daily
  cycles** (`ingest → freshness gate → signal → size → net → order → persist`) with
  **0 pipeline errors**, position state that round-trips across runs, and a **non-zero
  exit** on any failure (cron-detectable).

If **G2 fails** (the allocator does not reproduce the C2 path's decision on a single
strategy, or the placeholder sizing cannot be reconciled with the backtest), the verdict
is **"the multi-strategy layer is not a faithful generalization of the proven C2 path"** —
a valid, pre-committed negative that **blocks C3/C4** until reconciled. C6 does not ship
an allocator whose single-strategy behavior silently disagrees with the execution path
C2 already proved.

## Success Metrics

C6 is **infrastructure**, so the gate is a **contract + drift + parity + liveness** gate,
not a Sharpe gate — DSR/deflation is undefined here and C6 does **not** depend on
`A-DSR-GATE` (mirroring C1/C2). **All thresholds are pinned here before any compute
(METHODOLOGY §1) and are reproduced in the C6-M1/M2 gate functions (§2).**

| # | Claim | Measured on | Statistic | Threshold (pinned) | Reference |
|---|---|---|---|---|---|
| G1 | Registry refs resolve + provenance gate holds, both directions | every registered strategy × the feature/target/model catalogs | (unresolved refs, provenance violations) | **(0, 0)** | `tests/test_catalog.py` drift pattern (§6) |
| G2a | Single-strategy allocator target == C2 path target | every `(symbol, asof)` for the lone placeholder over a replay window | count of material target mismatches | **exactly 0** | C2 G1 parity analog (`derive_target_position`) |
| G2b | Fully-invested equal-weight sizing ⇄ backtest capital sizing | the placeholder universe over the replay window | relative per-symbol notional delta under matched assumptions | **≤ 1.0% relative**, residual decomposed | `backtest/simulator.py` sizing; closes `C2-M2-SIZING-PARITY` |
| G3 | Daily executor runs end-to-end with persisted state | a live paper run | (consecutive clean cycles, state round-trip OK?, non-zero exit on failure?) | **(≥ 5, True, True)**, 0 pipeline errors | C2-M3 G3 pattern (`run_paper_loop`) |

Notes on the metric choices:

- **G1 and G2 are the merge-blocking gates** (registry integrity + faithful
  generalization of the C2 path). **G3 is the operational gate** — it proves the loop
  *runs*, not that it *profits*.
- **G2b's 1% tolerance reuses the C2-M3 reconciliation machinery and its pinned
  constant** — fully-invested equal-weight sizing is the first *real* (capital-based)
  sizing the execution path emits, so reconciling it against the simulator's capital
  allocation is exactly the `C2-M2-SIZING-PARITY` close-out.
- **Materiality before significance (§10).** As with C1/C2, there is no statistical
  significance axis — G1/G2a/G3 are deterministic predicates and G2b is a deterministic
  replay. The bars are pure materiality thresholds pinned in code.

## Scope

**MVP** — the two milestones below, executed in order, reusing the existing models, the
C1 reader + freshness monitor, the C2 `ExecutionBridge` + reconciliation primitives, and
the existing feature/target catalogs. **No new model, no new data source, no new
universe, no real sizing/confidence logic, no intraday, no live capital** — C6 adds only
the **registry contract**, the **multi-strategy allocator**, and the **daily executor**.

1. **C6-M1 — Strategy registry schema + loader + drift test.** A YAML registry
   (`strategy_registry.yaml`) + a typed loader + a bidirectional drift test, mirroring
   `features/catalog.{py,yaml}`. Each entry is the full pipeline spec; the loader
   resolves refs against the existing catalogs and enforces the provenance gate. Exposes
   a serializable **view-model** for the Project E console. Ships the **G1 gate function**
   (0 unresolved / 0 provenance violations) and seeds **one** entry: the ARIMA
   placeholder (`enabled`, `provenance: placeholder`). This is the **contract** C3/C4 +
   the executor + the console consume (METHODOLOGY §4 — before any of them).
2. **C6-M2 — Daily cron executor.** `scripts/trade_daily.py`: one idempotent entrypoint
   chaining `ingest → freshness gate → for each enabled strategy: predict → size
   (fully-invested equal-weight placeholder) → produce signed targets → net per symbol →
   clamp → place_target via the bridge → persist state`. Ships the **G2 gate** (single-
   strategy parity + sizing reconciliation) and the **G3 liveness** runbook + loop. Exits
   non-zero on any failure; re-runnable same-day (state round-trip). **Must not touch
   walk-forward split logic** (`backtest/CLAUDE.md`).

**Out of scope**

- **Real position sizing / risk caps / drawdown stops** — **C3**. C6-M2 ships a
  placeholder fully-invested equal-weight sizing; the `sizing_policy`/`risk_limits`
  registry fields exist but carry the placeholder until C3 populates them.
- **Confidence calibration / the confidence gate** — **C4**. The `confidence_gate` field
  exists but is **inert** (degenerate always-pass) until C4 supplies calibrated
  confidence. No strategy is gated *out* on confidence in C6.
- **Live capital** — paper only; live is a later `broker` config flag on the already-
  abstracted `ExecutionBridge`.
- **New models / data / universe / intraday** — C6 runs the *existing* ARIMA on the
  *existing* daily feeds. A second strategy is registered later, not in C6.
- **The console panel UI** — `E-STRATEGIES-PANEL` (Project E). C6-M1 exposes the
  view-model; rendering it is an E task.
- **Confidence/track-record-weighted capital budgets** — MVP pins **equal-weight (1/N)**;
  smarter budgeting is a later registry-field swap.
- **Replacing the C2/C2-M3 path** — C6 *composes* the bridge and reconciliation
  primitives; it does not reimplement them.

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->

| # | Milestone | Outcome | PRIORITIES task | Depends on |
|---|---|---|---|---|
| 1 | Strategy registry contract | `strategy_registry.{py,yaml}` + loader + bidirectional drift test; G1 (0 unresolved refs, 0 provenance violations) passes; one seeded placeholder entry; serializable view-model for the console | `C6-M1` | `C6-PRD` |
| 2 | Daily cron executor | `scripts/trade_daily.py` runs the enabled subset end-to-end; G2 (single-strategy parity + sizing reconciliation ≤ 1%) + G3 (≥ 5 clean cycles, state round-trip, non-zero exit on failure) pass | `C6-M2` | `C6-M1`, `C1-M3`, `C2-M2` |
| Gate | The registry is a drift-checked contract (G1) AND the allocator reproduces the C2 path on a single strategy and reconciles its sizing (G2) AND runs daily end-to-end (G3) | Binary. **Pass** → multi-strategy, registry-driven paper execution is in code; C3/C4 unblocked. **Fail (G2)** → the multi-strategy layer is not a faithful generalization of the C2 path; C3/C4 stay blocked until reconciled. | — | — |

## Pre-committed gate (verbatim — implemented across C6-M1 and C6-M2)

The gate functions are the source of truth; this prose describes them (METHODOLOGY §2).
C6's gate is the conjunction of:

1. **Registry contract / drift (G1, C6-M1)** — the loader's drift report lists
   `unresolved` references (a strategy ref with no matching model/feature/target catalog
   entry) and `provenance_violations` (`enabled: true` with no `provenance`, placeholder
   exempt) in **both directions**; both lists must be **empty**.
2. **Single-strategy parity + sizing reconciliation (G2, C6-M2)** — with exactly the
   seeded placeholder enabled, for every `(symbol, asof)` in the replay window the
   allocator's per-symbol target position equals `lean_bridge.derive_target_position` of
   the same forecast (**0 mismatches**); and the fully-invested equal-weight per-symbol
   notional reconciles with `backtest/simulator.py`'s capital-based allocation to **≤ 1%
   relative**, residual decomposed (reusing the C2-M3 reconciliation harness + its pinned
   constant).
3. **Daily-loop liveness (G3, C6-M2)** — a real paper run completes **≥ 5** consecutive
   clean cycles with **0** pipeline errors, position state that round-trips across runs,
   and a **non-zero process exit** on any cycle failure.

The 0-mismatch G2a count, the 1% G2b tolerance (shared with C2-M3 under its drift
contract, §6), the ≥5-cycle G3 count, the equal-weight capital budget, the net-per-
symbol-then-clamp combination rule, and the provenance-gate rule are all pinned
constants. Changing any after a result is visible invalidates the run and requires a PRD
revision plus a new ledger entry (METHODOLOGY §1).

## Open Questions

- [ ] **Capital budget across strategies.** Pinned for MVP: **equal-weight (1/N)** per
      enabled strategy, in one config field. Confidence- or track-record-weighted
      budgeting is a deliberate later swap, not C6.
- [ ] **Same-symbol combination rule.** Pinned: **net the signed, (confidence-)sized
      positions per symbol, then clamp to the per-symbol risk cap** (the cap is a C3
      field; MVP uses a permissive default). Confidence is *not* re-applied at this step —
      it already shaped the sizes (resolved in design discussion, 2026-06-28).
- [ ] **Sizing until C3.** Pinned: **fully-invested equal-weight within each strategy's
      universe** (closes `C2-M2-SIZING-PARITY` via G2b). C3 replaces this with vol-target
      × confidence.
- [ ] **Confidence gate until C4.** Pinned: the `confidence_gate` field is **inert**
      (always-pass) until C4 supplies calibrated confidence — no strategy is gated out on
      confidence in C6.
- [ ] **Registry storage + provenance format.** YAML mirroring `features/catalog.yaml`;
      `provenance` is either the literal `placeholder` or a `ledger-<id>` reference to a
      passing gate verdict. Exact field set pinned in C6-M1 before the loader code.
- [ ] **View-model surface for Project E.** The loader exposes a serializable view
      (per-strategy: display fields, status, allocation %, provenance summary) the console
      export consumes; the panel is `E-STRATEGIES-PANEL`. The exact view-model shape is
      pinned in C6-M1 and coordinated with the Project E owner.
- [ ] **Ingest coupling in the daily loop.** Whether `trade_daily.py` invokes
      `flows/daily.py` directly or assumes a prior scheduled ingest is a C6-M2 finding;
      the freshness gate (`monitor_freshness`) is the hard precondition either way (a
      stale feed aborts the cycle with a non-zero exit).

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Registry ⇄ catalog drift (a strategy references a feature/target/model that doesn't exist) | Medium | High | G1 is a bidirectional drift test (the `tests/test_catalog.py` pattern, §6); CI-gated. |
| Allocator silently diverges from the proven C2 path (G2a fails) | Low | **Very High** | G2a is a deterministic 0-mismatch parity gate against `derive_target_position` — the single-strategy case must reproduce C2 exactly before any multi-strategy behavior is trusted. |
| Placeholder sizing can't be reconciled with the backtest (G2b fails) | Medium | High | G2b reuses the C2-M3 reconciliation harness + its 1% constant; a failure is the `C2-M2-SIZING-PARITY` finding surfacing, which is the milestone's value. |
| An un-vetted model gets `enabled: true` and trades | Low | **Very High** | The **provenance gate** (G1) forbids `enabled` without a passing-gate `provenance`; the placeholder is the one declared exception. Carries Phase-4A's "no edge without a pre-committed gate" into deployment. |
| Confidence double-counted (weighted at sizing *and* combination) | Low | Medium | Pinned rule: confidence enters **once**, at sizing; the allocator only nets + clamps. Asserted in the G2 tests. |
| Cross-agent conflict on Project E (another agent builds E1) | Medium | Low | `E-STRATEGIES-PANEL` is appended as a coordination task; C6 owns the registry + view-model contract, E owns the panel. `E1-CLOSE.depends_on` is left for the E owner to extend. |
| Daily loop runs on a stale feed | Low | High | The freshness gate (C1-M3) is a hard precondition; a stale/missing feed aborts the cycle with a non-zero exit (cron-detectable), never trades on stale data. |
| Scope creep into C3/C4 (building real sizing/confidence now) | Medium | Medium | Out-of-scope is explicit; the `sizing_policy`/`confidence_gate`/`risk_limits` fields exist as **placeholders/inert** in C6 and are populated by C3/C4. |

## Sequencing notes

- **C6-M1 ships the registry contract before C6-M2 (and C3/C4) write against it**
  (METHODOLOGY §4 — the central rationale for ranking C6 ahead of C3/C4). The schema +
  drift test are the contract; everything downstream consumes it.
- **C6-M2 ships the G2/G3 gates with their thresholds pinned before any parity or
  liveness is measured** (§1/§2). G2b reuses the C2-M3 reconciliation constant under its
  existing drift contract (§6) — no new tolerance is invented.
- **C6 must not touch walk-forward split logic.** The allocator + executor consume
  forecasts, feature rows, and prices; they leave `walkforward.py`/`harness.py`
  invariants untouched (`backtest/CLAUDE.md`). The harness self-tests stay green.
- **New module convention.** C6 adds `src/quant/execution/strategy_registry.{py,yaml}`
  (the `execution/` package already exists from C2) and `scripts/trade_daily.py` — both
  convention-*following* (mirroring `features/catalog.{py,yaml}` and the existing runner
  scripts), not convention-*setting*.
- **C6 does NOT depend on `A-DSR-GATE`** (no Sharpe claim → no deflation), mirroring
  C1/C2. It depends only on `C2-M2` (done) for the bridge contract; C6-M2 additionally on
  `C1-M3` (freshness) and `C2-M2`.
- **Ledger discipline.** C6 is infrastructure, not a research trial — it makes no
  pre-registered edge claim, so it contributes **no** research trials to the deflation
  `N`. A C6-M2 run may record an **audit-only** ledger entry (`n_comparisons = 0`) per the
  A-LEDGER-RUNNERS pattern (mirrors C1/C2).
- **Downstream unblock + closeout.** `C6-M1` unblocks `C3-PRD`, `C4-PRD`, and
  `E-STRATEGIES-PANEL`. `C-CLOSE.depends_on` already lists `C6-PRD/C6-M1/C6-M2`; any C6
  task appended later must be added there (AGENT_OPERATION Step 7 corollary).

---
*Status: DRAFT (2026-06-28) — pre-commitment for Project C6. Thresholds in "Success
Metrics" and "Pre-committed gate" (G1 = 0 unresolved refs / 0 provenance violations,
G2a = 0 single-strategy mismatches, G2b ≤ 1% sizing reconciliation, G3 ≥ 5 clean daily
cycles) and the pinned design rules (equal-weight capital budget, net-per-symbol-then-
clamp, confidence-once-at-sizing, provenance gate, fully-invested-equal-weight
placeholder sizing, paper-only) are frozen on ratification; changes require a PRD
revision and a new ledger entry, not an in-flight override. Next: `/plan` turns C6-M1
into an implementation plan.*
