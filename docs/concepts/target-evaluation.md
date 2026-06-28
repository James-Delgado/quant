# Target Evaluation — OOS Prediction Collection & Per-Regime Scoring

> **Living reference.** Companion to
> [`target-reframing.md`](target-reframing.md),
> [`label-schemes.md`](label-schemes.md),
> [`regime-evaluation.md`](regime-evaluation.md), and
> [`evaluation-standards.md`](evaluation-standards.md). This document explains
> *how* the Project B1 candidate targets are scored — the machinery in
> `src/quant/backtest/target_eval.py` plus the `forecast_skill_z` helper in
> `src/quant/backtest/statistics.py`. `target-reframing.md` defines *what* the
> four targets are; this document covers the harness that produces their
> metrics. The **source of truth** for behaviour is the code and its tests
> (`tests/test_target_eval.py`, `tests/test_statistics.py`); this is the
> rationale and the contract.

---

## Why the Phase 4A harness cannot score these targets

The Phase 4A evaluation path is **return → simulator → Sharpe**. `GBMModel`
is an `XGBRegressor`; `run_portfolio_backtest` routes `sign(pred)` through the
trade simulator and only ever emits `oos_returns` and `oos_forecast_errors`.
Every downstream metric (Sharpe, Sortino, drawdown, the regime DM test) is a
function of that return series.

Project B1 reframes the prediction problem onto **non-return target objects**
(see [`target-reframing.md`](target-reframing.md)):

| Target | Object | Primary metric |
|---|---|---|
| T1 | `P(max drawdown > 5% over next 21 bars)` | ROC-AUC |
| T2 | 21-day realized log-volatility | MAE on log-vol |
| T3 | `sign(ret_5d)` | ROC-AUC **and** tradeable Sharpe |
| T4 | `sign(ret_21d)` | ROC-AUC **and** tradeable Sharpe |

ROC-AUC and MAE are functions of the raw out-of-sample `(y_true, y_pred)`
pairs — a classifier's probability, a regressor's point forecast — **not** of a
simulated return series. The Phase 4A path never exposes those pairs: by the
time the harness has a return series, the prediction object has already been
collapsed to `sign(pred)` and fed to the simulator. You cannot recover an AUC
from a Sharpe.

`target_eval.py` adds exactly the missing surface — a raw-prediction collector
— **reusing the same purged walk-forward machinery** so the leakage controls
are byte-for-byte identical to the rest of the system. It is additive: it never
touches the split logic, and it calls `walkforward_splits` / `simulate` exactly
as the harness does.

The module has three public functions, described below.

---

## 1. The prediction collector — `collect_oos_predictions`

`collect_oos_predictions` is a **prediction-recording sibling of
`run_portfolio_backtest`**. It runs the identical walk-forward:

- The same `walkforward_splits` generator over the master timeline (the
  union of every symbol's feature index, sorted-unique).
- The same **pooled cross-sectional fit**: per fold, all alive symbols are
  stacked vertically into one `(X_train, y_train)` and the model is fit once.
- The same **per-symbol predict** on each symbol's slice of the test window.

The one difference: instead of routing `sign(pred)` through the simulator, it
**records the raw rows** — a DataFrame indexed by date with columns
`("symbol", "y_true", "y_pred")`, sorted by `(date, symbol)`.

### Leakage controls are unchanged

Purge and embargo (`backtest/CLAUDE.md` invariants 1–4) apply on the master
calendar exactly as in the harness, with **`label_horizon` as the purge
boundary**. This is the load-bearing property: because the collector delegates
splitting to the same `walkforward_splits` call with the same `label_horizon`,
it cannot drift from the harness's leakage guarantees. A target with a longer
label horizon (T1/T2/T4 are 21-bar; T3 is 5-bar — versus Phase 4A's 1-bar
label) passes a larger `label_horizon`, which purges *more* training data, not
less. Invariant 4 (test-fold length ≫ `label_horizon + embargo`) must be
asserted on the real slice config by the *caller* — the collector produces the
predictions, it does not adjudicate whether the fold geometry is sound.

### Input contract

`features_by_symbol` and `labels_by_symbol` are per-symbol panels with
**identical keys**, and each symbol's feature frame and label series must
**share an index and be NaN-free** over it (build them with the single-`dropna`
+ intersection discipline the notebooks use). The collector **does not silently
impute or align** — it raises `ValueError` on mismatched dict keys, on a
symbol whose feature/label indexes differ, or on an empty panel. This is
deliberate: silent realignment is exactly the class of bug that reintroduces
look-ahead. The model argument is used **as supplied** — the caller passes a
fresh / deep-copied model per target (the `run_label_ablation` discipline), or
folds leak fitted state across targets.

### Empty result

When no fold produces a usable OOS prediction, the collector returns an empty
frame *with the right columns and dtypes* (object / float / float) rather than
`None` or a bare `DataFrame()`, so downstream `per_regime_metric` /
`simulate_signal_returns` degrade to "no evidence" instead of raising.

---

## 2. The directional Sharpe arm — `simulate_signal_returns`

T3 and T4 are gated on **both** ROC-AUC *and* a tradeable Sharpe (a directional
target must clear both — an AUC edge that does not survive costs is not a
strategy). The AUC comes from `per_regime_metric` on the raw predictions; the
Sharpe comes from `simulate_signal_returns`, which maps a collected prediction
frame back into an OOS return series commensurable with the Phase 4A harness.

For each symbol it forms the signal `sign(y_pred − threshold) ∈ {−1, 0, +1}`,
routes it through the existing `simulate` on that symbol's OOS prices, and
averages the per-symbol return series across symbols per bar (equal-weight
cross-section — the same aggregation as `run_portfolio_backtest`). The result
is a single NaN-free OOS return `pd.Series`.

### Why the decision boundary is 0.5, not 0

This is the subtle convention and the reason the function exists rather than
reusing the harness path directly. A **classifier trained on a 0/1 label
predicts a probability centred at ~0.5** — `P(up)`. The natural trade rule is
"go long when `P(up) > 0.5`", i.e. `sign(y_pred − 0.5)`.

The Phase 4A harness instead uses `sign(pred)` with the boundary at **0**,
because it predicts a *signed return forecast* that is naturally centred at 0.
Applying that `sign(pred)` rule to a probability in `[0, 1]` would make the
signal `+1` on **every** bar (every probability exceeds 0), going long
unconditionally — a benchmark, not a model. Hence `threshold` defaults to
`0.5`. Passing `threshold=0.0` recovers the harness's return-forecast
convention, so a return-valued model can reuse this same arm.

`**sim_kwargs` (commission, slippage, etc.) are forwarded verbatim to
`simulate`, so the cost model is identical to the rest of the backtester (see
[`cost-model.md`](cost-model.md)).

---

## 3. Per-regime scoring — `per_regime_metric`

`per_regime_metric` groups the OOS predictions by regime and scores an
arbitrary `metric_fn(y_true, y_pred)` per regime — the per-regime input
`b1_gate_report` (in `regime_metrics.py`) consumes. Each prediction row's date
is mapped to a regime via a per-date `regime_labels` Series (typically
`tag_regimes(predictions.index.unique(), detector)`; see
[`regime-evaluation.md`](regime-evaluation.md)); rows in a regime are pooled
across symbols and dates, then `metric_fn` scores that pool.

Two graceful-degradation rules keep a thin regime from crashing the whole
report:

- A date with **no regime label** is dropped (it contributes to no regime).
- A regime whose `metric_fn` **raises `ValueError`** — e.g. ROC-AUC on a
  single-class pool, which is genuinely undefined — is recorded as `nan`, so
  that regime reads as "no evidence" rather than aborting the report. (Other
  exception types are *not* swallowed — only the documented `ValueError` from
  the metric.)

`metric_fn` is supplied by the caller (`roc_auc_score`, an MAE function, etc.),
keeping the regime-pooling logic metric-agnostic.

---

## The skill-z deflation analog — `forecast_skill_z`

Every B1 target must clear a **deflation** stage, the second gate after the
pre-committed materiality / significance thresholds (the
[evaluation-standards](evaluation-standards.md) discipline, carried into B1 by
[`target-reframing.md`](target-reframing.md)). The deflation method differs by
whether the target has a tradeable return series:

| Target kind | Deflation method | Where |
|---|---|---|
| Directional Sharpe arms (T3, T4) | **Deflated Sharpe ratio** (Bailey & López de Prado 2014), N from the trial ledger | `statistics.deflated_sharpe_ratio` |
| Non-tradeable targets (T1 drawdown, T2 log-vol) | **Forecast-skill z-score** | `statistics.forecast_skill_z` |

T1 (a drawdown *probability*) and T2 (a vol *forecast*) have **no return
series**, so a DSR is undefined for them. The analog is a one-sample test on a
per-observation **skill** series — "did the model reliably beat the baseline,
observation by observation":

- **Probability target (T1):** the Brier-score improvement per bar,
  `(baseline_prob − y)² − (variant_prob − y)²`.
- **Regression target (T2):** the absolute-error improvement per bar,
  `|baseline_error| − |variant_error|`.

A positive value means the variant beat the baseline on that observation.
`forecast_skill_z` computes `z = mean(skill) / standard-error(skill)` and passes
when `z > threshold` (default `0`) — the `spec.deflation == "skill_z"` stage the
B1 gate consumes for the non-tradeable targets. NaNs are dropped; it raises
`ValueError` with fewer than 2 non-NaN observations (the standard error is
undefined).

### Zero-dispersion edge case

If every retained skill value is identical (`ptp == 0`), the standard error is
0 and the z-score is taken as the limit: `+inf` when the constant mean exceeds
`threshold`, `−inf` when below, `0` on an exact tie. So a perfectly consistent
improvement passes and a perfectly consistent non-improvement fails, without a
divide-by-zero. The `ptp == 0` test (rather than `std == 0`) is intentional: it
is exactly zero even when `std(ddof=1)` carries float-rounding noise.

> **Why drawdown deflation is `skill_z`, not `DSR`.** The B1 PRD's T1 row reads
> "DSR > 0", but a drawdown *probability* has no return series, so T1's catalog
> entry uses `deflation="skill_z"` — the same analog the PRD prose grants the
> vol target. This is a recorded methodology decision, not a silent override
> (it surfaced in the B1-M2 review). Changing it requires a new ledger entry,
> not a mid-matrix edit. See `B1-DD-VOLIMPLIED-BASELINE` (`docs/PRIORITIES.yaml`)
> for the related open follow-up on the T1 baseline itself.

---

## Point-in-time / no-tuning discipline

This document describes *machinery*, not thresholds, so it pins no new number.
The two conventions it does fix — the `0.5` directional boundary and the `z > 0`
skill-z pass rule — live in code (`target_eval.simulate_signal_returns`'s
`threshold` default and `statistics.forecast_skill_z`'s `threshold` default) and
are the pre-committed values. Do not retune them to make a target pass a gate;
the same pre-commitment discipline that protects the T1–T6 thresholds and the
VIX regime thresholds applies here (METHODOLOGY §1).

---

## References

- López de Prado, M. (2018). *Advances in Financial Machine Learning.* Wiley.
  (Chapter 3: labelling and sample weights; the purged walk-forward this module
  reuses.)
- Bailey, D.H., & López de Prado, M. (2014). The Deflated Sharpe Ratio:
  Correcting for Selection Bias, Backtest Overfitting, and Non-Normality.
  *Journal of Portfolio Management*, 40(5), 94–107. (The DSR arm for T3/T4.)

---

*Sister documents:
[target-reframing.md](target-reframing.md),
[label-schemes.md](label-schemes.md),
[regime-evaluation.md](regime-evaluation.md),
[evaluation-standards.md](evaluation-standards.md),
[purging-and-embargo.md](purging-and-embargo.md),
[metrics-glossary.md](metrics-glossary.md).*
