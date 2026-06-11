# Label Schemes for the Phase 4A Ablation Matrix

> **Living reference.** Companion to `docs/concepts/regime-evaluation.md`
> and `docs/concepts/evaluation-standards.md`. This document defines the
> label schemes the Phase 4A ablation evaluates, their pre-committed
> parameter values, and the point-in-time rule they all share. Update it
> when a new scheme is added or a default is revised. Do **not** retune a
> default to make a scheme pass the gate.

---

## Why test multiple label schemes

The Phase 3 GBM trains on **signed forward returns** — the sign of the
1-bar percent change. Notebook 05 (`05_phase4a_regime_harness.ipynb`)
identified that the GBM's failure attribution is dominated by the
`rate_cycle` regime (2022 onward), where it underperforms ARIMA by
≈ 1.44 Sharpe points. The dominant failure mode is **trend-fighting**:
the model trains primarily on high-vol crisis bars where mean-reversion
pays, then mis-applies that learning in the persistent, low-vol uptrend
of `rate_cycle`.

This is not a feature-engineering problem first — it is a label problem.
Three things are wrong with signed forward return as a target on this
universe:

1. **Scale ambiguity.** A +0.5% bar in `low_vol` and a +5% bar in
   `high_vol` look identical to the model after `sign()`. The model
   therefore weights its loss toward the high-vol regime where the
   absolute moves are larger — exactly where mean-reversion is paid.
2. **No PT/SL discipline.** The label encodes "what happened next" but
   not "what would a trader with discipline have captured." A trader who
   takes a position with a fixed PT/SL would never let a trade run flat
   for a week.
3. **No path-dependence.** A bar that ends +1% but went −5% mid-window is
   labelled identically to a bar that ended +1% on a clean ramp. The
   first is a losing trade for most realistic execution; the second is
   a winning trade.

Each of the schemes below addresses at least one of these failure modes.

---

## Schemes

### 1. `signed_returns` (control)

`generate_labels(prices, horizon=1)` from `src/quant/features/labels.py`.
Forward 1-bar return; downstream `np.sign` produces {−1, 0, +1}. This is
the Phase 3 default and serves as the ablation control.

### 2. `vol_scaled_returns`

```python
vol_scaled_returns(prices, horizon, vol_window=21)
```

Forward return divided by the trailing realised volatility:

`label[t] = (prices[t+h] / prices[t] − 1) / σ̂[t]`

where `σ̂[t]` is the rolling standard deviation of one-bar pct returns
over the most recent `vol_window` returns ending at bar t. **No
look-ahead.**

**What it fixes.** Standardises the training signal so a +1σ move in
`low_vol` and a +1σ move in `high_vol` look identical to the model. The
model can no longer be implicitly weighted toward crisis bars by the
larger absolute scale of crisis returns.

**What it does not fix.** It is still a signed-direction target after
`np.sign()`. It does not encode PT/SL discipline and is not
path-dependent.

### 3. `triple_barrier_labels` (López de Prado AFML §3.5)

```python
triple_barrier_labels(prices, config=LDP_DEFAULT)
```

For each bar t with a valid σ̂[t] and a forward window of
`config.max_horizon` bars:

- Upper (profit-take) barrier: `pt[t] = prices[t] * (1 + pt_sigma * σ̂[t])`
- Lower (stop-loss) barrier:   `sl[t] = prices[t] * (1 - sl_sigma * σ̂[t])`
- Walk forward up to `max_horizon` bars; first hit wins:
  - `+1` → PT hit first
  - `−1` → SL hit first
  - `0`  → neither hit before time-out (or two-sided same-bar hit)

**What it fixes.** Encodes the trading discipline "I expect ≥ pt_sigma·σ̂
upside and can survive sl_sigma·σ̂ adverse motion." The label is
path-dependent: a bar that goes +1σ mid-window but ends flat is labelled
0 (time-out), not +1; a bar that touches −1σ first then recovers is
labelled −1 (stopped out), not 0. This is closer to what a disciplined
trader would actually capture.

**Two-sided same-bar hits.** Without intraday data, a bar where both
barriers cross (`p_future >= pt AND p_future <= sl`) cannot be resolved.
The label falls back to `0` ("ambiguous") rather than fabricating an
order — a López de Prado-recommended fallback.

#### `TripleBarrierConfig` defaults (`LDP_DEFAULT`)

```python
@dataclass(frozen=True)
class TripleBarrierConfig:
    pt_sigma: float = 2.0      # upper barrier in σ̂ units
    sl_sigma: float = 1.0      # lower barrier in σ̂ units
    vol_window: int = 21       # rolling-vol lookback in bars
    max_horizon: int = 5       # time-out in bars
```

These values are **pre-committed**: pinned before any Phase 4A model run.
Same discipline as the `VIXThresholdDetector` thresholds in
`regime-evaluation.md` and the T1–T6 gates in `evaluation-standards.md`.
Override only via a PR that explains the rationale and re-runs the full
ablation.

#### Parameter rationale

- **`pt_sigma = 2.0`, `sl_sigma = 1.0` (asymmetric, 2:1 PT:SL).** López de
  Prado AFML §3.5. Equity has positive long-run drift, so a symmetric
  ±1σ barrier is biased against the natural carry — a trader giving back
  ±1σ on both sides effectively pays the spread on every neutral hold.
  The 2:1 ratio encodes "I take a trade if I expect ≥ 2σ upside and can
  survive 1σ adverse motion" — exactly the convexity the signed-return
  GBM lacks. AFML's chapter-three examples use 2:1 as the canonical
  asymmetric default.
- **`vol_window = 21`.** One trading month. AFML §3.5 uses the recent
  monthly realised vol estimator throughout; Bouchaud & Potters,
  *Theory of Financial Risk*, treats 21–30 days as the empirical "fast"
  vol horizon that captures regime change without dragging in distant
  history. Shorter (5–10 days) reacts too quickly to noise; longer (60+
  days) smooths through regime transitions.
- **`max_horizon = 5`.** One trading week. The AFML §3.5 sweet spot on
  daily bars — long enough to capture meaningful directional information
  (a typical breakout plays out in 3–7 days), short enough to keep the
  noise floor below the directional signal. Longer horizons (≥ 21 days)
  push the noise-to-signal ratio toward unfavorable territory on daily
  bars; shorter horizons (≤ 2 days) underweight the path-dependence
  triple-barrier is meant to encode.

---

## Point-in-time rule (hard invariant)

> The label denominator (σ̂[t]) and barrier definitions at bar *t* must
> use only price data at bars ≤ *t*. The forward window
> (bars *t+1 .. t+horizon*) is consumed by the label *numerator* only.

Both shipped schemes honour this:

- `vol_scaled_returns` constructs `σ̂[t]` from
  `returns.rolling(vol_window).std()` evaluated at bar t — strictly
  trailing.
- `triple_barrier_labels` uses the same `σ̂[t]` to scale the barriers
  before the forward walk begins; the forward walk reads
  `prices[t+1 .. t+max_horizon]` but does not feed those back into the
  barrier definition.

Violations would silently leak future volatility into the label and
inflate measured edge — the most dangerous class of bug in this codebase.
The `tests/test_label_schemes.py::test_point_in_time_no_lookahead` test
encodes this invariant on synthetic series; do not weaken it.

---

## Triple-barrier purge handling

The label horizon used by purge is `config.max_horizon` (worst-case
time-out), not the actual fill bar. Real fills end earlier when PT or SL
is hit, so the purge is *conservative over-purge* — slightly more
training data is discarded than strictly necessary, but the leakage
control stays intact. Under-purging by actual fill would re-introduce
the look-ahead leak that purging exists to prevent. The trade-off is
documented here so a future agent does not "optimise" purge by hooking
into the actual fill bar and silently break invariant 1 in
`src/quant/backtest/CLAUDE.md`.

---

## How the ablation harness uses these schemes

```python
from quant.backtest.ablation import run_label_ablation
from quant.backtest.report import (
    ablation_composite_ranking,
    ablation_summary_table,
    format_ablation_report,
)
from quant.features.label_schemes import (
    LDP_DEFAULT,
    triple_barrier_labels,
    vol_scaled_returns,
)
from quant.features.labels import generate_labels

schemes = {
    "signed_returns": lambda p: generate_labels(p, horizon=1),
    "vol_scaled":     lambda p: vol_scaled_returns(p, horizon=1, vol_window=21),
    "triple_barrier": lambda p: triple_barrier_labels(p, config=LDP_DEFAULT),
}
results = run_label_ablation(
    label_schemes=schemes,
    model=gbm,
    features_by_symbol=features_by_sym,
    prices_by_symbol=prices_by_sym,
    train_window=504, test_window=63, step=63, embargo=3,
)
print(format_ablation_report(results, regime_labels))
```

The ranking is **balanced multi-regime Borda count**: each column in the
per-regime Sharpe table (`aggregate`, `qe_bull`, `covid`, `rate_cycle`)
is ranked 1 → N independently; the composite is the mean rank across
columns. No regime is weighted more than another. This is robust to
outlier Sharpe values (a single crisis-regime Sharpe of +5 cannot
dominate the ranking) and avoids choosing a per-regime weighting that
itself becomes a p-hacking knob.

---

## Update protocol

The defaults in this document are intended to be stable. To change them:

1. Open a PR that explains the new value, citing AFML §3.5 or a
   peer-reviewed source that supersedes it.
2. Re-run the full label ablation on the same panel and include the
   before/after composite Borda ranking in the PR.
3. Do **not** revise these defaults to make a scheme pass the gate —
   that is post-hoc tuning of the evaluation harness, which destroys the
   value of pre-commitment. The same discipline applies to the T1–T6
   thresholds and the VIX thresholds.

---

## References

- López de Prado, M. (2018). *Advances in Financial Machine Learning.*
  Wiley. (Chapter 3.5: The Triple-Barrier Method; Chapter 3.6:
  Meta-Labeling.)
- Bouchaud, J.-P., & Potters, M. (2003). *Theory of Financial Risk and
  Derivative Pricing.* Cambridge University Press. (Volatility-estimation
  chapter.)
- Diebold, F.X., & Mariano, R.S. (1995). Comparing Predictive Accuracy.
  *Journal of Business & Economic Statistics*, 13(3), 253–263. (Used by
  `ablation_dm_matrix`.)

---

*Sister documents:
[regime-evaluation.md](regime-evaluation.md),
[evaluation-standards.md](evaluation-standards.md),
[purging-and-embargo.md](purging-and-embargo.md).*
