# Performance Metrics Glossary

> Reference for all metrics reported by `src/quant/backtest/metrics.py` and
> `src/quant/backtest/report.py`. Read this before interpreting backtest output
> or adding new metrics.

---

## Return metrics

### Total return
`(final equity / initial equity) - 1`

The simplest measure. Easy to understand; hard to compare across strategies
with different holding periods or start dates.

### Annualized return
`(1 + total_return) ^ (252 / n_bars) - 1`

Total return scaled to a one-year equivalent using 252 trading days. Makes
strategies of different lengths comparable. Degenerates to -1.0 (total ruin)
when equity reaches zero — annualisation through zero is undefined.

### Max drawdown
`min over t of: (equity[t] - peak[t]) / peak[t]`

The largest peak-to-trough decline in the equity curve, expressed as a
fraction. Always ≤ 0. A drawdown of -0.25 means the portfolio fell 25% from
its previous high before recovering.

This is the number that gets strategies shut down in practice. Risk managers
watch it, not Sharpe. Size positions so that the expected max drawdown is
survivable for the operator.

---

## Risk-adjusted metrics

### Sharpe ratio
`(mean daily return / std of daily returns) × √252`

Return per unit of total volatility, annualised. The most widely used
single-number summary of risk-adjusted performance.

| Value | Interpretation |
|-------|----------------|
| < 0 | Strategy loses money |
| 0 – 0.5 | Weak, barely above noise |
| 0.5 – 1.0 | Acceptable for a live strategy |
| 1.0 – 2.0 | Good |
| > 2.0 | Excellent — verify carefully for data leakage |

Uses `ddof=1` for the standard deviation. Returns 0.0 when std is zero
(constant or zero returns).

### Sortino ratio
`(mean daily return / downside std) × √252`

Like Sharpe, but the denominator uses only *negative* return days:
`sqrt(mean(r²) for r < 0)`. The argument is that upside volatility is not risk.

A strategy with Sortino >> Sharpe has spiky gains and smooth losses — generally
a desirable property.

### Calmar ratio
`annualized_return / |max_drawdown|`

Return per unit of worst historical loss. A Calmar of 1.0 means the strategy
earns back its max drawdown in one year.

Preferred by practitioners who think in terms of capital at risk rather than
return variance. Reported as `NaN` (shown as "—") when max drawdown is zero —
the ratio is undefined, not infinite, in that case.

---

## Trade-level metrics

### Hit rate
`number of profitable trades / total trades`

Fraction of trades that closed with positive P&L. 0.5 is random. A strategy
can be profitable with a hit rate below 0.5 if winners are larger than losers
(see profit factor).

Always emitted even without a trade log (defaults to 0.0) so callers never get
a KeyError.

### Profit factor
`sum of winning P&L / |sum of losing P&L|`

How much the strategy makes on winners relative to what it loses on losers.

| Value | Interpretation |
|-------|----------------|
| < 1.0 | Losers outweigh winners — net loss |
| 1.0 | Break-even before costs |
| > 1.0 | Winners outweigh losers |
| ∞ | No losing trades |

A hit rate of 40% with a profit factor of 2.0 is viable: you lose 6 small
trades for every 4 large wins and still come out ahead. Always emitted; `NaN`
when no trades exist, `inf` when no losing trades exist.

---

## IS / OOS gap

The summary table and report show `IS metric - OOS metric` for each key metric.

**In-sample (IS):** performance on data the model was *trained* on.
**Out-of-sample (OOS):** performance on data the model *never saw*.

IS almost always beats OOS — that is expected. The question is by how much.

| Sharpe gap | Interpretation |
|------------|----------------|
| < 0.3 | Normal estimation noise, probably fine |
| 0.3 – 0.7 | Investigate — check for feature leakage |
| > 0.7 | Overfit — model learned noise, not signal |

The OOS Sharpe is the honest number. A large IS/OOS gap means the IS Sharpe
is not a reliable estimate of live performance.

---

## Statistical tests

### Diebold-Mariano test
Tests whether model A has statistically lower forecast error than model B. Used
in T5 to ask: "does GBM produce significantly smaller squared errors than Ridge?"

The null is equal MSE. The one-sided alternative tests that GBM MSE < Ridge MSE.
The HLN (Harvey-Leybourne-Newbold, 1997) small-sample correction adjusts the
standard DM statistic for autocorrelation introduced by multi-step-ahead forecasts.
A p-value < 0.10 → reject the null → GBM has meaningfully better forecast accuracy.

### Deflated Sharpe Ratio (DSR)
Adjusts the observed OOS Sharpe for the number of hyperparameter configurations
tested (N ≤ 50) and for return non-normality (skewness, excess kurtosis). Based
on Bailey & López de Prado (2012).

`DSR = Φ((SR* − E[max SR]) / SE(SR))`

where `SR*` is the observed Sharpe, `E[max SR]` is the expected maximum Sharpe
under the null for N trials, `SE(SR)` is the Mertens (2002) standard error
corrected for higher moments, and `Φ` is the standard normal CDF.

DSR > 0.5 means the observed Sharpe is more likely real signal than noise after
accounting for the number of configurations tried. Note: fat-tailed return
distributions (high excess kurtosis) inflate `SE(SR)` and depress DSR — a
legitimate technical challenge, not a modelling error.

### Block bootstrap (Sharpe CI)
Resamples 21-bar contiguous blocks (rather than individual returns) to preserve
autocorrelation structure, then computes a 95% CI for the OOS Sharpe.
The lower bound is the T1 gate criterion.

---

## Model interpretation

### SHAP (SHapley Additive exPlanations)
A game-theoretic method for attributing a prediction to its input features.
Each feature receives a SHAP value — its marginal contribution to the prediction
relative to the model's average prediction (base value).

*Local accuracy:* `base_value + Σ SHAP_i = predicted_output` for every observation.

In this project, computed via XGBoost's native `booster.predict(pred_contribs=True)`
(TreeSHAP — exact polynomial-time algorithm). Output shape: `(n_samples, n_features + 1)`;
the last column is the bias term (expected prediction over all training data).

Positive SHAP → feature pushed prediction more bullish.
Negative SHAP → feature pushed prediction more bearish.

### SHAP beeswarm plot
One dot per observation per feature, sorted by mean |SHAP|. X-axis = SHAP value;
color = feature value (blue = low, red = high). Shows global direction and magnitude
of each feature's impact and how that impact varies with feature level.

### SHAP dependence plot
Feature value (x) vs SHAP value (y) for one feature. A positive slope means
trend-following behavior; a negative slope means mean-reversion. Non-linearity
reveals regime-conditional effects (e.g., the feature matters more in bear markets).

### SHAP waterfall chart
Stacked bar chart for a single prediction showing each feature's SHAP contribution
starting from the base value and ending at the final prediction. Used to explain
why the model made a specific call on a specific day.

### Feature importance (XGBoost)

| Metric | Definition | Best used for |
|--------|-----------|--------------|
| **Gain** | Average loss reduction per split | Ranking predictive value |
| **Cover** | Average observations per split | Assessing breadth of use |
| **Frequency (weight)** | Total split count | Can be misleading — avoid as sole metric |

Gain is the most reliable for assessing predictive value. Disagreement between
metrics is informative: high frequency + low gain = the feature is used often but
does not reduce loss much.

**Implementation note:** When `XGBRegressor` is fit via the sklearn interface
(no explicit `DMatrix`), XGBoost assigns internal names `f0, f1, ...` by column
position. `booster.get_score()` returns these internal names, which must be mapped
back by index to recover human-readable feature names.

### Rolling directional accuracy
63-bar rolling hit rate (fraction of correct sign predictions). 50% = random.
Persistent runs above 50% indicate skill; runs below 50% indicate the model is
actively wrong. Wide variation reveals regime-conditional skill.

### Confusion matrix (direction)
2×2 table of predicted sign vs actual sign. Row-normalized shows precision per
predicted class. Asymmetry (e.g., short precision ≫ long precision) reveals
directional bias in the model's learned patterns.

---

## Not yet implemented

- **Turnover** — fraction of portfolio traded per day. High-turnover strategies
  need a much larger gross Sharpe to survive transaction costs.
- **Information ratio** — excess return over a benchmark per unit of tracking
  error. Relevant once a benchmark is defined.

---

## References

- W. Sharpe, "The Sharpe Ratio", *Journal of Portfolio Management*, 1994.
- M. López de Prado, *Advances in Financial Machine Learning*, ch. 14–16.
- S. Lundberg & S.-I. Lee, "A Unified Approach to Interpreting Model Predictions", *NeurIPS*, 2017.
- D. Bailey & M. López de Prado, "The Deflated Sharpe Ratio", *Journal of Portfolio Management*, 2012.
- D. Harvey, S. Leybourne & P. Newbold, "Testing the Equality of Prediction Mean Squared Errors", *Int'l Journal of Forecasting*, 1997.
