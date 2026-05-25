# Cross-Validation Leakage Controls: Purging & Embargo

> **Conceptual reference.** Read this before implementing or modifying any
> walk-forward splitting, purging, embargo, or cross-validation logic (it
> primarily concerns `src/quant/backtest/`). It explains *why* the leakage
> controls exist and what invariants must hold. See `PHASE_1_BACKTESTER.md`
> for the broader backtester spec.

---

## TL;DR — invariants that must always hold

1. **Purge.** Drop any training sample whose label-evaluation window overlaps
   any test sample's label-evaluation window.
2. **Embargo.** Enforce an additional temporal gap between the training set
   and the test set, beyond label overlap.
3. **Embargo length** must be at least the larger of: (a) the maximum feature
   lookback window, and (b) the lag at which the label/feature autocorrelation
   decays into noise. Currently set as a fixed constant — see §4.
4. **Test folds must be long relative to** `label_horizon + embargo`. If they
   are not, the leakage controls discard most of the training data (§5).
5. **Purging and embargo apply to the backtest/CV only.** The production model
   is refit on all data with realized labels and uses no embargo (§5).
6. **Hyperparameter tuning happens *inside* each walk-forward training window**,
   never across the train/test boundary.

Violating 1–3 silently inflates backtested performance. Violating 4–6 either
wastes data or reintroduces leakage.

---

## 1. The problem: two distinct leakage channels

A labeled financial observation `i` has features `X_i` observed at time
`t_i,0`, and a label `y_i` evaluated over the interval `[t_i,0, t_i,1]` — the
**label horizon**. Naively splitting such data into train/test sets leaks
information through **two structurally different channels**:

- **Channel A — deterministic label overlap.** Two observations whose label
  windows overlap are functionals of a *shared* segment of the price path, so
  their labels are mechanically correlated.
- **Channel B — stochastic serial dependence.** Even with *disjoint* label
  windows, labels are correlated because the underlying process has memory
  (volatility clustering, slow-moving regime/liquidity state). Feature vectors
  add a second path: features are computed over trailing windows that can
  reach into the test period.

Purging addresses Channel A. The embargo addresses Channel B. They are not
redundant.

---

## 2. Purging — closing the deterministic channel

**Rule:** drop training sample `i` if `[t_i,0, t_i,1] ∩ [t_j,0, t_j,1] ≠ ∅`
for any test sample `j`.

This is a set-intersection test on label windows. It is *exact* for what it
targets: after purging, no surviving training label is a functional of any
price-path segment that also determines a test label.

It is **blind** to everything else — specifically, it cannot see:

- **Feature lookback.** `X_i` is rarely point-in-time; it is a functional of a
  trailing window `[t_i,0 − w, t_i,0]` (rolling volatility, moving averages,
  momentum, microstructure stats). Purging never inspects feature windows.
- **Serial correlation.** Purging is a 0/1 test on window overlap. It cannot
  see a covariance that is small but nonzero across a positive temporal gap.

---

## 3. Embargo — closing the residual serial-correlation channel

### Why purging is insufficient

After purging, the worst surviving train/test pair is a training observation
whose label window terminates exactly at the test boundary: temporal gap
`Δ = 0`, and `Cov(y_i, y_j) = γ(0⁺)` is maximal, where `γ` is the
autocovariance function of the label process. Purging kept this pair because
the windows do not *overlap* — but the labels are still correlated.

### The mechanism

The embargo imposes a **minimum temporal separation `h`** between any retained
training observation and the test set. For a monotonically decaying ACF:

```
sup over (i in train, j in test) | Cov(y_i, y_j) |  ≤  sup over (Δ ≥ h) |γ(Δ)|  =  |γ(h)|
```

For a strong-mixing process the same bound holds with dependence governed by
the mixing coefficient `α(h) → 0`. So the embargo is a **single knob that
drives the entire residual train–test dependence down to `|γ(h)|`** (or
`α(h)`), and `h` is chosen to make that negligible.

### Why this matters

The cross-validation estimate `R̂` of generalization error is unbiased *only*
when train and test are independent. Under dependence, `R̂` acquires a bias
whose leading term is monotone in the train–test dependence, and the sign is
**optimistic** — the model is scored against labels it has partial, correlated
foreknowledge of. The embargo does not improve the model; it removes a
**first-order bias from the measurement** of the model.

### Which side the embargo goes on

- In **strict walk-forward** (training entirely before the test fold), the
  embargo is the gap on the **pre-test** side; it addresses pre-test serial
  correlation.
- The **feature-lookback** sub-channel is **post-test**: a training sample
  *after* the test fold can have a feature window reaching *back* into it. It
  appears once training folds also lie after a test fold (k-fold / CPCV).
- The standard (López de Prado) embargo is one-sided/post-test, motivated by
  the feature channel. The pure serial-correlation component is **symmetric**;
  a fully rigorous treatment embargoes whichever side carries adjacent training
  data. Do not treat the one-sided convention as theoretically complete.

---

## 4. Choosing the embargo length

### Principle (the eventual target)

`h` should exceed the larger of:

- the **maximum feature lookback** `w` used by any feature — so a post-test
  training sample's feature window fully clears the test set; and
- the **decorrelation lag** of the label and feature processes — the lag at
  which their autocorrelation functions decay into the noise band. Estimate
  this from the data (sample ACF, or mixing-coefficient decay).

If the process had no memory and features were instantaneous, the correct
embargo would be zero. Its length is a *measurement* of how far the data's
memory extends.

### Current implementation status

> **Embargo length is currently a FIXED CONSTANT, not data-derived.**
> Set it conservatively: at least the maximum feature lookback window, plus a
> margin. Do **not** under-set it to save data — under-embargoing reintroduces
> the bias in §3. A fixed, generous embargo is the correct conservative
> default for now.

Deriving `h` dynamically from the estimated ACF / mixing rate is **future
work** (see §7). It is a refinement, not a prerequisite — a fixed conservative
constant is safe and correct; it merely costs some data efficiency.

---

## 5. The information cost

Purging and embargo discard data. The cost is real and quantifiable.

### Raw count

With sampling rate `ρ`, label horizon `H`, embargo `h`, each train/test
boundary removes `≈ ρ(H + h)` training observations. As a fraction of the
training data adjacent to a fold of length `n_test`:

```
f ≈ (H + h) / n_test
```

This is a **boundary effect** — it scales with horizon and embargo length, not
with dataset size `N`. Examples (daily data, `H = 10`, `h = 5`):

- Annual test fold (`n_test ≈ 252`): `f ≈ 6%` — benign.
- Monthly test fold (`n_test ≈ 21`): `f ≈ 71%` — catastrophic.

**Design rule:** never let a test fold be short relative to the label horizon.

### True information loss is much smaller than `f`

`f` counts samples; it overstates the loss, because the relevant quantity is
the information in the dropped block *conditional on what is retained*:

- The dropped sub-block adjacent to the **retained training set** is strongly
  correlated with it — near-duplicates, little marginal information.
- The dropped sub-block adjacent to the **test set** is correlated with the
  test set — that information is exactly what must not be used. Losing it is
  the objective, not a loss.
- Genuine, unrecoverable loss is only the **core** of the zone decorrelated
  from both sides, of width `≈ max(0, (H + h) − ℓ)`, where `ℓ` is the
  autocorrelation length. If `H + h ≲ ℓ`, that core is empty and essentially
  no genuine information is destroyed.

### It is the estimator that pays — not the deployed model

Purging and embargo are properties of the **evaluation procedure**. The model
deployed in production is refit on the entire history up to the present (up to
`H` ago — the limit of realized labels). There is no future test set for it to
leak from, hence nothing to purge or embargo. **The information cost does not
touch the production model's training set.**

The cost is paid entirely as: (i) a modest increase in the variance of `R̂`,
and (ii) a modest increase in the variance of hyperparameter selection. This
is trading a small rise in *estimator variance* for the elimination of a
first-order *estimator bias*. A biased estimate of generalization error is not
merely imprecise — it is directionally wrong and causes deployment of
strategies with no edge. The trade is essentially always correct.

**CPCV** (Combinatorial Purged Cross-Validation) reuses each observation
across many purged/embargoed train/test combinations, amortizing the per-split
losses and yielding a *distribution* of `R̂` with lower variance than a single
walk-forward path. It is, among other things, the remedy for the efficiency
lost to purging and embargo.

---

## 6. Implementation checklist

When writing or reviewing cross-validation code, confirm:

- [ ] Purging removes all training samples with label-window overlap into any
      test window.
- [ ] An embargo gap is applied between train and test (correct side: pre-test
      for walk-forward; both sides considered for CPCV).
- [ ] Embargo length ≥ maximum feature lookback used by any feature.
- [ ] Test fold length ≫ `label_horizon + embargo` (else flag the config).
- [ ] The label horizon used by purging matches the actual label definition
      (they are coupled — if the label definition changes, purging must too).
- [ ] Hyperparameter search runs inside the training window only.
- [ ] The production-refit path does **not** apply embargo and uses all data
      with realized labels.
- [ ] Harness self-tests still pass (random ⇒ ~0 edge; leaky ⇒ caught).

---

## 7. Future work

- **Data-derived embargo.** Replace the fixed embargo constant with a value
  estimated per-dataset from the sample ACF / mixing-coefficient decay of the
  features and labels. Refinement only; the fixed constant is safe meanwhile.
- **Two-sided embargo for CPCV.** Evaluate embargoing both sides of a test
  fold to fully cover the symmetric serial-correlation component.
- **Per-feature purging.** Purge on feature-lookback windows explicitly,
  rather than relying on the embargo to cover the feature channel.

---

## References

- M. López de Prado, *Advances in Financial Machine Learning* — purged
  k-fold CV, embargo, combinatorial purged CV, sample uniqueness, the Deflated
  Sharpe Ratio.
