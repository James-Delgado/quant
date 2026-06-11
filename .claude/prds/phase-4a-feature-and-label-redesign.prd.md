# Phase 4A — Feature and Label Redesign with Regime-Conditional Evaluation

## Problem

The Phase 3 GBM (with or without sentiment) does not beat the ARIMA baseline out-of-sample net of costs over the 23-year, 33-symbol Dow 30 + ETF panel. Adding complexity in the form of advanced architectures (transformers, foundation models — Track A in `docs/PHASE_4_ADVANCED.md`) before understanding *why* the GBM fails would violate Phase 4's own entry gate, which states: *"If the prototype shows no edge, Phase 4 does not begin as written. The correct response is to revisit features, labels, and assumptions — not to add complexity in the hope it rescues a non-edge."* The cost of skipping this diagnostic phase is months of transformer work that inherits the same defects and produces no attributable signal.

## Evidence

Direct quantitative evidence from `notebooks/04_phase3_sentiment.ipynb` (OOS 2003-04-03 → 2026-04-21, 116 folds):

| Arm | OOS Sharpe | Max DD | Gates passed |
|---|---|---|---|
| GBM (no sentiment) | −0.216 | −567% (simulator artifact) | 2 / 6 (T2, T5) |
| GBM (+ sentiment) | +0.024 | −48.74% | 2 / 6 (T2, T5) |
| Always-long | **+0.704** | — | — |
| ARIMA(1,0,0) | +0.434 | — | — |
| Random walk | +0.376 | — | — |

Qualitative evidence:

- The +0.240 Sharpe lift from sentiment is concentrated in the 2008–09 crisis (1 of ~6 macro regimes in the OOS span). Outside the crisis, sentiment contributes little.
- nb03 SHAP rankings on the Phase 3 universe show macro features (DFF, yield_curve, DGS10, VIXCLS) dominating in-sample, but out-of-sample performance does not reflect this — a signal of feature instability, label misspecification, or possible FRED ASOF-join leakage.
- Phase 2.5 lifted Sharpe from −0.833 to +0.487 on a narrow 6-symbol post-2010 panel; the same features delivered −0.216 on the broader 33-symbol 23-year panel. Features that worked on the narrow sample do not generalize to multi-regime data.
- The GBM produces directionally mean-reverting predictions on a structurally trending universe — buy-and-hold (+0.704) outperforms by 0.68 Sharpe.

## Users

- **Primary**: The researcher (you), working interactively in notebooks and the harness. Phase 4A is designed *in anticipation of* the continuous-agent pair from Phase 5, but the agents themselves are not the primary user yet.
- **Secondary (future)**: The planned continuous feature-engineering and continuous model-development agents (Phase 5). Phase 4A leaves behind artifacts (feature catalog, ablation harness, regime-tagged evaluation outputs) that these agents will read and write to.
- **Not for**: Production traders, execution systems, live capital. Phase 4A is offline research only.

## Hypothesis

We believe that **redesigning labels, adding cross-sectional + regime-aware features, and switching to rolling-window + regime-conditional evaluation** will produce **a GBM that beats the ARIMA baseline OOS net of costs in at least 2 of the 3 most recent macro regimes** for **the researcher (and, eventually, the continuous-agent pair)**. We'll know we're right when **GBM Sharpe > ARIMA Sharpe in ≥ 2 of 3 recent regimes (e.g., 2010–2019 QE bull, 2020–2021 COVID, 2022–2026 rate cycle), with the Diebold-Mariano test rejecting equal-loss at p < 0.05 in at least one of those regimes.**

## Success Metrics

| Metric | Target | How measured |
|---|---|---|
| GBM vs ARIMA OOS Sharpe, per regime | GBM > ARIMA in ≥ 2 of 3 most recent regimes | Rolling-window walk-forward, regime-tagged via VIX threshold or HMM (decision deferred) |
| DM test on GBM vs ARIMA residuals, per regime | p < 0.05 in ≥ 1 of 3 recent regimes | `backtest/statistics.py::diebold_mariano` with HLN correction |
| Per-feature edge attribution | ≥ 3 features show ≥ 0.1 Sharpe lift net of costs in ≥ 1 regime | Per-feature ablation matrix (run with / without feature, hold rest constant) |
| Label-scheme comparison | One label scheme strictly dominates signed-return on aggregate OOS Sharpe AND on trend-fighting bias | Labels-ablation matrix across signed-return, vol-scaled, triple-barrier, meta-labeling |
| Feature catalog coverage | 100% of features in `features/engineering.py` registered with metadata | YAML/JSON registry checked into repo |

## Scope

**MVP** — Four workstreams advancing together:

1. **Label redesign as peer workstream.** Test 3–4 label schemes (signed return, volatility-scaled returns, triple-barrier per López de Prado, meta-labeling) as a labels-ablation matrix. Pick the scheme that best resolves the trend-fighting bias.
2. **Cross-sectional + regime-aware features.** Add features the current model lacks: cross-sectional return rank, cross-sectional volatility rank, regime indicators (VIX-conditional, yield-curve-conditional, possibly HMM-derived), and any per-symbol features the SHAP / feature-leakage investigation surfaces. Run per-feature ablation.
3. **Rolling-window + regime-conditional evaluation harness.** Replace the single 23-year aggregate Sharpe gate with rolling-window walk-forward evaluation, regime-tagged outputs, and per-regime DM tests. The current single-aggregate evaluation cannot resolve regime-dependent performance.
4. **Feature catalog infrastructure.** Structured YAML/JSON registry of features with metadata (family, lookback, source, ablation status, regime performance). Designed for the future continuous-agent pair to read/write.

**Out of scope**

- **Transformer / TFT / time-series foundation models** — Track A proper. Deferred until Phase 4A's exit gate clears. *(Note: this is "deferred until earned," not "permanently excluded" — it is the next phase if and only if Phase 4A succeeds.)*
- **Execution layer (LEAN / paper trading)** — Track B. Out of scope.
- **Polymarket / event markets** — Track C. Out of scope.
- **Continuous-agent harness, scheduler, prompt infrastructure** — Phase 5. Phase 4A leaves artifacts the agents will consume but does not build the agents themselves.
- **New data sources / ingestors** — Phase 4A works with current data (OHLCV + FRED + SEC filings + sentiment). If the ablation work surfaces a need for new data, that becomes a *finding* feeding a follow-up phase, not a Phase 4A deliverable.

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->

| # | Milestone | Outcome | Status | Plan |
|---|---|---|---|---|
| 1 | Rolling-window + regime-conditional evaluation harness | Researcher can run any model through regime-tagged evaluation; per-regime Sharpe, DM p-value, and gate outcomes are first-class outputs of the harness | in-progress | [phase-4a-milestone-1-regime-harness.plan.md](../plans/phase-4a-milestone-1-regime-harness.plan.md) |
| 2 | Label-scheme ablation matrix | Researcher knows which label scheme best resolves trend-fighting bias; signed-return is either confirmed or replaced as the default | pending | — |
| 3 | Cross-sectional + regime-aware feature set + per-feature ablation | Researcher knows which new features add per-regime edge and which are noise; SHAP and OOS performance agree on dominant features | pending | — |
| 4 | Feature catalog (YAML/JSON registry) | Every feature in `features/engineering.py` is registered with metadata; future continuous-agent pair has a machine-readable contract to work against | pending | — |
| 5 | FRED ASOF-join leakage investigation | Researcher knows whether macro feature dominance is real predictive signal or look-ahead artifact; Phase 2.5 baseline either confirmed or invalidated | pending | — |
| 6 | Phase 4A exit-gate report and go/no-go for Track A | A written report documenting whether Phase 4A's exit gate (GBM > ARIMA in ≥ 2 of 3 recent regimes, DM p<0.05 in ≥ 1) is met, with explicit go/no-go for Track A (transformers) | pending | — |

## Open Questions

- [ ] **Is the 23-year OOS span the wrong evaluation window?** Strategy decay is well-documented in quant finance; many production strategies have effective windows of 6–24 months. The Phase 3 result may reflect regime-smearing across 6 distinct macro periods rather than a true no-edge finding. The rolling-window evaluation harness (Milestone 1) is the empirical test.
- [ ] **Does label redesign alone fix the trend-fighting bias?** It is possible the signed-return label is the entire problem — the GBM may be correctly learning a noisy short-horizon mean-reversion signal that is the wrong target on a trending universe. The labels-ablation matrix (Milestone 2) is the empirical test.
- [ ] **Are macro features (DFF, yield_curve, DGS10, VIXCLS) leaking via FRED ASOF joins?** SHAP shows macro dominance IS but performance does not transfer OOS — a classic leakage signature. Milestone 5 investigates.
- [ ] **Regime detection: hand-coded VIX thresholds, or HMM?** Thresholds are simpler and transparent; an HMM is more flexible but adds surface area and overfitting risk. Decision is part of Milestone 1.
- [ ] **What is the right number of regimes?** The PRD assumes "3 most recent" (QE bull, COVID, rate cycle), but the rolling-window evaluation may reveal more or fewer effective regimes.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Label redesign does not fix trend-fighting; model still mean-reverts | Medium | High | Fallback to regime-conditional model abstention (model predicts 0 in trending regimes); document negative finding |
| Rolling-window evaluation reveals no regime has edge | Medium | High | Phase 4A ends with a documented "no edge" report; transition is to either new data sources or fundamentally different label/target framing — *not* to Track A |
| FRED ASOF-join leakage is real and invalidates Phase 2.5 + Phase 3 results | Low–Medium | Very High | Phase 4A surfaces this early (Milestone 5) before investing further; if confirmed, all prior Sharpe numbers require re-statement |
| Feature catalog over-engineered for current scale | Medium | Low | Start with a minimal YAML schema (10–15 fields); resist adding agent-runtime concepts that belong in Phase 5 |
| Scope creep into transformers when GBM "almost" clears the gate | Medium | Medium | Exit gate is binary and pre-committed; "almost passes" means "does not pass." Track A stays deferred. |
| HMM regime model adds overfitting surface without improving signal | Medium | Medium | Default to hand-coded VIX thresholds; only adopt HMM if it provably improves regime classification accuracy on a held-out span |

## Sequencing notes

- This PRD is deliberately *sequential* (not parallelized into two subprojects per the user's original question). Running feature work and architecture work simultaneously would confound the diagnostic question Phase 4A is asking. Track A becomes its own PRD only if and when Phase 4A's exit gate clears.
- This PRD is deliberately *not* the continuous-agent harness (Phase 5). The artifacts it produces (feature catalog, ablation harness, regime-tagged evaluation outputs) are designed to be agent-consumable, but the agents themselves are out of scope.
- The PRD's exit gate is calibrated against ARIMA(1,0,0), not against buy-and-hold. Beating buy-and-hold on a structurally bull universe over 23 years is a separate (and likely harder) problem than producing a model with edge over the simplest predictive baseline. The Phase 4 spec itself anchors on ARIMA.

---
*Status: DRAFT — requirements only. Implementation planning pending via /plan.*
