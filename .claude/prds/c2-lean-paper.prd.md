# C2 — Execution Layer (Alpaca paper trading; LEAN deferred)

> **Platform resolved (C2-M1, 2026-06-28).** This PRD was drafted under the §8.3
> *LEAN-first* default; C2-M1 then **triggered the ratified fallback** — LEAN-local's
> CLI gates local data/live behind a paid QuantConnect seat (friction beyond the
> 2-day budget *a fortiori*), so the chosen platform is **Alpaca paper trading**
> (pure-Python, zero Docker). The prose below has been reframed to Alpaca-paper as
> the **primary** engine with **LEAN preserved as the documented future swap** behind
> the broker-agnostic `ExecutionBridge` Protocol
> (`docs/concepts/lean-setup.md` Appendix A). All pinned gate thresholds (G1 = 0
> mismatches, G2 ≤ 1% relative, G3 ≥ 5 cycles) are **unchanged** — this was a
> platform-wording sync (`C2-DOC-PLATFORM-SYNC`), not a re-commitment.
>
> **Project**: C (Live execution & deployment infrastructure) — sub-project C2.
> **Roadmap**: [`docs/PROJECT_ROADMAP.md`](../../docs/PROJECT_ROADMAP.md) §4 Project C,
> §7 "C2 — Execution layer (Alpaca paper; LEAN deferred)", §8 ratified decisions 3
> (platform: LEAN-first with the §8.3 Alpaca-paper fallback, resolved to Alpaca paper
> in C2-M1) & 4 (ARIMA placeholder).
> **Methodology** (binding): [`docs/METHODOLOGY.md`](../../docs/METHODOLOGY.md) — esp.
> §1 (pre-committed thresholds), §2 (gates-in-code), §4 (contract before consumer),
> §6 (drift contracts), §8 (invariant-parity audits), §9 (honest deviation declaration),
> §15/§17 (tests + E2E notebook).
> **Parent contract**: [`c1-live-data.prd.md`](c1-live-data.prd.md) — C2 consumes C1-M2's
> same-day reader (`storage/realtime.py::get_pit_panel` + `build_features(asof=…)`).
> **Existing substrate**: `src/quant/models/arima_baseline.py` (the placeholder),
> `src/quant/storage/realtime.py` (C1-M2 reader), `src/quant/backtest/{simulator,harness,metrics}.py`
> (the reconciliation ground truth), `docs/concepts/cost-model.md` (the cost assumptions
> a paper engine must match).
> **Backlog tasks**: `C2-M1`, `C2-M2`, `C2-M3` in
> [`docs/PRIORITIES.yaml`](../../docs/PRIORITIES.yaml).

## Problem

C1 closed the *data* gap: there is now a point-in-time-correct same-day reader
(`get_pit_panel`/`get_pit_bar`) and an `asof`-parameterized `build_features`, so
"build today's feature row without look-ahead" is a one-call operation that is
*proven bit-for-bit identical to the backtest path* (C1 G2). But a feature row is
not a trade. **There is no path from a model prediction to an order.** ROADMAP §2:
"Execution layer (paper or live) — ❌ Not built. Phase 4 Track B deferred."

Concretely, three things do not exist between `build_features(asof=today)` and a
position:

1. **No prediction-emission contract.** A model (the placeholder ARIMA(1,0,0), or
   any future B-model) produces a forecast in-process; nothing turns that forecast
   into a **target position** an execution engine can act on, nor records the
   `(symbol, date, prediction, target_position)` decision for audit. The Phase 1
   `simulator.py` consumes a *signal panel* it is handed; it has no notion of
   "emit a signal *today* for *tomorrow's* fill."
2. **No execution engine / broker boundary.** The Phase 1 backtester *simulates*
   fills (next-bar, cost-adjusted) over history. It is not a live or paper engine:
   it cannot hold a paper account, persist position state across daily runs, or
   place an order with a broker. The "same code in backtest, paper, and live"
   guarantee (Phase 4 Sub-track B) requires the model to live **outside** the
   execution engine and the engine to consume its predictions — a boundary that
   does not yet exist.
3. **No backtest-vs-execution reconciliation.** The single largest deployment
   risk after train/serve skew (C1's concern) is **execution skew**: the paper
   engine's fills/costs/P&L diverging from the backtest that justified the
   strategy. Phase 4 Sub-track B pinned this as the Track B exit criterion —
   "paper-trading performance must reconcile with the Phase 1 backtest" — but
   nothing measures it. A strategy that backtests at one Sharpe and papers at
   another is telling you the cost model, the fill convention, or the execution
   wiring is wrong, and you must find out **before** capital, not after.

C2 does **not** seek edge, size positions, or calibrate confidence (those are
C3/C4). Like C1, it is **infrastructure**: it stands up the model→order→account
path with a deliberately trivial strategy, so that all the missing execution
machinery is forced into existence and *reconciled against the backtest* on a
placeholder whose P&L nobody is tempted to defend. It is the C-project's second
load-bearing layer — C1 made "today's data" real; C2 makes "today's order" real.

## Evidence

From the existing code and ratified decisions (read at draft time):

| Fact | Source | Implication for C2 |
|---|---|---|
| Same-day reader is PIT-correct and batch-identical (G1=0 future bars, G2 `rtol≤1e-9`) | `storage/realtime.py`, C1-M2 / nb13 | C2 consumes `get_pit_panel(asof) → build_features(asof) → predict` as a *settled* contract; no execution-side feature recompute |
| ARIMA(1,0,0) fits once per fold, forecasts 1-step-ahead, no per-bar re-fit | `models/arima_baseline.py` | The placeholder signal is cheap and deterministic — a daily run is one `fit` on history-to-`asof` + one `predict_one_step` per symbol |
| Phase 1 simulator fills **next-bar** with a pinned cost model | `backtest/simulator.py`, `docs/concepts/cost-model.md` | Reconciliation is only meaningful if the paper engine's fill convention + costs are *matched* to these; mismatched assumptions are the first thing M3 surfaces |
| Model lives outside the execution engine | Phase 4 Sub-track B; ROADMAP §8.3 | The bridge is "the paper engine consumes an external signal feed"; the engine (Alpaca paper now, LEAN deferred) is an execution *engine*, not the model host |
| Platform resolved to Alpaca paper | ROADMAP §8.3; C2-M1 (`lean-setup.md`) | LEAN-first was attempted and found paywalled; the §8.3 Alpaca-paper fallback (no Docker, free unlimited paper) is the chosen engine. The `ExecutionBridge` abstraction keeps the future LEAN swap a swap, not a rewrite |
| Same-day feed cadence is unresolved for deployment | `C1-M2-ALPACA-FRESHNESS` (PRIORITIES rank 43) | C2 must pick a decision cadence: the parity-safe Tiingo source is `T+1 12:00 UTC`; the freshest same-day Alpaca feed (`T 23:00 UTC`) is *not* parity-safe (IEX raw close ≠ Tiingo adjClose) |

Structural facts that shape the design:

- **The Phase 1 backtest is the reconciliation ground truth, and it is
  deterministic.** Reconciliation does **not** require waiting for wall-clock paper
  days to accumulate. The honest, gateable comparison is: run the paper engine
  over the **same historical window** the Phase 1 backtest covers, feeding it the
  **same daily ARIMA signals**, and reconcile the two equity curves. Forward paper
  trading then accrues over real time as a *liveness* check, but the merge-blocking
  reconciliation gate is the deterministic historical replay (METHODOLOGY §7 —
  verdict from a reproducible run, not from accumulated live luck).
- **Two engines, one signal.** C2 deliberately keeps the *model and signal*
  identical across the backtest and paper paths (the "same code" guarantee) and
  isolates the *execution engine* as the only thing that differs. Any reconciliation
  delta is therefore attributable to execution (cost model, fill timing, corporate
  actions, rounding), not to the strategy — which is exactly what makes a delta
  diagnostic.
- **ARIMA is the placeholder by ratified decision, precisely because its P&L is
  uninteresting.** GBM was rejected (§8.4): it adds ~25 min/backtest of compute
  without exercising one byte of execution infrastructure ARIMA doesn't. The
  placeholder's job is to drive the plumbing, not to make money.

## Users

- **Primary**: the **C3 (sizing/risk) and C4 (confidence) milestones**. Both
  `depends_on C2-M2` in the backlog — they extend the execution path C2 establishes
  (C3 replaces the placeholder's fixed sizing with vol-targeting; C4 feeds calibrated
  intervals into that sizing). C2-M2's `lean_bridge` API is the contract they consume.
- **Secondary**: every **future deployable B-model**. Once a B sub-project surfaces a
  target with edge, it ships its prediction through the *same* `predict → emit signal
  → execute → reconcile` path C2 builds, swapping ARIMA for the B-model. C2 makes
  "deploy a model to paper" a wiring change, not a new project.
- **Tertiary (operations)**: whoever runs the daily loop reads the C2-M3
  reconciliation report (and, later, the C5 dashboard) to confirm paper tracks
  backtest before any thought of live capital.
- **Not for**: backtesting (the Phase 1 path is unchanged and remains the historical
  source of truth); live-capital trading (paper only — going live is explicitly
  gated on reconciliation per Phase 4 Sub-track B exit criterion and is beyond C2);
  intraday execution (daily cadence is ratified, ROADMAP §7/§8).

## Hypothesis

We believe that **a model-outside-the-engine execution bridge — emitting a daily
ARIMA(1,0,0) target-position signal from `build_features(asof=today)`, executed by an
Alpaca paper engine (the ratified §8.3 platform; LEAN deferred) — produces a paper
equity curve that reconciles with the Phase 1 backtest over a shared historical window to
within a pinned tolerance**, and that **the residual delta, once within tolerance, is
fully attributable to declared execution-model differences** — for **the C3/C4
milestones and every future live B-model run** — closing the "execution layer not
built" gap (ROADMAP §2) without making any edge claim.

We'll know we're right when (all thresholds pinned in "Success Metrics" before any
compute, METHODOLOGY §1, and reproduced verbatim in the C2-M2/M3 gate functions,
METHODOLOGY §2):

- **G1 (signal parity)**: the daily target position the bridge emits for `(symbol,
  asof=date)` equals the target position the Phase 1 backtest path computes from the
  *same* ARIMA forecast on the *same* `build_features(asof=date)` row — **zero**
  material mismatches over the replay window. (This is the execution-side analog of
  C1's G2: same inputs ⇒ same decision. It isolates *signal* skew from *execution*
  skew so that any G2 delta is provably executional.)
- **G2 (backtest↔paper reconciliation)**: the paper engine's per-period total return
  over the shared historical window, fed the G1-parity signals under matched cost +
  fill assumptions, differs from the Phase 1 backtest's by **≤ 1.0% relative** (the
  ROADMAP §7 "any >1% delta investigated" line, pinned as a code constant), with the
  residual decomposed into named sources (cost-model, fill-timing, rounding) each
  reported, none "unexplained".
- **G3 (paper-loop liveness)**: a real paper run completes **≥ 5 consecutive daily
  cycles** — `ingest → to_processed → get_pit_panel(asof) → build_features(asof) →
  ARIMA predict → emit signal → paper order → persist position state` — with **zero**
  pipeline errors and position state that round-trips across runs (run N's persisted
  holdings == run N+1's opening holdings).

If **G2 fails** (paper and backtest diverge beyond 1% with an *unexplained* residual),
the verdict is **"execution skew present — the paper path is not a faithful
realization of the backtest until reconciled"**: a valid, pre-committed negative that
**blocks C3/C4 and any live deployment** until the divergence source is found and
fixed. C2 does not ship a bridge whose paper P&L silently disagrees with the backtest
that justified it.

## Success Metrics

The deliverable is **a reconciled, model-outside execution path on a placeholder
strategy**, not a strategy edge. The gate is therefore a **parity + reconciliation +
liveness** gate, not a Sharpe gate — DSR/deflation is undefined here and C2 does
**not** depend on `A-DSR-GATE` (mirroring C1). **All numeric thresholds are pinned
here before any compute (METHODOLOGY §1) and are the source of truth reproduced in the
C2-M2/M3 gate functions (METHODOLOGY §2).**

| # | Claim | Measured on | Statistic | Threshold (pinned) | Reference |
|---|---|---|---|---|---|
| G1 | Bridge-emitted target position == backtest-path target position for the same forecast | every `(symbol, date)` in the replay window | count of material target-position mismatches | **exactly 0** | same-forecast determinism; analog of C1 G2 |
| G2 | Paper engine ⇄ Phase 1 backtest reconcile over a shared window | shared historical window, ≥ 2 regimes, the placeholder ARIMA universe | relative difference in per-period total return, under matched cost + fill assumptions | **≤ 1.0% relative, residual fully decomposed** | `backtest/harness.py` curve = ground truth; ROADMAP §7 |
| G3 | Daily paper loop runs end-to-end with persisted state | a live paper run | (consecutive clean cycles, state round-trip OK?) | **(≥ 5, True)**, 0 pipeline errors | Phase 4 Sub-track B "paper trades daily" |

Notes on the metric choices:

- **G1 and G2 are the merge-blocking gates.** A bridge that emits a different
  decision than the backtest (G1) or whose paper P&L diverges with an unexplained
  residual (G2) is not deployment-faithful; both are hard tests, not advisory
  diagnostics. **G3 is the operational gate** — it proves the loop *runs*, not that
  it *profits*.
- **The 1% reconciliation threshold is a relative total-return delta under matched
  assumptions, not an absolute P&L number.** "Matched assumptions" means the paper
  engine is configured with the *same* cost model (`docs/concepts/cost-model.md`) and
  the *same* next-bar fill convention as the Phase 1 simulator; the residual within
  1% is then decomposed and named (e.g. the paper broker's per-share fee model vs the simulator's
  bps model, integer-share rounding vs fractional, corporate-action handling). A
  residual that cannot be named is treated as a G2 *failure* even if it is under 1%
  (METHODOLOGY §9 — no silent unexplained gaps).
- **Materiality before significance (METHODOLOGY §10).** As with C1, there is no
  statistical "significance" axis — G1 and G3 are deterministic predicates and G2 is
  a deterministic replay. The 1% bar is a pure materiality threshold pinned in code.

## Scope

**MVP** — the three milestones below, executed in order, reusing the existing models
(`arima_baseline.py`), the C1-M2 reader (`storage/realtime.py`), the Phase 1
backtester (`backtest/`), and the pinned cost model. **No new model, no new data
source, no new universe, no sizing/confidence logic, no intraday, no live capital** —
C2 wires the *existing* placeholder ARIMA through a *new* execution boundary and
reconciles it; only the **execution bridge**, the **signal-emission contract**, and
the **reconciliation harness** are new.

1. **C2-M1 — Paper engine installed; hello-world algorithm runs.**
   `docs/concepts/lean-setup.md`: the platform setup procedure and the **platform
   decision record** — which path was taken and why. *Resolved:* LEAN-local was
   attempted and found paywalled (CLI local data/live behind a paid QuantConnect
   seat), triggering the §8.3 fallback, so the procedure documents the **Alpaca-paper
   adapter setup**, with the LEAN install steps preserved in Appendix A for the future
   swap; plus a hello-world algorithm that boots and places one paper order. This is
   the platform contract (METHODOLOGY §4 — before the C2-M2 bridge code commits to an
   engine). Docs + a runnable hello-world; the production bridge is M2.
2. **C2-M2 — ARIMA(1,0,0) daily signal feeds the paper account.**
   `src/quant/execution/lean_bridge.py` exposing a **broker-agnostic
   `ExecutionBridge`** boundary (an `AlpacaPaperBridge` impl as the primary engine
   plus a deferred `LeanBridge` swap behind one Protocol, so the future LEAN swap is
   a swap) plus a
   `daily_signal(asof)` that runs `get_pit_panel(asof) → build_features(asof) →
   ARIMA.predict_one_step → target position` and emits it to the engine. Ships the
   **G1 (signal parity) gate function** with the pinned `0-mismatch` threshold. Tests
   land with the module (METHODOLOGY §15); a cross-module E2E notebook
   (`notebooks/16_c2_execution.ipynb`, number confirmed against the live sequence at
   build time) exercises reader → features → ARIMA → bridge → paper order on real
   fixtures and renders the G1 verdict (METHODOLOGY §17). **Must not touch
   walk-forward split logic** (`backtest/CLAUDE.md`).
3. **C2-M3 — Backtest-vs-paper reconciliation harness.**
   `scripts/reconcile_paper_backtest.py`: replays the daily ARIMA signals through the
   paper engine over a shared historical window, reconciles the equity curve against
   `backtest/harness.py` under matched cost + fill assumptions, and emits the
   **G2 gate function** (≤ 1% relative delta with a fully decomposed residual) plus a
   reconciliation report. Tests cover the reconciliation arithmetic and the residual
   decomposition (METHODOLOGY §15). The G3 liveness check is a documented runbook in
   `lean-setup.md` exercised by a real ≥5-cycle paper run.

**Out of scope**

- **Live capital / going live** — paper only. The live transition is gated on a
  documented reconciliation (Phase 4 Sub-track B exit criterion) and is a post-C2
  decision, not a C2 deliverable.
- **Position sizing, risk caps, drawdown stops** — C3. C2 emits a placeholder target
  position (e.g. fixed long/flat from the ARIMA sign); vol-targeting and caps are C3.
- **Confidence / prediction intervals** — C4. ARIMA emits a point forecast in C2.
- **Monitoring dashboard / alerting** — C5. C2-M3 emits a reconciliation *report*;
  the live dashboard and divergence alerting are C5. (Feed-staleness alerting already
  exists in C1-M3.)
- **New models / data sources / universe / intraday** — C2 runs the *existing* ARIMA
  on the *existing* daily feeds. A discovered need for a faster feed or a better
  placeholder is a *finding*, not a C2 deliverable.
- **Replacing the Phase 1 backtester** — the backtest path is the reconciliation
  ground truth and is unchanged. C2 *adds* an execution engine alongside it.

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->

| # | Milestone | Outcome | PRIORITIES task | Depends on |
|---|---|---|---|---|
| 1 | Paper engine install + hello-world | `docs/concepts/lean-setup.md` documents the setup, a paper hello-world runs, platform decision recorded (resolved to Alpaca paper — LEAN-local paywalled, §8.3 fallback triggered; 2-day budget satisfied a fortiori) | `C2-M1` | `C2-PRD` |
| 2 | ARIMA daily signal → paper | `execution/lean_bridge.py::ExecutionBridge` + `daily_signal(asof)` emit a daily ARIMA target position; G1 (signal parity, 0 mismatches) gate function passes | `C2-M2` | `C2-M1` |
| 3 | Reconciliation harness | `scripts/reconcile_paper_backtest.py` reconciles paper ⇄ backtest (G2 ≤ 1% with decomposed residual); G3 ≥5-cycle paper liveness verified | `C2-M3` | `C2-M2` |
| Gate | The execution path emits the backtest's decision (G1) AND papers within 1% of the backtest (G2) AND runs daily end-to-end (G3) | Binary. **Pass** → a reconciled paper execution path is in code; C3/C4 unblocked. **Fail (G2)** → execution skew documented; C3/C4 + live stay blocked until reconciled. | — | — |

## Pre-committed gate (verbatim — implemented across C2-M2 and C2-M3)

The gate functions are the source of truth; this prose describes them (METHODOLOGY
§2). C2's gate is the conjunction of three predicates:

1. **Signal parity (G1, C2-M2)** — for every `(symbol, date)` in the replay window,
   `bridge.daily_signal(asof=date)[symbol].target_position` equals the target position
   the backtest path derives from the same ARIMA forecast on the same
   `build_features(asof=date)` row. The material-mismatch count must be **exactly 0**.
2. **Backtest↔paper reconciliation (G2, C2-M3)** — over the shared historical window
   (≥ 2 regimes), with the paper engine configured to the *same* cost model
   (`docs/concepts/cost-model.md`) and next-bar fill convention as `backtest/simulator.py`,
   the relative per-period total-return delta is **≤ 0.01 (1%)** and the residual is
   **fully decomposed** into named execution-model sources — an unexplained residual
   fails the gate even if under 1% (METHODOLOGY §9).
3. **Paper-loop liveness (G3, C2-M3)** — a real paper run completes **≥ 5** consecutive
   daily cycles with **0** pipeline errors and position state that round-trips across
   runs.

The 1% reconciliation tolerance, the 0-mismatch G1 count, the ≥5-cycle G3 count, the
≥2-regime span, and the matched cost/fill assumptions are all pinned constants — the
tolerance lives in **one** module constant consumed by both the reconciliation harness
and its tests under a drift contract (METHODOLOGY §6), so prose and code cannot
diverge. Changing any of them after a result is visible invalidates the run and
requires a PRD revision plus a new ledger entry (METHODOLOGY §1).

## Open Questions

- [ ] **Decision cadence: T+1 Tiingo (parity-safe) vs T-evening Alpaca (not
      parity-safe).** Resolved in **C2-M1, before C2-M2 code**: C2 trades on the
      **parity-safe Tiingo source at T+1** (the dataset the backtest trains on, so C1
      G2 holds structurally), accepting a one-session decision lag. A T-evening Alpaca
      same-day path is an explicit train/serve-skew source (IEX raw close ≠ Tiingo
      adjClose) and is **out of scope** — it is the `C1-M2-ALPACA-FRESHNESS` (PRIORITIES
      rank 43) policy decision, deferred to a future C2/C3 deployment-policy task, not
      a C2-M2 deliverable. Pinned in `lean-setup.md`.
- [ ] **Where does the placeholder target position come from?** The ARIMA emits a
      *return forecast*; C2's placeholder maps `sign(forecast) → {long, flat}` (or
      long/short) at a **fixed** notional — *not* vol-targeted (that is C3). The exact
      mapping (long-flat vs long-short, fixed notional value) is pinned in C2-M2 with
      the G1 gate, because G1 reconciles against whatever mapping the backtest path
      uses; the two must use the identical rule.
- [ ] **Fill convention + cost-model matching for G2.** The Phase 1 simulator fills
      next-bar with a bps cost model (`cost-model.md`). The paper engine (Alpaca paper;
      LEAN deferred) has its own fill/fee model. G2 *requires* configuring the engine to
      match (next-bar fill, equivalent cost), then *decomposing* the residual. Whether
      the engine can be configured to the exact bps model or only approximated (per-share
      fee) is an M3 finding; if only approximated, the approximation is a *named* residual
      component, not an unexplained gap.
- [x] **Platform fallback timing (§8.3) — RESOLVED in C2-M1.** LEAN-local was attempted
      first and hit a categorical blocker (paid QuantConnect seat for local data/live),
      satisfying the >2-day-friction trigger a fortiori, so the `AlpacaPaperBridge` impl
      is the path and the `ExecutionBridge` Protocol keeps the future LEAN swap a swap.
      The decision and the friction-budget assessment are recorded in `lean-setup.md`
      (§1 + Appendix B; audit trail).
- [ ] **Position-state persistence format (G3).** Daily runs must persist holdings so
      run N+1 opens where run N closed, and so the live engine's view matches ours
      (C3-M3 later reconciles against the broker's reported holdings). MVP: a small on-disk state file
      (format pinned in C2-M2); a richer store is a C5 concern, flagged not built.
- [ ] **Reconciliation window selection.** G2 replays over a *shared historical
      window* spanning ≥ 2 regimes (reusing the regime axis from `backtest/regimes.py`).
      The exact window is pinned in C2-M3 before the reconciliation runs, so the
      tolerance is not measured against a hand-picked favorable span (METHODOLOGY §1/§10).

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Execution skew: paper P&L silently diverges from the backtest (G2 fails) | Medium | **Very High** | G2 is a pre-committed merge-blocking reconciliation gate (≤1% with a *fully decomposed* residual). A failure is a *valid negative* that blocks C3/C4 + live until reconciled — C2 never ships a divergent engine. Matched cost + fill assumptions are the structural mitigation. |
| Cost-model / fill-convention mismatch between the paper engine and the Phase 1 simulator | High | High | Expected and designed-for: G2 *requires* matching, then *naming* the residual. The first reconciliation run surfacing the mismatch is the milestone's value, not a defect. Anything unnameable fails the gate (METHODOLOGY §9). |
| LEAN local install friction exceeds budget | — | — | **Realized and resolved in C2-M1**: LEAN-local's CLI gates local data/live behind a paid QuantConnect seat (friction > the 2-day budget a fortiori), so the ratified §8.3 fallback was taken — Alpaca paper behind the same `ExecutionBridge` Protocol. The future LEAN swap stays a swap, not a rewrite. Recorded in C2-M1 (`lean-setup.md`). |
| Signal skew: the bridge emits a different decision than the backtest (G1 fails) | Low | **Very High** | G1 is a deterministic 0-mismatch gate — same forecast + same `build_features(asof)` row ⇒ same target position. The model lives *outside* the engine (one code path for the forecast), so a G1 failure points at the position-mapping rule, which is pinned once and shared. |
| Trading on a non-parity same-day feed reintroduces train/serve skew | Low | High | Resolved in Open Q 1: C2 trades the parity-safe Tiingo T+1 source (C1 G2 holds). The Alpaca T-evening path is explicitly deferred to `C1-M2-ALPACA-FRESHNESS`, not wired in C2. |
| Going live prematurely (before reconciliation) | Low | **Very High** | Live capital is out of scope; the live transition is gated on a documented G2 pass (Phase 4 Sub-track B exit criterion). C2 papers only. |
| `build_features(asof)` / reader contract changes break the daily loop | Low | High | C2 consumes the *settled* C1-M2 contract (G1/G2 already proven, nb13). The existing 467-test suite + C1's parity gate pin the reader's behavior; a regression there trips CI before it reaches C2. |
| C2 ships but no B model is ever deployable (Project B finds no edge) | Medium | Low | Independent by design: the reconciled execution path accrues to C3/C4 and to *any* future deployable model regardless of any B verdict — the "build deployment in parallel" rationale (ROADMAP §3.2). |

## Sequencing notes

- **C2-M1 ships the platform contract before C2-M2/M3 write engine code**
  (METHODOLOGY §4). The LEAN-vs-Alpaca decision (resolved to Alpaca paper) and the
  setup runbook are the contract the bridge commits to; the bridge Protocol is what
  keeps the future LEAN swap a swap.
- **C2-M2 ships the G1 gate function with the 0-mismatch threshold pinned before any
  parity is measured** (METHODOLOGY §2); **C2-M3 ships the G2 reconciliation gate with
  the 1% tolerance pinned before any reconciliation runs** (METHODOLOGY §1). No
  tolerance is computed against an unwritten gate.
- **C2 must not touch walk-forward split logic.** The bridge is an *execution-layer*
  addition; it consumes feature rows and forecasts and leaves `walkforward.py` /
  `harness.py` purge+embargo invariants untouched (`backtest/CLAUDE.md`). The harness
  self-tests (random ⇒ ~0 edge, leaky ⇒ caught) must stay green.
- **New module convention.** C2 introduces `src/quant/execution/` (already implied by
  the `C2-M2` deliverable path in `PRIORITIES.yaml`). This is convention-*following*
  (the path is pre-specified in the backlog), not convention-*setting*; the module
  mirrors the existing `src/quant/storage/`, `features/`, `backtest/` layout.
- **C2 does NOT depend on `A-DSR-GATE`** (no Sharpe claim → no deflation), mirroring
  C1. It depends only on `C1-M2` (done) and `C1-PRD` (done), already encoded in
  `PRIORITIES.yaml`.
- **Ledger discipline.** C2 is infrastructure, not a research trial — it makes no
  pre-registered edge claim, so it contributes **no** research trials to the cross-PRD
  deflation `N`. If a C2 milestone run emits a verdict artifact (e.g. the M3
  reconciliation report), it **may** record an audit-only ledger entry
  (`n_comparisons = 0`) per the A-LEDGER-RUNNERS pattern; bookkeeping, not a deflation
  contribution (mirrors the C1 PRD's ledger note).
- **Project-C closeout.** `C-CLOSE` (PRIORITIES) already lists `C2-PRD`, `C2-M1`,
  `C2-M2`, `C2-M3` in its `depends_on`; no edit needed for this PRD. Per the
  AGENT_OPERATION Step 7 corollary, any C2 task appended later must be added to
  `C-CLOSE.depends_on`.
- **Downstream unblock.** `C2-M2` blocks `C3-PRD` and `C4-PRD`; `C2-M3` is the
  reconciliation evidence those PRDs assume. C3/C4 PRD drafting begins once the
  `ExecutionBridge` contract (C2-M2) is fixed, mirroring how C2-PRD began once C1-M2's
  reader contract was fixed.

---
*Status: DRAFT (2026-06-28) — pre-commitment for Project C2. Thresholds in "Success
Metrics" and "Pre-committed gate" (G1 = 0 signal mismatches, G2 ≤ 1% relative
reconciliation delta with a fully decomposed residual, G3 ≥ 5 clean daily cycles) and
the matched cost/fill assumptions are frozen on ratification; changes require a PRD
revision and a new ledger entry, not an in-flight override. Next: `/plan` turns C2-M1
into an implementation plan.*
</content>
