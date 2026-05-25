# Phase 2 — Gradient-Boosted Predictive Model

> **Spec document.** See `PROJECT_OVERVIEW.md` for full project context, and
> `PHASE_1_BACKTESTER.md` for the evaluation harness this phase depends on.

---

## Objective

Build the project's **first real predictive model** — gradient-boosted trees —
and evaluate it honestly through the Phase 1 backtester against a classical
baseline. The aim is not high accuracy; it is to establish whether a **small,
real, risk-adjusted edge** exists, net of realistic costs.

Gradient boosting is chosen as the starting model because it has the best
signal-to-effort ratio: it excels on tabular engineered features, trains fast
on CPU (no GPU needed), and is comparatively hard to overfit when disciplined.

---

## Entry gate (prerequisites)

- Phase 1 backtester complete, with all harness self-tests passing.
- The harness can accept a model and produce a leak-free, cost-aware report.

---

## Scope — what to build

1. **Label definition** — what the model predicts.
2. **Feature engineering** — a point-in-time-correct feature store.
3. **The model** — XGBoost / LightGBM, tuned without leakage.
4. **The baseline** — an ARIMA model the gradient-boosted model must beat.
5. **Evaluation** — both run through the Phase 1 harness; a comparison report.

---

## Design detail

### Label definition

Decide and document the prediction target. Options, in rough order of
preference for this project:

- **Cross-sectional return ranking** — "which names will outperform" across the
  universe. Plays to gradient boosting's strengths.
- **Directional classification** — up/down over a horizon.
- **Return regression** — least preferred; noisy.

Consider the **triple-barrier method** (López de Prado) for labeling: label a
sample by which of an upper (profit), lower (stop), or time barrier is hit
first. It produces more trade-realistic labels than fixed-horizon returns.

Whatever is chosen, the label's forward window must be known to the Phase 1
**purging** logic — the two are coupled.

### Feature engineering

Build a **feature store** with strict point-in-time correctness (every feature
timestamped to when it was knowable; use the `ingested_at` stamps). Candidate
features:

- Lagged returns over multiple horizons.
- Rolling statistics — volatility, moving averages, momentum.
- Standard technical indicators (RSI, MACD, etc.).
- **Cross-sectional ranks** — a name's feature relative to the universe.
- Macro context from the FRED dataset (yields, VIX, etc.).

Consider **volume or dollar bars** instead of time bars — time bars have poor
statistical properties (serial correlation, non-normal returns). `mlfinlab`
implements these.

Apply **label-overlap-aware sample weighting** (López de Prado "uniqueness"):
overlapping labels mean samples are not independent; weight them down
accordingly.

### The model

- **XGBoost or LightGBM.** Either is fine; LightGBM is faster on large feature
  sets.
- **Hyperparameter tuning must happen *inside* the walk-forward** — tuning on
  data that includes the test period is leakage. Tune within each training
  window, or on an inner validation split.
- Inspect **feature importances** as a sanity check — if the model leans
  entirely on one suspicious feature, investigate for leakage.

### The baseline

An **ARIMA** model on the same target. This is not a formality: if the
gradient-boosted model cannot beat ARIMA out-of-sample, it has learned nothing
useful and must not advance. GARCH may additionally be used for volatility
estimates feeding position sizing.

### Evaluation

Run both the gradient-boosted model and the ARIMA baseline through the **Phase
1 harness**. Compare on **risk-adjusted, cost-net, out-of-sample** metrics —
not accuracy. Produce a single comparison report.

---

## Deliverables

- The feature pipeline / feature store (extends the Phase 0 lake).
- Label-generation code.
- Model training code (gradient-boosted model + ARIMA baseline).
- A comparison report: both models through the Phase 1 harness, with
  in-sample vs out-of-sample, risk-adjusted metrics, and feature importances.

---

## Exit gate (success criteria)

Phase 3 may begin only when:

- The gradient-boosted model **beats the ARIMA baseline** out-of-sample, net of
  realistic costs, on risk-adjusted metrics.
- The edge is **believable** — small and stable, not a suspiciously large
  number. A huge edge is more likely a bug or leak than alpha.
- The in-sample vs out-of-sample gap is modest (a large gap = overfitting).

If these are not met, that is a valid and important result: document it, and do
**not** paper over it by advancing to a more complex model. A simple model that
honestly shows no edge is more useful than a complex one that hides the fact.

---

## Risks and pitfalls

- **Feature leakage** — a feature that encodes future information. The most
  common cause of fake edges.
- **Hyperparameter p-hacking** — searching configurations until the backtest
  looks good. Constrain the search; account for it via the Deflated Sharpe
  Ratio.
- **Regime dependence** — a model that worked in one market regime may fail in
  another. CPCV's distribution of outcomes helps expose this.
- **Mistaking accuracy for profitability** — directional accuracy above 50%
  does not guarantee positive returns after costs.

---

## Tooling

`xgboost` / `lightgbm`, `statsmodels` (ARIMA/GARCH), `scikit-learn`, `mlfinlab`
(triple-barrier labels, sample uniqueness, bar types), pandas/polars, DuckDB.

---

## What comes next

Phase 3 adds an LLM-derived **sentiment feature** and tests, via ablation,
whether it measurably improves this model.
