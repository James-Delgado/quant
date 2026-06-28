# Execution-Platform Setup (C2-M1)

> **Milestone**: C2-M1 — "LEAN local installed; hello-world algorithm runs."
> **PRD**: [`.claude/prds/c2-lean-paper.prd.md`](../../.claude/prds/c2-lean-paper.prd.md) (scope item 1).
> **Roadmap**: [`PROJECT_ROADMAP.md`](../PROJECT_ROADMAP.md) §7 (C2), §8.3 (LEAN-first /
> Alpaca-paper fallback decision), §8.4 (ARIMA placeholder).
> **Methodology** (binding): [`METHODOLOGY.md`](../METHODOLOGY.md) — §4 (contract before
> consumer), §9 (honest deviation declaration).
> **Runnable half**: [`scripts/c2_hello_world.py`](../../scripts/c2_hello_world.py)
> (tests: [`tests/test_c2_hello_world.py`](../../tests/test_c2_hello_world.py)).

This doc is the **platform contract** the C2-M2 execution bridge commits to: it records
*which* execution platform C2 runs on, *why*, and *how to stand it up and smoke-test it*.
Per the PRD it is the prose half of C2-M1; the runnable half is a hello-world that boots
the paper engine and places one paper order.

The filename is `lean-setup.md` because that is the deliverable path pinned in the C2 PRD
and `PRIORITIES.yaml` before any compute (METHODOLOGY §1) — it is retained even though the
platform decision below selected the Alpaca-paper fallback over LEAN, so the pinned
contract and the artifact path stay identical. The LEAN install steps are preserved in the
appendix for the future swap.

---

## 1. Platform decision record

### The ratified plan (ROADMAP §8.3)

> *LEAN local first; fall back to the Alpaca paper adapter only if LEAN install friction
> exceeds 2 days.* The model lives **outside** the engine; the engine consumes predictions
> via a signal feed. The fallback must be a **swap, not a rewrite** — enforced by the
> C2-M2 `ExecutionBridge` Protocol (`LeanBridge` ‖ `AlpacaPaperBridge` behind one interface).

### What was attempted (LEAN-local)

| Step | Result | Elapsed |
|---|---|---|
| Docker Desktop (Apple Silicon, macOS 13.5) | ✅ installed, daemon up, **native `linux/aarch64`** (no amd64 emulation — removes the M-series LEAN risk the plan flagged) | user-driven |
| LEAN CLI via `pipx` (isolated from `.venv`, so its heavy dep tree never touches the research env) | ✅ `lean 1.0.227` installed | ~12 s |
| `lean init` (scaffold workspace + sample data) | ❌ **blocked**: aborts at `User id:` — requires a **QuantConnect account**, and meaningful *local* CLI use (data download, local live/paper) sits behind a **paid "Quant Researcher" seat** (~$60–100/mo) | immediate |

The LEAN *engine* is free and open-source (Apache 2.0); the **LEAN CLI + QuantConnect data
/ local-live services are paywalled**. The blocker is therefore not time-bounded effort but
a **recurring monetary subscription** — a fortiori worse than the §8.3 "2 days of friction"
trigger, which it satisfies categorically.

### Friction assessment — why the fallback is the *right* call here, not just the cheap one

C2 is **infrastructure**, and this project has already built the substrate that is most of
QuantConnect's value:

- **Backtester** — we have a bespoke, leakage-controlled **purged walk-forward backtester**
  that is C2's *reconciliation ground truth* (`backtest/harness.py`). Adopting LEAN's
  backtester would reconcile our engine against *LEAN's* engine (different purge / fill /
  cost conventions) — fighting our pinned invariants instead of validating them.
- **Data** — we have our own lake (Tiingo/Alpaca/FRED) and the C1-M2 PIT same-day reader.
  LEAN wants its own data format: conversion friction *and* cost.
- **Model-outside-the-engine** (PRD invariant) — so LEAN's role collapses to "consume an
  external signal and place a broker order," exactly what the Alpaca API gives us directly.

LEAN's one *unique* value — a production multi-asset **live** engine — is **premature**:
C2 is paper-only, going live is gated on the C2-M3 reconciliation pass, and no Project-B
model has cleared an edge gate. Paying a monthly subscription to use ~10 % of LEAN, for a
live capability we cannot yet use, is spending ahead of need.

### Decision

**Platform = Alpaca paper trading** (the §8.3 fallback), taken on **2026-06-28**.

- Pure-Python (`alpaca-py 0.43.4`, already installed), **zero Docker at runtime**, free and
  unlimited paper trading.
- Credentials already exist: the `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` in `.env` (the same
  keys the project ingests bars with) resolve to an Alpaca **paper** account (`PA…`).
- LEAN stays a documented future swap (appendix §A) behind the C2-M2 `ExecutionBridge`
  Protocol — revisit only if C2+ wants asset classes we lack data for, or hosted live
  deployment *after* a B-model clears its gate.

This is a declared deviation from the LEAN-first default (METHODOLOGY §9): the default was
**attempted**, the blocker (paid-account gate) is **named**, and the fallback is the one the
PRD pre-ratified for exactly this case — not an improvised substitution.

---

## 2. Alpaca paper setup runbook

### Prerequisites

1. **`alpaca-py`** — already in `.venv` (`0.43.4`). If absent: `.venv/bin/pip install alpaca-py`.
2. **Paper API keys in `.env`** — `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`. These are loaded by
   `quant.config.settings` (already required at startup). Alpaca issues a **separate** key
   pair for the paper environment; this repo's keys are already paper keys (they authenticate
   to `paper-api.alpaca.markets` and return a `PA…` account). If you ever need fresh paper
   keys: Alpaca dashboard → **Paper Trading** → *Generate New Keys* → paste into `.env`.

> No QuantConnect account, no Docker, no data-format conversion. The paper endpoint is pinned
> by `build_paper_client(..., paper=True)`; there is **no live-trading code path** in C2.

### Run the hello-world

```bash
.venv/bin/python scripts/c2_hello_world.py            # boot + place 1 share SPY (paper), then cancel
.venv/bin/python scripts/c2_hello_world.py --dry-run  # account summary only; no order
.venv/bin/python scripts/c2_hello_world.py --no-cleanup  # leave the order resting (for a manual fill check)
```

### Captured evidence (2026-06-28, market closed)

```
platform        : alpaca-paper
paper account   : PA3WV9497WLA  (AccountStatus.ACTIVE)
cash / equity   : 1000000 / 1000000  (buying_power 4000000)
market open     : False
order submitted : OrderSide.BUY 1 SPY
order id        : f8878264-31ae-4c5b-b736-3965a5c2c774
order status    : OrderStatus.ACCEPTED
cleanup         : {'cancelled': True, 'order_id': 'f8878264-31ae-4c5b-b736-3965a5c2c774'}
```

This proves the full `credentials → TradingClient → MarketOrderRequest → broker ack →
cancel` path is live. The market was closed, so the order **rested** as `ACCEPTED` (Alpaca
queues a market `DAY` order for the next open) rather than filling immediately; submitting
during RTH instead yields a `FILLED` status and the cleanup step is skipped (a fill is no
longer cancelable). Either outcome demonstrates placement — the M1 success criterion.

---

## 3. What M1 establishes (and the boundary to M2/M3)

M1 is the **platform smoke test only**. It deliberately does **not**:

- run a model, wire `build_features(asof=…)`, or compute a signal — **C2-M2** adds the
  `ExecutionBridge` + `daily_signal(asof)` (ARIMA → target position) and the **G1 signal-parity
  gate** (bridge decision == backtest-path decision, 0 mismatches);
- reconcile paper P&L against the Phase 1 backtest — **C2-M3** adds the **G2** reconciliation
  gate (≤ 1 % relative delta, fully decomposed residual) under matched cost + fill
  assumptions (`docs/concepts/cost-model.md`);
- run the daily loop — the **G3** ≥ 5-cycle liveness check is a C2-M3 runbook exercised by a
  real multi-day paper run with position-state round-trip.

The contract M2 inherits from this milestone:

- **Boundary** — the broker-agnostic `ExecutionBridge` Protocol; `AlpacaPaperBridge` is the
  first impl, `LeanBridge` the deferred swap (appendix §A).
- **Endpoint** — paper only, `paper=True`, never live (live is post-C2, gated on G2).
- **Decision cadence** — per PRD Open-Q 1, C2 trades the **parity-safe Tiingo T+1** source
  (the dataset the backtest trains on, so the C1 G2 parity holds structurally); the
  T-evening Alpaca same-day feed is an explicit train/serve-skew source and is out of scope
  (`C1-M2-ALPACA-FRESHNESS`, PRIORITIES rank 43).
- **Order convention** — the hello-world's `MarketOrderRequest` is a *trivial fixed*
  placeholder (1 share, always BUY). M2 replaces it with `sign(ARIMA forecast) → target
  position` at fixed notional; that mapping is pinned **once** in M2 and shared with the
  backtest path so G1 reconciles (sizing/vol-targeting is C3, not here).

---

## 4. G3 — daily paper-loop liveness runbook (C2-M3)

> **What G3 certifies.** A real paper run completes **≥ 5 consecutive daily cycles**
> (`G3_MIN_CYCLES`, pinned in `scripts/reconcile_paper_backtest.py`) with **zero**
> pipeline errors and position state that **round-trips** across runs (run N's
> persisted holdings == run N+1's opening holdings). This is the *liveness* gate —
> it proves the loop **runs**, not that it **profits** (the placeholder's P&L is
> uninteresting by design). The merge-blocking reconciliation evidence is the
> deterministic G2 replay (§ below); G3 accrues over real market days.

Each cycle is one call to `run_daily_cycle` (composed by `run_paper_loop`), which
chains the C2-M1/M2 primitives:

```
load_position_state(state.json)          # prior holdings (None on cycle 1)
  → daily_signal(asof)                   # get_pit_panel → ARIMA → target position (C2-M2)
  → bridge.place_target(...)             # AlpacaPaperBridge submits the signed-delta order
  → save_position_state(holdings, …)     # persist the broker's reported positions
```

**Operational procedure** (one cycle per trading day, ≥ 5 sessions):

1. After the Tiingo T+1 bar lands (parity-safe cadence, PRD Open-Q 1), run one cycle:

   ```bash
   # A thin daily driver over the C2-M3 primitives (run_paper_loop with a single asof):
   .venv/bin/python - <<'PY'
   import pandas as pd
   from quant.execution.lean_bridge import AlpacaPaperBridge
   import importlib.util, pathlib
   spec = importlib.util.spec_from_file_location(
       "rpb", pathlib.Path("scripts/reconcile_paper_backtest.py"))
   rpb = importlib.util.module_from_spec(spec); spec.loader.exec_module(rpb)
   bridge = AlpacaPaperBridge.from_settings()
   state = rpb.run_paper_loop([pd.Timestamp.now("UTC").normalize()],
                              bridge, "data/c2/paper_state.json")[0]
   print("cycle ok — holdings:", state.holdings)
   PY
   ```

2. Repeat on the next trading day. `data/c2/paper_state.json` carries holdings
   forward; cycle N+1 opens where cycle N closed (the round-trip).
3. After ≥ 5 clean cycles, record the run in `lean-setup.md` (date span, cycle
   count, zero-error confirmation) — the G3 evidence, mirroring the §2 hello-world
   evidence capture.

**What is testable now vs. operational.** The *gateable half* of G3 — the loop
runs end-to-end and state round-trips across cycles — is covered deterministically
in `tests/test_reconciliation.py::test_run_paper_loop_state_round_trips` (5 cycles
through a fake bridge). The *live* ≥ 5-session accrual against the real Alpaca
paper account spans real market days and cannot be run in a single session; it is
this runbook, exercised operationally (declared per METHODOLOGY §9).

---

## Appendix A — LEAN-local install (preserved for the future swap)

If a later milestone justifies LEAN (multi-asset data we lack, or hosted live after a
B-model clears its gate), the `ExecutionBridge` Protocol makes it a `LeanBridge` swap. The
install path, for the record:

```bash
brew install --cask docker && open -a Docker      # Docker Desktop; wait for the daemon
brew install pipx && pipx install lean            # LEAN CLI, isolated from .venv
lean login                                         # requires a QuantConnect account (paid
                                                   #   seat for local data/live as of 2026-06)
lean init -l python                                # scaffold workspace + data (needs login)
lean project-create "HelloWorld"                   # starter algorithm
lean backtest "HelloWorld"                         # runs in the quantconnect/lean container
```

The blocker is `lean login` / `lean init`: a free QuantConnect account no longer unlocks
local data/live use — that requires the paid Quant Researcher seat. Re-evaluate the
subscription cost against the concrete need at that time.

## Appendix B — Friction-budget summary (METHODOLOGY §9)

| Item | Status |
|---|---|
| §8.3 budget | 2 days of LEAN install friction before fallback |
| Actual | Categorical blocker (paid-account gate) hit at `lean init`, well inside the budget; Docker + LEAN CLI themselves installed cleanly in minutes |
| Trigger satisfied | Yes — a recurring paid subscription is friction beyond the 2-day bar a fortiori |
| Fallback | Alpaca paper (pre-ratified §8.3), hello-world green (§2 evidence) |
| LEAN preserved | Appendix A + the C2-M2 `ExecutionBridge` swap |
