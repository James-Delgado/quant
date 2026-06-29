# The Strategy Registry & Daily Executor

> **Living reference.** Companion to `docs/concepts/feature-glossary.md`
> (the feature catalog), `docs/concepts/target-reframing.md` /
> `docs/concepts/target-evaluation.md` (the B1 target catalog and its scoring),
> and `docs/concepts/lean-setup.md` (the execution bridge). This
> document describes the **deployment-side registry** shipped by Project C6 —
> the schema/loader/provenance gate (`C6-M1`,
> `src/quant/execution/strategy_registry.{py,yaml}`) and the daily cron
> executor (`C6-M2`, `scripts/trade_daily.py`). It documents **what exists in
> code**; the code is the source of truth (METHODOLOGY §2). Do **not** edit a
> pinned rule or constant here — change it in code under a ledger entry, then
> reflect it back into this doc.

---

## Why the registry exists

Every registry/label family in this repo has a typed loader, a YAML (or
Python) source of record, a bidirectional drift test, and a companion concept
doc: **features** (`features/catalog.{py,yaml}` ↔ `feature-glossary.md`) and
**targets** (`TARGET_CATALOG` in `features/targets.py` ↔ `target-evaluation.md`).
The strategy registry is the **deployment-side** member of that family — and
this doc is its companion.

Before C6, the execution path built in C2 ran **exactly one hardcoded
strategy**: the ARIMA(1,0,0) placeholder was baked directly into
`lean_bridge.daily_signal`. "Run a different model" meant editing source, and
nothing stopped an un-vetted model from being deployed. The registry is the
missing indirection. It makes "deploy a model" a **registry entry**, not a code
edit (the ROADMAP §3 promise), and it is the **contract that C3 (sizing), C4
(confidence), the C6-M2 daily executor, and the Project E console all consume**
— so it lands *before* any of them (contract-before-consumer, METHODOLOGY §4;
the rationale for ranking C6 ahead of C3/C4).

A strategy is the **deployment-side analog of Project B's research tuple**.
ROADMAP §4 describes research as a portfolio of `(target, model, universe)`; the
registry is the promoted-to-live subset of exactly those tuples — the ones that
cleared a gate and were enabled. It **references** the existing feature and
target catalogs rather than duplicating them (DRY; METHODOLOGY §4).

---

## What a strategy is: the `StrategySpec` field set

A strategy is **not just a model** — it is the full pipeline spec the daily
executor needs to turn a forecast into a paper order. `StrategySpec`
(`strategy_registry.py`) is a pydantic model with `extra="forbid"` (an unknown
YAML key is an error, not a silent pass). The fields:

| Field | Type | What it is | Resolves / pinned to |
|---|---|---|---|
| `id` | `str` (non-empty) | Stable registry key. | unique across the registry (`load_registry` names duplicates) |
| `display_name` | `str` (non-empty) | Human label for the console. | — |
| `description` | `str` (non-empty) | Human-readable summary for the UI. | — |
| `model_ref` | `str` (non-empty) | Which model class produces the forecast. | a key of `MODEL_REGISTRY` |
| `feature_set_ref` | `list[str]` (default `[]`) | The feature subset the model consumes; `[]` for label-only models. | names in `features/catalog.yaml` |
| `target_ref` | `str` (non-empty) | What the strategy predicts. | `known_targets()` = `{next_bar_return}` ∪ `TARGET_CATALOG` keys |
| `universe` | `list[str]` (≥ 1 symbol) | The symbols traded. | non-empty (validator) |
| `decision_rule` | `Literal["sign"]` | Forecast → position. | the C2 parity rule `sign(forecast) ∈ {-1, 0, +1}` |
| `sizing_policy` | `SizingPolicy` | How target positions become sized holdings. | placeholder until C3 (see below) |
| `confidence_gate` | `ConfidenceGate` | Whether/how the strategy is gated on confidence. | inert until C4 (see below) |
| `risk_limits` | `RiskLimits` | Per-strategy risk caps. | permissive placeholder until C3 |
| `cadence` | `Literal["daily"]` | Run frequency. | daily (ratified; no intraday) |
| `broker` | `Literal["alpaca_paper"]` | Execution venue selector. | paper only (live is a later config flag) |
| `enabled` | `bool` | Whether the daily executor runs it. | gated by `provenance` (see below) |
| `provenance` | `str` | The gate verdict that authorizes deployment. | `placeholder` or a `ledger-<id>` |
| `created_at` | `str` | ISO-8601 creation timestamp. | — |
| `enabled_at` | `str \| None` (default `None`) | ISO-8601 enable timestamp; `None` if never enabled. | — |

The YAML source of record is `strategy_registry.yaml`; adding a deployable
strategy is **adding an entry there, not editing source**. The repo seeds
**one** entry: `arima_placeholder` (`enabled: true`, `provenance: placeholder`).

### What each reference resolves to

`registry_drift_report` (the G1 gate, below) resolves the three external
references; `resolve_model_class` does the lazy import at execution time:

- **`model_ref` → `MODEL_REGISTRY`.** A `dict[str, str]` mapping a ref to a
  `"module:Class"` path. The three registered models are `arima_baseline`
  (`quant.models.arima_baseline:ARIMABaseline`), `gbm`
  (`quant.models.gbm:GBMModel`), and `buyandhold_baseline`
  (`quant.models.buyandhold_baseline:BuyAndHoldBaseline`). `resolve_model_class`
  imports lazily so loading the registry does **not** pull XGBoost (via
  `models.gbm`) into memory; the code↔code drift test asserts every path still
  imports. An unknown ref raises `KeyError`.
- **`feature_set_ref` → the feature catalog.** Each member must be a name in
  `features/catalog.yaml` (resolved against `load_catalog()`). An empty list is
  valid and is the correct value for a **label-only** model like the ARIMA
  placeholder, which fits on the price-label series and consumes no features.
- **`target_ref` → `known_targets()`.** This is the union of the **built-in
  targets** (`BUILTIN_TARGETS = {"next_bar_return"}`) and the **B1 target
  catalog** keys (`TARGET_CATALOG` in `features/targets.py`). A strategy may
  reference either family.

### Why `next_bar_return` is a built-in target

`next_bar_return` predates the B1 `TARGET_CATALOG` reframing work. It is the
Phase-1/2 forward-return label produced by
`features.labels.generate_labels(prices, horizon=1)` — the target the ARIMA
placeholder forecasts via `lean_bridge.daily_signal`. The B1 catalog targets
(drawdown / vol / directional) **supplement** it, they do not replace it, so
`known_targets()` unions the built-in with the catalog rather than treating the
catalog as exhaustive. This keeps an already-deployable Phase-1/2 strategy
expressible without retrofitting it into the B1 catalog.

---

## The provenance gate: no deployment without a verdict

The methodology guardrail "no edge without a pre-committed gate" (Phase 4A) is
carried into **deployment**: a strategy may not be `enabled: true` without
`provenance` pointing at a passing gate verdict. `provenance` is one of:

- the literal **`placeholder`** (`PLACEHOLDER_PROVENANCE`) — the **one
  sanctioned exception**, for infrastructure that makes no edge claim
  (`n_comparisons = 0`); or
- a **`ledger-<id>`** reference that must resolve to a `gate_passed` entry in
  `data/ledger.yaml` (the valid ids are read by `_load_ledger_passed_ids()`).

A **disabled** strategy needs no provenance. An enabled one fails the gate if
its provenance is empty, is neither `placeholder` nor a `ledger-` reference, or
is a `ledger-<id>` that does not resolve to a `gate_passed` entry
(`_provenance_violation`). The seed entry `arima_placeholder` is the declared
exception: it exercises the spine end-to-end on paper, its P&L is deliberately
uninteresting, and it makes no edge claim — so its provenance is `placeholder`.

---

## Placeholder sub-models C3/C4 extend

`sizing_policy`, `confidence_gate`, and `risk_limits` are typed sub-models whose
enums are pinned to a **single placeholder value** for C6. They exist now so C3
and C4 are built *into* this contract rather than retrofitted (METHODOLOGY §4):

- **`SizingPolicy.method`** — `Literal["fully_invested_equal_weight"]` (the only
  value in C6). C3 extends the `Literal` with real policies (e.g.
  `vol_target`) and populates the parameters. The executor raises
  `NotImplementedError` on any non-placeholder method rather than sizing
  silently wrong (METHODOLOGY §9 — no silent fallback).
- **`ConfidenceGate.method`** — `Literal["always_pass"]`. The gate is **inert**
  until C4 supplies calibrated confidence; **no strategy is gated out on
  confidence in C6**.
- **`RiskLimits`** — `max_position: float = 1.0` (the per-symbol target-position
  magnitude cap; `1.0` = the full long/short unit the C2 sign rule emits;
  validated `> 0`) and `max_drawdown_stop: float | None = None` (no stop until
  C3 populates it).

---

## The loader and the G1 drift contract

`load_registry(path=DEFAULT_REGISTRY_PATH)` parses the YAML, validates each
entry, and returns `{id: StrategySpec}`. Mirroring `load_catalog`, it raises on
**structural** problems only: a top-level that is not `{"strategies": [...]}`, a
non-list `strategies`, or duplicate `id`s (every duplicate named); per-entry
schema problems surface as pydantic `ValidationError`. **External-reference
resolution and the provenance gate are deliberately not checked here** — they
are the two axes owned by `registry_drift_report` (the
`validate_catalog_coverage` analog).

`registry_drift_report(registry, *, catalog=None, valid_targets=None,
valid_models=None, passed_ledger_ids=None)` is the **G1 gate**. For each
strategy it resolves `model_ref`, every `feature_set_ref` member, and
`target_ref`, then checks the provenance gate. All four reference sets are
**injectable** so the drift test can drive crafted positive/negative cases
without touching the real catalogs (defaults: `MODEL_REGISTRY`, the feature
catalog, `known_targets()`, and the `gate_passed` ledger ids). It returns a
frozen `RegistryDriftReport` with two lists:

- **`unresolved`** — every strategy reference with no matching catalog entry
  (named as `"{id}.model_ref -> {ref}"`, etc.);
- **`provenance_violations`** — every `enabled` strategy lacking a valid
  provenance.

`RegistryDriftReport.passed` is `True` **iff both lists are empty** — the pinned
**G1 = (0 unresolved, 0 provenance violations)** threshold. The drift test
(`tests/test_strategy_registry.py`) exercises **both directions** (METHODOLOGY
§6): registry→code (refs resolve) and code→code (`MODEL_REGISTRY` paths import,
`TARGET_CATALOG` keys stay resolvable).

---

## The view-model contract for the Project E console

`strategy_view_models(registry)` returns a JSON-serializable per-strategy view
the console (`E-STRATEGIES-PANEL`) consumes — for **both in-use and idle**
strategies. C6-M1 owns this view-model contract; the panel rendering is a
Project E task. Each view exposes the display fields (`id`, `display_name`,
`description`, `model_ref`, `target_ref`, `universe`, `cadence`, `broker`,
`enabled`), a `status` (`"enabled"` / `"idle"`), an `allocation_pct`, the raw
`provenance`, and a human `provenance_summary` (`_provenance_summary`:
"Placeholder (infrastructure — no edge claim)" or "Gate-verified (ledger-…)").

`allocation_pct` is the **equal-weight `1/N` capital budget**: `100 / N` rounded
to 4 dp across the `N` enabled strategies, and `0.0` for idle ones. With one
enabled strategy it is `100.0`. This is the same `1/N` budget the daily executor
applies (below); the view-model is the read-only projection of it.

`enabled_strategies(registry)` returns the enabled subset in registry order —
the set the daily executor runs.

---

## The daily executor (`scripts/trade_daily.py`)

`trade_daily.py` (C6-M2) is the **deployment spine**: one idempotent cron
entrypoint that turns the enabled registry into paper orders each day. The CLI
chains `ingest → freshness gate → trade one cycle`, exiting non-zero on any
failure (cron mail-on-stderr). Flags: `--asof`, `--no-ingest`, `--no-ledger`,
`--state`. It is **paper only** and touches **no** walk-forward split logic
(`backtest/CLAUDE.md`): it consumes forecasts and prices only.

### The allocator (pure, unit-tested)

`equal_weight_shares` / `size_strategy` / `net_targets` are **pure** (no
network, no lake). The combination rule, pinned in the PRD:

1. **Capital budget** — equal-weight `1/N` across the `N` enabled strategies
   (one knob; confidence/track-record weighting is a deliberate later swap).
2. **Sizing** — `size_strategy` splits a strategy's `1/N` budget **equally
   across its full universe** (`budget / |universe|` per symbol) and converts to
   **integer shares** via `equal_weight_shares`, which reproduces the Phase-1
   simulator's `int(cash / entry_fill)` rule at the paper entry fill (close ×
   the long/short slippage). This is the real capital-based sizing that closes
   `C2-M2-SIZING-PARITY` (the old fixed-1-share placeholder).
3. **Combination** — `net_targets` NETs the signed, sized positions per symbol:
   the **direction** is the budget-weighted sign vote (`_net_direction`,
   deliberately *sizing-independent*), the magnitude is the summed signed
   shares, then the direction is **clamped** to the tightest per-symbol
   `risk_limits.max_position`. Confidence is **not** re-applied here — it already
   shaped the sizes at step 2 (confidence enters **once**, at sizing).

### The cycle (G3 liveness)

`run_trading_cycle(asof, bridge, registry, state_path, *, freshness_fn, …)` runs
one idempotent cycle: **freshness gate** (a stale/missing feed raises
`FreshnessError` → non-zero exit; never trades on stale data) → load prior state
(the round-trip proof) → for each enabled strategy `{signal → size}` at `1/N`
budget → `net_targets` → `bridge.place_target` → persist the bridge's reported
holdings. Every external dependency (freshness, signal, price, capital) is an
**injected callable**, so the loop runs offline in tests through a fake bridge.
`run_trading_loop` iterates the cycle over multiple as-ofs, persisting state
between each. Only the `arima_baseline` signal path is wired (`_strategy_signal`
dispatches on `model_ref`); an unsupported model raises rather than silently
emitting nothing — the documented C6 extension point.

### The three C6-M2 gates

| Gate | Function | Pinned threshold |
|---|---|---|
| **G2a — single-strategy parity** | `single_strategy_parity_report` | with the lone placeholder enabled, the allocator's per-symbol direction reproduces `lean_bridge.backtest_path_target_position` of the same forecast — **0 mismatches** (`G2A_MAX_MISMATCHES`) |
| **G2b — sizing reconciliation** | `sizing_reconciliation_report` | the fully-invested equal-weight notional reconciles with the **real** `backtest/simulator.py` capital deployment to **≤ 1% relative** (`G2B_MAX_RELATIVE_DELTA`, the C2-M3 constant, kept in lock-step by a drift test) |
| **G3 — daily-loop liveness** | `run_trading_loop` | the cycle runs end-to-end with state that round-trips and a non-zero exit on failure; the live **≥ 5 consecutive clean cycles** (`G3_MIN_CYCLES`) accrue against the real paper broker per the cron runbook (spans real market days — declared §9, the C2-M3 precedent) |

A C6-M2 run may append an **audit-only** ledger entry (`n_comparisons = 0`):
C6 is infrastructure and makes no pre-registered edge claim, so a daily run
contributes **no** research trials to the deflation `N` (`_record_ledger`).

### Cron runbook

Run after the parity-safe Tiingo T+1 adjusted bar is available (~12:00 UTC), on
weekdays:

```cron
# 12:30 UTC, Mon–Fri — after the Tiingo T+1 bar, before the next session
30 12 * * 1-5  cd /path/to/quant && .venv/bin/python scripts/trade_daily.py
```

The run is idempotent same-day: position state round-trips, so a re-run
re-derives the same targets and places nothing new once on target.

---

## Where it lands in code

| Milestone | Lands in | What |
|---|---|---|
| **C6-M1** | `src/quant/execution/strategy_registry.{py,yaml}` (+ `tests/test_strategy_registry.py`) | `StrategySpec` + sub-models, `MODEL_REGISTRY`, `known_targets`, `load_registry`, `registry_drift_report` (G1), `strategy_view_models`, `enabled_strategies`; one seeded placeholder |
| **C6-M2** | `scripts/trade_daily.py` (+ `tests/test_trade_daily.py`) | the allocator (`size_strategy` / `net_targets`), the cycle/loop, and the G2a/G2b/G3 gates |

The gate functions and pinned constants (G1 = (0, 0), G2a = 0 mismatches,
G2b ≤ 1%, G3 ≥ 5 cycles, the equal-weight `1/N` budget, the net-per-symbol-then-
clamp rule, the provenance gate) are the source of truth (METHODOLOGY §2).
Changing any after a result is visible invalidates the run and requires a PRD
revision plus a new ledger entry (METHODOLOGY §1).

---

## Update protocol

This document tracks code; the code is authoritative. To change a documented
rule or constant: change it in code under a ledger entry / PRD revision, run the
drift test (`tests/test_strategy_registry.py`) and the executor tests
(`tests/test_trade_daily.py`), then reflect the change here. Do **not** edit a
pinned threshold in this doc alone — a doc that drifts from the code is the
failure mode the registry's drift test exists to prevent (METHODOLOGY §6/§9).

---

## References

- Primary code: `src/quant/execution/strategy_registry.{py,yaml}`,
  `scripts/trade_daily.py`.
- Drift / executor tests: `tests/test_strategy_registry.py`,
  `tests/test_trade_daily.py`.
- PRD (pre-commitment): `.claude/prds/c6-strategy-registry.prd.md`.
- Consumed contracts: `src/quant/execution/lean_bridge.py` (C2 bridge +
  `daily_signal` / `derive_target_position`), `scripts/monitor_freshness.py`
  (C1 freshness gate), `src/quant/storage/realtime.py` (C1 PIT reader),
  `src/quant/features/catalog.{py,yaml}`, `src/quant/features/targets.py`,
  `data/ledger.yaml` (provenance verdicts).
- Binding methodology: `docs/METHODOLOGY.md` — §1 (pinned thresholds), §2
  (gates-in-code), §4 (contract-before-consumer), §6 (drift contracts), §9
  (honest deviation), §15/§17 (tests + E2E).

---

*Sister documents:
[feature-glossary.md](feature-glossary.md),
[target-reframing.md](target-reframing.md),
[target-evaluation.md](target-evaluation.md),
[lean-setup.md](lean-setup.md),
[data-freshness-slas.md](data-freshness-slas.md),
[freshness-monitor.md](freshness-monitor.md).
Primary reference: `.claude/prds/c6-strategy-registry.prd.md`.*
