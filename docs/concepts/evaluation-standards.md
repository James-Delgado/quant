# Model Evaluation Standards

> **Living reference.** This document records the quantitative thresholds used
> to judge whether a predictive model has a real edge, and the reasoning behind
> each threshold. Update it when the universe expands, the label changes, or new
> statistical practice warrants a revision. Do not update thresholds to make a
> failing model pass.

---

## Why thresholds exist before implementation

Setting thresholds after seeing results is p-hacking. Every threshold here was
chosen before any model runs, using statistical arguments independent of outcome.
The exit gate in `docs/PHASE_2_MODELING.md` references this document; the
specific numbers live here with their justifications.

---

## Phase 2 exit gate thresholds

The following six criteria must all be met for Phase 3 to begin.

### T1 — Out-of-sample Sharpe ratio

**Threshold:** OOS Sharpe > 0.4, with the bootstrapped 95% confidence interval
lower bound > 0.0.

**Rationale:** A Sharpe of 0.4 is deliberately modest — not a number that looks
good on paper, but one that is plausible on a 10-name tech-heavy basket with
realistic costs. On 60+ OOS bars the confidence interval lower bound > 0.0 rules
out zero-edge with reasonable power. At 10 names the sampling distribution is
wide; requiring both the point estimate and the CI lower bound prevents a lucky
draw from passing the gate.

**Implementation:** Block bootstrap with ~21-trading-day blocks (one month),
1 000+ resamples. Block length chosen to preserve autocorrelation structure of
daily returns. Use `arch.bootstrap.StationaryBootstrap` or `numpy` resampling.
Do not use i.i.d. bootstrap on financial returns.

**If not met:** OOS Sharpe ≤ 0.4 or CI lower bound ≤ 0.0 → no edge. Document
the result; do not advance to Phase 3. Consider feature leakage audit first.

---

### T2 — In-sample / out-of-sample Sharpe ratio

**Threshold:** IS/OOS Sharpe ratio < 2.0 (equivalently: OOS Sharpe > 0.5 × IS
Sharpe).

**Rationale:** Heavy overfitting produces IS Sharpe that is many multiples of
OOS Sharpe. A ratio above 2× is a strong signal that the model memorised the
training fold rather than learning a generalizable pattern. The 2× bound is a
conservative practical threshold; stricter ratios (1.5×) would be appropriate
with a larger universe and longer test periods.

**If not met:** Large IS/OOS gap → overfitting. Reduce feature count; tighten
regularisation; audit features for look-ahead.

---

### T3 — Baseline panel sweep

**Threshold:** GBM must beat all six baselines on OOS Sharpe, net of costs.

**Baselines (in order of difficulty):**
1. Naive (always long +1) — zero-cost bull-market floor; any real signal beats this.
2. Buy-and-hold SPY — the practical benchmark for a long-only investor.
3. Momentum (sign of trailing 21-day return) — the simplest signal.
4. ARIMA(1,0,0) — AR(1) on the stationary forward-return series; d=0 because labels are already I(0).
5. Pooled linear / Ridge regression — linear ML baseline, same features as GBM.
6. Random walk (predicts training-window mean return for all test bars).

**Rationale:** Beating only ARIMA is a low bar. The meaningful claim is "a
nonlinear model adds value over the linear model trained on the same features."
That claim requires beating Ridge. Buy-and-hold SPY is included because it is
the alternative available to any investor without a model. Failing to beat it on
a risk-adjusted basis means the model destroys value.

Use `evaluate_panel(models, ...)` to ensure identical parameters across all six.

**If not met:** Identify which baselines GBM fails to beat. If it fails Ridge,
the nonlinearity is not being exploited. If it fails SPY risk-adjusted, the
model imposes costs without compensating return.

---

### T4 — Deflated Sharpe Ratio

**Threshold:** DSR > 0.5.

**Rationale:** The Deflated Sharpe Ratio (Bailey & López de Prado 2012) adjusts
the observed OOS Sharpe for the number of hyperparameter configurations tried
(N) and the non-normality of the return distribution. The implementation computes
DSR as `norm.cdf((sr - E[max_sr]) / se_sr)`, which returns a probability in
[0, 1]. A DSR > 0.5 means the observed Sharpe is more likely to be a genuine
edge than a selection-bias artifact across the N trials searched. With N=50 (the
hard cap on hyperparameter configurations), this is achievable when the
underlying edge is real.

**Hyperparameter search cap:** N ≤ 50 configurations (RandomizedSearchCV,
`n_iter=50`). This is both a computational budget and a DSR input. Do not raise
N without recalculating the DSR threshold.

**Formula inputs:** observed Sharpe, N = number of configs tried, skewness and
kurtosis of the strategy returns distribution, T = number of OOS observations.

**Reference:** Bailey, D., & López de Prado, M. (2012). *The Sharpe Ratio
Efficient Frontier.* Journal of Risk.

**If not met:** DSR ≤ 0.5 suggests the result is consistent with overfitting the
hyperparameter space. Reduce N or extend the OOS period.

---

### T5 — Diebold-Mariano test (GBM vs Ridge)

**Threshold:** One-sided Diebold-Mariano p-value < 0.10 (GBM forecast errors <
Ridge forecast errors).

**Rationale:** The DM test formalises whether GBM's forecast improvement over
the linear baseline is statistically distinguishable from noise. p < 0.10
(rather than 0.05) is used because: (1) the test has limited power at 10 names
and ~250 OOS observations, and (2) this is an exploratory result feeding a Phase
3 decision, not a published claim.

**Implementation:** Use `statsmodels.stats.diagnostic.acorr_ljungbox`-style DM
statistic or implement directly. Apply the Harvey, Leybourne & Newbold (1997)
small-sample correction (multiply the DM statistic by
`sqrt((T+1-2h+h*(h-1)/T)/T)` where h is the forecast horizon). Without this
correction the test over-rejects in small samples.

**Reference:** Diebold, F.X., & Mariano, R.S. (1995). *Comparing Predictive
Accuracy.* Journal of Business & Economic Statistics.
Harvey, D., Leybourne, S., & Newbold, P. (1997). *Testing the equality of
prediction mean squared errors.* International Journal of Forecasting.

**If not met:** GBM does not significantly outperform the linear model. The
nonlinear features are not contributing. Consider feature engineering revision
before advancing.

---

### T6 — Maximum out-of-sample drawdown

**Threshold:** OOS max drawdown > −25% (i.e., `metrics["max_drawdown"] > -0.25`).

**Rationale:** A model that produces a Sharpe above threshold but also produces
a −40% drawdown is not operationally deployable at any reasonable leverage.
−25% is the bound consistent with a 2× Kelly leverage and a 50% risk-of-ruin
tolerance under standard drawdown models. At the 10-name universe scale and with
realistic position sizing, a drawdown worse than −25% implies either extreme
concentration risk or a regime event the model cannot handle.

**If not met:** Investigate the drawdown period. Is it a single regime event or
structural? Adjust position sizing or add a drawdown stop rule before advancing.

---

## Failure protocol

If any threshold is not met:

1. Record the result honestly in the session log and evaluation notebook.
2. Identify the most likely failure mode (leakage, overfitting, no edge).
3. Make at most one targeted change (feature set, regularisation, embargo
   length) and re-run.
4. If the gate still fails after one targeted revision, document "no
   edge found at Phase 2 scope" and stop. Do not iterate until the backtest
   looks acceptable.

---

## How thresholds evolve with universe expansion

These thresholds are calibrated for the Phase 2 scope (10 names, daily bars).
They should be revisited when the universe changes.

| Universe size | Key changes |
|---|---|
| 10 names (Phase 2) | Current thresholds. Block bootstrap handles limited OOS sample. |
| 50–100 names (Phase 3+) | Cross-sectional ranking becomes primary metric. OOS Sharpe threshold may rise to 0.6+ due to better diversification. |
| 500 names (S&P 500) | Information coefficient (IC) and ICIR become primary evaluation metrics alongside Sharpe. DSR N cap may need upward revision with longer training histories. |

---

## See also

- [regime-evaluation.md](regime-evaluation.md) — Phase 4A regime-conditional
  evaluation. Adds a regime axis (era and volatility) to per-model OOS
  reporting and defines the Phase 4A success-metric gate
  (GBM > ARIMA in ≥ 2 of 3 recent regimes, DM p < 0.05 in ≥ 1).

## References

- López de Prado, M. (2018). *Advances in Financial Machine Learning.* Wiley.
  (Chapter 14: Backtest Statistics; Chapter 8: Feature Importance.)
- Bailey, D., & López de Prado, M. (2012). The Sharpe Ratio Efficient Frontier.
  *Journal of Risk*, 15(2), 3–44.
- Diebold, F.X., & Mariano, R.S. (1995). Comparing Predictive Accuracy.
  *Journal of Business & Economic Statistics*, 13(3), 253–263.
- Harvey, D., Leybourne, S., & Newbold, P. (1997). Testing the equality of
  prediction mean squared errors. *International Journal of Forecasting*, 13(2),
  281–291.
