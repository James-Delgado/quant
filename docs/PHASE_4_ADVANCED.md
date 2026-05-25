# Phase 4 — Advanced Modeling, Execution Layer & Adjacent Tracks

> **Spec document.** See `PROJECT_OVERVIEW.md` for project context, and the
> Phase 1–3 specs for the foundation this phase builds on.

---

## Objective

With a **proven prototype** in hand (a gradient-boosted model that shows a
real, honest, cost-net edge — with or without the sentiment feature), Phase 4
decides and builds the next capability. Unlike Phases 0–3, this phase is an
**assessment plus a set of optional sub-tracks**, not a single linear
deliverable. What gets built is decided *at the start of the phase, based on
results*, and is deliberately not pre-committed.

---

## Entry gate (prerequisites)

- Phases 1–3 complete.
- The prototype clears its exit gate: it beats the ARIMA baseline
  out-of-sample, net of costs, with a believable edge.
- If the prototype shows **no** edge, Phase 4 does not begin as written. The
  correct response is to revisit features, labels, and assumptions — not to
  add complexity in the hope it rescues a non-edge.

---

## The Phase 4 decision

Before building anything, assess: *where is the prototype limited, and is there
a specific reason to believe a given investment will help?* Complexity must be
**earned**, not adopted by default. The sub-tracks below are options; pursue
the one(s) the assessment justifies.

---

## Sub-track A — Advanced predictive models

Pursue only if the gradient-boosted model has **plateaued** and there is a
concrete reason to expect a different architecture to help (e.g. evidence that
sequence/temporal structure or cross-series dependencies carry signal the
boosting model cannot capture).

Options:

- **Transformer models** — e.g. a Temporal Fusion Transformer or a current
  hybrid architecture. Suited to multi-horizon, multi-asset forecasting.
- **Fine-tuned time-series or financial foundation model** — adapt a
  pretrained model (parameter-efficient methods such as LoRA make this feasible
  on modest hardware).

Requirements:

- The advanced model must be evaluated through the **same Phase 1 harness** and
  must **beat the Phase 2/3 model** out-of-sample, net of costs — and beat it
  by enough to **justify its added complexity and cost**. A marginal,
  fragile improvement is not worth the maintenance and overfitting surface.
- **Compute:** training uses **ephemeral spot GPU instances** (AWS g5/g4dn, or
  cheaper RunPod/Vast.ai), spun up per run and shut down after. No persistent
  GPU. Inference for live use remains CPU-friendly.

---

## Sub-track B — Execution layer

Stand up the path from model to live (or paper) trading.

- Adopt **LEAN, run locally** (open-source; cloud subscription optional) as the
  execution and final-validation layer. Running locally avoids the cloud
  live-node RAM cap.
- The trained model lives **outside** LEAN; the LEAN algorithm consumes its
  predictions. This preserves the "same code in backtest, paper, and live"
  guarantee.
- Begin with **paper trading**. A key check: paper-trading results must
  **reconcile with the Phase 1 backtest**. A large discrepancy indicates a
  flawed cost model, leakage, or an execution bug — investigate before risking
  capital.
- This sub-track is the natural bridge into **Project B** (the trading
  platform), and effectively starts it.

### Deployment notes (from project architecture)

- The **daily ingestion cron** and the **live trading algo** co-locate on one
  small **CPU instance**, run as **isolated processes** (separate `systemd`
  services / containers) so one cannot take down the other.
- Model **retraining stays off that box** — it runs on ephemeral compute.
- The data store is backed up to **S3** so the live instance is disposable.

---

## Sub-track C — Polymarket event-betting (separate track)

A distinct modeling problem, **not** part of the price-prediction pipeline.
Event/prediction markets resolve binary on news and outcomes, so they share
little with equity/crypto price dynamics.

- If pursued, this track sits **downstream of the sentiment/LLM stack** (Phase
  3) — the plausible edge is processing news and events better or faster.
- **The current US regulatory status of Polymarket must be verified against
  official sources before any trading.** Its access rules have a complicated,
  shifting history. This is a hard prerequisite.
- Additional risks specific to this track: thin liquidity, market-resolution
  risk, and rapid edge decay.
- Lowest priority of the three sub-tracks; treat as exploratory.

---

## Deliverables

Depends on the sub-track(s) chosen. Possible deliverables:

- (A) An advanced model, its training pipeline, and a comparison report
  against the Phase 2/3 model through the Phase 1 harness.
- (B) A local LEAN integration, a paper-trading deployment, and a
  backtest-vs-paper reconciliation report.
- (C) A Polymarket data pipeline and an event-market model, plus a written
  regulatory-status check.

---

## Exit criteria

Phase 4 has no single linear exit. For each sub-track:

- **A** — the advanced model is adopted *only if* it beats the simpler model by
  a margin that justifies its complexity and cost; otherwise the simpler model
  remains in production and the result is documented.
- **B** — paper-trading performance reconciles with the backtest within a
  documented tolerance before any live capital is deployed.
- **C** — regulatory status confirmed; any model evaluated with the same rigor
  as the price-prediction pipeline.

---

## Risks and pitfalls

- **Complexity for its own sake** — the central risk of this phase. A
  transformer that does not clearly beat the boosting model is a net negative.
- **GPU cost creep** — forgetting to shut down instances; defaulting to
  persistent GPU. Keep training ephemeral.
- **Going live prematurely** — deploying capital before paper trading
  reconciles with the backtest.
- **Reinforcement learning** — attractive but high risk of wasted effort and
  severe overfitting; if explored at all, execution optimization is the most
  defensible application. Not a default sub-track.
- **Polymarket** — regulatory, liquidity, and resolution risk; do not skip the
  status check.

---

## Tooling

PyTorch and the `transformers` ecosystem (advanced models), LoRA / PEFT for
parameter-efficient fine-tuning, ephemeral cloud GPU (AWS / RunPod / Vast.ai),
LEAN (local) for execution, the Polymarket API and supporting data sources for
sub-track C. The Phase 1 harness remains the evaluation standard throughout.

---

## What comes next

Successful completion of sub-track B transitions the project from **Project A
(modeling)** into **Project B (the trading platform)** — connecting brokerages
and crypto accounts, order management, monitoring, and risk controls. That work
warrants its own set of phase documents when reached.
