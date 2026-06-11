# Regime-Conditional Model Evaluation

> **Living reference.** Companion to `docs/concepts/evaluation-standards.md`.
> This document specifies how a model's OOS performance is sliced by regime
> for Phase 4A reporting, and the rationale behind the default regime
> definitions. Update it when a new regime is added or a threshold is
> revised. Do not update thresholds to make a failing model pass.

---

## Why regime-conditional evaluation

The Phase 3 GBM (with and without sentiment) is evaluated over an OOS span
of **23 years** (2003-04-03 → 2026-04-21) on a 33-symbol Dow 30 + ETF panel.
That span covers at least six structurally different macro periods —
pre-2008 tech expansion, the 2008–09 crisis, the QE-fueled bull market of
the 2010s, COVID, the 2022 rate-hike cycle, and the post-COVID regime.
A single aggregate Sharpe across all of them cannot answer the question
*"does the model have edge in any regime?"* — it can only tell us that the
mean Sharpe across the panel of regimes is around zero.

Regime-conditional evaluation answers a different and more actionable
question: *given an explicit partition of time into regimes, how does the
model perform inside each regime?* A model that has positive edge in the
QE-bull regime and negative edge in the COVID regime is operationally
useful (run it when the regime detector says "QE-bull"); a model with the
opposite profile is not.

The Phase 4A success-metric gate is defined on this axis:

> GBM Sharpe > ARIMA Sharpe in ≥ 2 of 3 most recent regimes
> (`qe_bull`, `covid`, `rate_cycle`), with the Diebold-Mariano test
> rejecting equal-loss at p < 0.05 in ≥ 1 of those regimes.

---

## Two orthogonal axes

Phase 4A ships **two** regime detectors. They are intentionally orthogonal —
a single date can carry one label from each axis.

### Era axis — `DateRangeDetector` (PRIMARY)

This axis encodes macro-era information that is exogenous to market
microstructure: which calendar period the bar belongs to. The default
ranges are:

| Regime         | Start              | End          | Defining event(s)                                          |
|----------------|--------------------|--------------|------------------------------------------------------------|
| `pre_qe`       | (anything earlier) | 2009-12-31   | Pre-QE / Greenspan put era; default fall-through label.    |
| `qe_bull`      | 2010-01-01         | 2019-12-31   | Post-GFC zero-rate / QE expansion; structural bull market. |
| `covid`        | 2020-01-01         | 2021-12-31   | COVID crash + emergency monetary expansion + tech rally.   |
| `rate_cycle`   | 2022-01-01         | (present)    | Inflation print + Fed rate hikes + 2023 banking stress.    |

Boundaries are **inclusive on both ends**. Boundary dates were chosen
to coincide with widely-accepted macro inflection points; they are not
tuned against any model's performance. See **Update protocol** below
before adjusting.

### Volatility axis — `VIXThresholdDetector` (COMPLEMENTARY)

This axis encodes contemporaneous risk-regime information: a bar's
realised volatility regime as measured by the CBOE VIX close. The
defaults are anchored to the long-run VIX distribution:

| Regime       | Condition          |
|--------------|--------------------|
| `low_vol`    | `VIX <= 15`        |
| `mid_vol`    | `15 < VIX < 25`    |
| `high_vol`   | `VIX >= 25`        |

The thresholds 15 and 25 approximate the ~25th and ~75th percentiles
of daily VIX closes since 1990 (source: CBOE
https://www.cboe.com/tradable_products/vix/). They were chosen *before*
running any Phase 4A model so that per-regime statistics carry comparable
sample sizes; do not adjust them to make a model's regime breakdown look
better.

---

## Point-in-time rule (hard invariant)

> A regime label assigned to date *D* must use only information available
> at or before *D*.

Both shipped detectors honour this trivially:

- `VIXThresholdDetector` indexes `vix_series.loc[D]`. If the VIX series
  passed in *contains* future values, the detector still only reads the
  value at *D*. Missing dates raise `ValueError` — no silent forward fill.
- `DateRangeDetector` consults only a fixed table of date ranges; nothing
  about its labels depends on any external time series.

A future HMM-based detector would have to enforce this differently — it
would need to fit on data ending at *D* and apply only the resulting
parameters at *D*. Until that is implemented and tested, do **not** use a
detector that may peek beyond the date it is labeling. Look-ahead in a
regime detector silently inflates the model's measured per-regime Sharpe.

---

## How per-regime statistics differ from aggregate gates

| Quantity              | Aggregate (existing, `evaluation-standards.md`)              | Per-regime (this doc)                                                  |
|-----------------------|--------------------------------------------------------------|------------------------------------------------------------------------|
| Sharpe                | One number across all OOS bars                               | One number per regime                                                  |
| Diebold-Mariano test  | One test on the full OOS error series                        | One test per regime; regimes with `n < 4` return `None`                |
| Gate decision         | T1 (Sharpe > 0.4 with CI > 0), T3 (beat all six baselines), … | GBM > ARIMA in ≥ 2 of 3 recent regimes, DM p < 0.05 in ≥ 1            |
| Output                | `format_report(result)`                                       | `format_regime_report(result, labels)` + `phase4a_gate_report(...)`    |

Aggregate gates remain the primary go/no-go for Phases 2–3. The Phase 4A
gate is *additional* — it answers the regime-attribution question without
displacing the older standards.

---

## Worked example

```python
from quant.backtest.harness import run_portfolio_backtest
from quant.backtest.regimes import (
    DateRangeDetector,
    VIXThresholdDetector,
    tag_regimes,
)
from quant.backtest.regime_metrics import (
    compute_regime_metrics,
    phase4a_gate_report,
)
from quant.backtest.report import format_regime_report

# 1. Run the harness as usual — `oos_returns` and `oos_forecast_errors`
#    are now populated automatically.
gbm_result   = run_portfolio_backtest(model=gbm,   ...)
arima_result = run_portfolio_backtest(model=arima, ...)

# 2. Tag every OOS bar with its macro-era regime.
era_labels = tag_regimes(gbm_result.oos_returns.index, DateRangeDetector())

# 3. Per-regime Sharpe / Sortino / drawdown for each model.
gbm_per_regime   = compute_regime_metrics(gbm_result.oos_returns,   era_labels)
arima_per_regime = compute_regime_metrics(arima_result.oos_returns, era_labels)

print(format_regime_report(gbm_result, era_labels))

# 4. Phase 4A success gate.
report = phase4a_gate_report(gbm_result, arima_result, era_labels)
print(report["gate_passed"], report["pass_count"], report["dm_p_values"])

# 5. (Optional) Cross-reference with the volatility axis.
vol_labels = tag_regimes(
    gbm_result.oos_returns.index,
    VIXThresholdDetector(vix_series),
)
gbm_by_vol = compute_regime_metrics(gbm_result.oos_returns, vol_labels)
```

The two axes can be inspected side by side but should not be combined
into composite labels in Milestone 1 — that is a Milestone-2/3 concern
(feature engineering, not evaluation).

---

## Update protocol

The default ranges and thresholds in this document are intended to be
stable. They were chosen before any Phase 4A model was run. To change them:

1. Open a PR that explains the new regime / threshold in writing, citing
   the macro event or VIX-distribution argument that justifies it.
2. Re-run every Phase 4A model through the new regime definitions and
   include the before/after gate report in the PR.
3. Do **not** revise these definitions to make a failing model pass — the
   gate's value depends on its definitions being immune to post-hoc
   tuning. The same discipline applies to the T1–T6 thresholds in
   `evaluation-standards.md`.

---

## References

- López de Prado, M. (2018). *Advances in Financial Machine Learning.*
  Wiley. (Chapter 14: Backtest Statistics — for the model-comparison
  baseline.)
- Diebold, F.X., & Mariano, R.S. (1995). Comparing Predictive Accuracy.
  *Journal of Business & Economic Statistics*, 13(3), 253–263.
- Harvey, D., Leybourne, S., & Newbold, P. (1997). Testing the equality
  of prediction mean squared errors. *International Journal of
  Forecasting*, 13(2), 281–291.
- CBOE. VIX historical data and methodology.
  https://www.cboe.com/tradable_products/vix/

---

*Sister documents:
[evaluation-standards.md](evaluation-standards.md) — the aggregate-gate
(T1–T6) thresholds from Phases 2 and 3.
[label-schemes.md](label-schemes.md) — Phase 4A Milestone 2 label-scheme
ablation; per-regime ranking uses the regimes defined here.*
