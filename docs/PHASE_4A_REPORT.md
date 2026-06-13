# Phase 4A — Exit-Gate Report and Go/No-Go for Track A

*Verdict computed in [`notebooks/09_phase4a_exit_gate.ipynb`](../notebooks/09_phase4a_exit_gate.ipynb)
from the four full-panel arm checkpoints in
[`data/phase4a/{arima,signed,vol_scaled,triple_barrier}/`](../data/phase4a/).
PRD: [`phase-4a-feature-and-label-redesign.prd.md`](../.claude/prds/phase-4a-feature-and-label-redesign.prd.md).
Plan: [`phase-4a-milestone-6-exit-gate.plan.md`](../.claude/plans/phase-4a-milestone-6-exit-gate.plan.md).*

## 1 — Verdict

**The Phase 4A exit gate FAILED. Track A (transformers / foundation models)
is DEFERRED.** All three GBM arms (signed_returns, vol_scaled,
triple_barrier) lose to the ARIMA(1,0,0) control in every PRD-required
regime (`qe_bull`, `covid`, `rate_cycle`) on the full 33-symbol × ~22-year
OOS panel (2004-06-20 → 2026-03-30, 5,394 bars, 87 walk-forward folds).
DM p-values for the signed and vol_scaled arms vs ARIMA are 1.0000 in
every required regime — ARIMA's forecast errors are *strictly* smaller.
No secondary arm clears the Bonferroni-adjusted bar; no per-regime claim
flips the go/no-go to "go." Phase 4A's diagnostic mission is complete:
the Phase 3 no-edge finding survives a label redesign, regime-aware
features, a corrected FRED join, and a regime-conditional evaluation.
Adding architectural complexity now would inherit the same defect.

## 2 — The gate, verbatim

### PRD success metric (quoted)

> *We'll know we're right when GBM Sharpe > ARIMA Sharpe in ≥ 2 of 3
> recent regimes (e.g., 2010–2019 QE bull, 2020–2021 COVID, 2022–2026
> rate cycle), with the Diebold-Mariano test rejecting equal-loss at
> p < 0.05 in at least one of those regimes.*
> — `phase-4a-feature-and-label-redesign.prd.md` § Hypothesis

### Pre-committed evaluation protocol (quoted from the M6 plan)

> 1. **Primary gate arm = GBM + `signed_returns`** — the scheme M2 kept
>    as default. The official Phase 4A gate verdict is computed on this
>    arm alone, at the standard DM α = 0.05.
> 2. **Secondary arms = GBM + `vol_scaled`, GBM + `triple_barrier`** —
>    reported as the label-scheme-under-GBM finding. Bonferroni-adjusted
>    significance bar (DM p < 0.05/3) for any secondary-arm gate claim;
>    only a secondary arm clearing the *adjusted* bar can flip the
>    go/no-go to "go."
> 3. **Control = ARIMA(1,0,0)** — one run; ARIMA forecasts returns
>    directly and is label-scheme-independent, so a single control
>    serves all three arms.
> 4. **DM error-unit contract — all DM inputs live in return space.**
>    The signed arm's forecast errors are natively in return space. The
>    vol_scaled arm's predictions are converted back to return space
>    *before* error computation, by multiplying by the same point-in-time
>    vol denominator used to scale its labels. The triple_barrier arm's
>    residuals are classification residuals and are **not** commensurable
>    with ARIMA's return errors: that arm reports Sharpe only; its DM
>    numbers appear in a caveated appendix and can never support a gate
>    claim.
> 5. **OOS index alignment.** The gate and every cross-arm table are
>    evaluated on the **intersection** of the four runs' `oos_returns`
>    indices; per-arm dropped-bar count reported.
> 6. **Sample-weight parity audit** recorded in the runner before the
>    arms ran.
> 7. These rules are fixed *before* any run starts.

### Protocol deviations honestly declared

The protocol item 4 vol_scaled "convert errors back to return space"
step was **not** performed in this report. The runner persisted
`oos_forecast_errors` in label-space (vol-scaled units) but did not
persist the per-bar `σ̂[t]` denominator alongside the checkpoint.
Recomputing `σ̂[t]` per (symbol, bar) over the 5,394-bar intersection
would require re-loading the lake. Since the **Sharpe-side** of the
vol_scaled gate fails in every required regime (Δ −1.24 / −1.97 /
−1.01 vs ARIMA — wider losses than the primary arm), the unit
correction cannot flip the verdict; we report vol_scaled's checkpoint
DM values verbatim and **flag that they are in vol-scaled units, not
return space**. Reproducibility appendix (§8) records the limitation.

### Sample-weight parity audit

López de Prado uniqueness weights (`features/weights.py::compute_sample_weights`)
depend only on `(n_samples, horizon)`, not on label values. `GBMModel.fit`
reads horizon from `self.label_horizon` (set at construction time).
`run_label_ablation` deep-copies one model per scheme but does **not**
update `self.label_horizon` — silently mis-weighting a scheme whose
horizon differs from the model's construction horizon (notably
`triple_barrier` h=5 paired with a model built for `signed_returns`
h=1). **The runner (`scripts/run_phase4a_arms.py`) sidesteps this by
construction**: each `--arm` invocation constructs a fresh
`GBMModel(label_horizon=<scheme_horizon>)` and calls
`run_portfolio_backtest` directly. Each arm's `metadata.json` pins the
audit verbatim under `sample_weight_parity_audit`.

## 3 — Evidence

### Primary gate: GBM(signed_returns) vs ARIMA(1,0,0)

| Regime | n_bars | GBM Sharpe | ARIMA Sharpe | ΔSharpe | GBM beats ARIMA? | DM p-value |
|---|---:|---:|---:|---:|:---:|---:|
| `pre_qe` (not gated) | 1,373 | −0.288 | −0.201 | −0.087 | no | 1.0000 |
| `qe_bull` (req) | 2,476 | −0.029 | +1.059 | **−1.088** | no | 1.0000 |
| `covid` (req) | 497 | −1.280 | +0.403 | **−1.683** | no | 1.0000 |
| `rate_cycle` (req) | 1,048 | −0.442 | +0.405 | **−0.847** | no | 1.0000 |

- `pass_count`: **0 of 3** (need ≥ 2)
- `significant DM`: **0 of 3** (need ≥ 1)
- `gate_passed`: **False**

### Secondary arm: GBM(vol_scaled) vs ARIMA(1,0,0) — Bonferroni α = 0.0167

| Regime | n_bars | GBM Sharpe | ARIMA Sharpe | ΔSharpe | beats? | DM p-value |
|---|---:|---:|---:|---:|:---:|---:|
| `pre_qe` | 1,373 | +0.058 | −0.201 | +0.259 | YES | 1.0000 |
| `qe_bull` (req) | 2,476 | −0.183 | +1.059 | −1.242 | no | 1.0000 |
| `covid` (req) | 497 | −1.564 | +0.403 | −1.967 | no | 1.0000 |
| `rate_cycle` (req) | 1,048 | −0.607 | +0.405 | −1.012 | no | 1.0000 |

- `pass_count`: **0 of 3 required**
- Bonferroni-significant DM (α = 0.0167): **0 of 3 required**
- Bonferroni gate: **FAILED**

### Secondary arm: GBM(triple_barrier) vs ARIMA(1,0,0) — Sharpe only

| Regime | n_bars | GBM Sharpe | ARIMA Sharpe | ΔSharpe | beats? |
|---|---:|---:|---:|---:|:---:|
| `pre_qe` | 1,373 | +0.474 | −0.201 | +0.675 | YES |
| `qe_bull` (req) | 2,476 | −0.215 | +1.059 | −1.274 | no |
| `covid` (req) | 497 | −1.140 | +0.403 | −1.543 | no |
| `rate_cycle` (req) | 1,048 | +0.322 | +0.405 | −0.083 | no |

- `pass_count` (Sharpe-only): **0 of 3 required**
- DM excluded by protocol item 4 → gate is **mechanically unreachable**
- Verdict: **FAILED**

### Cross-scheme GBM Borda composite — M2 re-test under GBM

| Scheme | aggregate | pre_qe | qe_bull | covid | rate_cycle | mean rank | composite |
|---|---:|---:|---:|---:|---:|---:|:---:|
| **triple_barrier** | +0.177 | +0.474 | −0.215 | −1.140 | +0.322 | 1.40 | **1** |
| signed_returns | −0.336 | −0.288 | −0.029 | −1.280 | −0.442 | 2.00 | 2 |
| vol_scaled | −0.339 | +0.058 | −0.183 | −1.564 | −0.607 | 2.60 | 3 |

**Cross-arm finding:** the M2 ARIMA-control verdict (5-symbol × 8-year
slice, composite winner `vol_scaled`) does **not** hold under GBM at
full panel. Under GBM, `triple_barrier` is the Borda winner — driven by
its sole strong regime, `pre_qe` (the 2004–2009 era outside the PRD's
qualifying window), and the only GBM arm with a positive aggregate
Sharpe. None of the three schemes wins in any of the PRD-required
eras: even the cross-arm winner is a verdict on *which scheme fails
least badly*, not which scheme produces edge.

### Aggregate Sharpe — all four arms

| Arm | Aggregate Sharpe | Aggregate Max DD (simulator-artifact; see §6) | Wall time |
|---|---:|---:|---:|
| ARIMA(1,0,0) | **+0.423** | −60.39% | 1,003 s |
| GBM signed | −0.336 | −74.86% | 1,446 s |
| GBM vol_scaled | −0.339 | −78.17% | 1,477 s |
| GBM triple_barrier | +0.177 | −64.74% | 1,453 s |

Sanity gate (M6 plan Task 3): the ARIMA aggregate +0.423 matches the
nb02 re-run on the Phase 3 union panel (+0.434, CLAUDE.md) within
|Δ| < 0.05 — the corrected-FRED M6 set-up reproduces the published
control.

## 4 — What Phase 4A changed

### M5 — FRED publication-lag leakage (`ef65256`)

Verdict: **leak confirmed + material.** Pre-M5 `build_features()`
merged FRED on the observation date; DFF is published next-business-day,
so a look-ahead leak entered every macro feature. The slice nb07
measured a sign-flip rate of 23.3% of OOS bars and |ΔSharpe| up to
0.38 per regime. The fix (`FRED_PUBLICATION_LAGS`: DGS10/DFF/VIXCLS
lagged 1 business day) is now the default for `build_features()`. **All
M6 arms in this report run under the corrected joins.** Numbers from
Phase 2.5 (+0.487 Sharpe on the 6-symbol post-2010 slice) and Phase 3
(+0.024 Sharpe with sentiment on the 33-symbol panel) were measured
under the leaked join and are no longer the reference; CLAUDE.md
records them with the leakage caveat. The leak does **not** explain
nb03's IS macro-feature dominance — IS skill survives the lag (DM
p=0.72 between leaked and lagged IS predictions) — so the IS-vs-OOS
puzzle re-attributes to feature instability / label misspecification,
not to leakage.

### M2 — label-scheme verdict (`893db9a`)

Slice verdict (5-symbol × 8-year, ARIMA control): no scheme alone
fixes `rate_cycle`; signed_returns stays as the default for the
primary gate arm. Full-panel re-test under GBM (this report, §3) is
consistent with M2's slice finding: `triple_barrier` leads the Borda
composite but loses every qualifying regime.

### M3 — regime-aware feature survivors (`d83e5cf`)

PRD M3 gate FAILED (2/3 qualifying, noise guard on). Survivors:
`xs_rank_vol_21d` (covid lift +0.636, CI90 [−0.01, 1.25],
sign-consistent) and `trend_regime` (rate_cycle lift +0.168,
sign-consistent). Both promoted into the M6 final feature set (25
columns total). The M3 finding that **SHAP-vs-ablation Spearman ρ =
−0.074** (IS importance does not transfer OOS on M3's features) was
the strongest internal signal that adding features could not, on its
own, rescue the model — and M6's full-panel result confirms it.

### M4 — feature catalog state (`397f68a`)

27 columns registered with 12 metadata fields each;
`tests/test_catalog.py` (14 tests) enforces drift in both directions.
At the end of M6, `xs_rank_vol_21d` and `trend_regime` (the M3
survivors that became part of the final feature set) retain their
`tested_edge` status — neither was *negatively* differentiated at full
panel; we have no marginal ablation that re-isolates them at full
panel (the M6 design ran scheme arms, not per-feature arms — the M3
plan's "promote only if M3 surfaces survivors" conditional was
exhausted by the M2 plus M3 logic). Updates in §8 of this report (the
write-back) reflect the no-edge **aggregate** truth without inventing
unmeasured per-feature numbers.

### M1 — regime harness (`af8d7da`)

Substrate: `phase4a_gate_report`, `DateRangeDetector`,
`compute_regime_metrics`, `regime_dm_test`. All M2–M6 verdicts run
through this substrate; the gate verdict in §3 is bit-for-bit what
`phase4a_gate_report` would have produced from `BacktestResult`-shaped
inputs (the notebook constructs `SimpleNamespace`s with
`oos_returns` + `oos_forecast_errors` and routes them through the
same `compute_regime_metrics` + `regime_dm_test` calls the gate
function uses internally — duck-typed equivalence).

## 5 — Interpretation

### Why ARIMA wins

ARIMA(1,0,0) fits a one-step AR coefficient to recent return history.
On a structurally trending universe like Dow 30 + ETFs over 22 years,
this is almost mechanically equivalent to a slow long bias plus
small-amplitude mean-reversion correction — which is approximately the
*right* directional prior for a market that compounds upward at
~6–8% per year with daily noise. ARIMA's `qe_bull` Sharpe of **+1.059**
(2,476 bars) is the empirical fingerprint: in the long bull regime,
the model that says "tomorrow's return is roughly today's return,
with a small AR(1) damping" prints money. The PRD's GBM is supposed
to learn at least this much — and more — but instead learns a
short-horizon mean-reversion signal that fights the trend.

### Why GBM signed_returns produces negative Sharpe

Per-regime: signed loses `qe_bull` by −1.088 Sharpe and `covid` by
−1.683. The GBM is trained on bars where short-horizon mean-reversion
is locally informative (high-vol crisis stretches dominate the
feature-variance signal that XGBoost greedy splits latch onto); it
then applies that learning to bars where the dominant short-horizon
dynamic is trend continuation. This is the trend-fighting failure
mode CLAUDE.md flagged in Phase 2.5 and Phase 3 — Phase 4A's M2 label
redesign was the empirical test of whether labels alone could fix it.
The answer here is *no*: vol-scaling the label preserves the
trend-fighting bias (Sharpe falls slightly, from −0.336 to −0.339),
because vol-scaling reshapes the loss surface without changing the
direction of the model's signal. triple_barrier's classification
target *does* change the signal (the model now learns "will this
trade hit PT before SL?") — which is why triple_barrier wins the
Borda — but at the cost of a label that is fundamentally about
event detection rather than return prediction; the per-trade quality
gain in `rate_cycle` (Δ −0.083, almost neutral) is paid for by
worse `qe_bull` and `covid` performance.

### Why removing the FRED leak made GBM worse

Pre-M5, the GBM appeared to extract signal from `DFF`, `DGS10`,
`VIXCLS`, and `yield_curve` (SHAP feature 1–4 in nb03). M5 confirmed
the leak was material on a slice (23.3% sign-flip rate). The leak
inflated apparent IS predictive power of the macro block — and
because XGBoost greedy splits are biased toward the higher-variance
columns, the macro block ate disproportionate split capacity.
Removing the leak (corrected joins now standard in `build_features()`)
means the macro block's *true* OOS contribution is what now bleeds
through, and that true contribution is negative net of costs. The
−0.336 M6 aggregate Sharpe vs the −0.216 Phase 3 17-feature aggregate
is consistent with this: corrected leakage + extra features = a
slightly worse aggregate, because the leak had been carrying part of
the apparent skill.

### Why nb03's puzzle persists

nb03 IS SHAP showed macro features dominant. nb04 OOS showed no
attributable edge to those macro features. M5 confirmed the IS-vs-OOS
gap is **not** explained by leakage alone (the IS dominance survives
the corrected join — see nb07 §6 "DM test on the IS predictions
before vs after the lag fix: p=0.72, indistinguishable"). M3's
Spearman ρ = −0.074 between IS SHAP and OOS ablation lift on the
new M3 features says the puzzle generalizes to non-macro features
too: the model's IS importance rankings do **not** predict its
OOS contributions, full stop. This is the structural finding M3 +
M5 + M6 jointly deliver: **the IS-vs-OOS asymmetry is the model
class's behaviour on this data, not a leakage artifact**.

## 6 — Decision

**Outcome: NO-GO for Track A** (transformers / time-series foundation
models). The PRD risk table is binary; "almost passes" = "does not
pass" applies here, and the result is not "almost" — it is a clean
failure with Sharpe deltas of −0.85 to −1.97 across the qualifying
regimes and DM p-values of 1.0 in every gate-eligible test. The
correct next move is not architectural complexity; it is to revisit
features, labels, data sources, or model class — per the PRD's own
risk-table direction.

### Three concrete candidate next directions

The evidence points to three orthogonal candidate workstreams. None
is a guaranteed yield, but each addresses a distinct part of the
diagnosed failure mode:

1. **Switch the target framing entirely.** The PRD already named this
   as the alternative ("new data sources or a fundamentally different
   label/target framing — *not* to Track A"). The evidence from §5
   suggests the GBM is learning a mean-reversion signal it can't
   monetize. A candidate target: **n-day directional with a
   trend-conditional loss** — e.g., predict `sign(ret_5d)` with sample
   weights that down-weight bars during `trend_regime=0` (regime-aware
   loss). This is meta-labeling generalized: the M2.5 sub-milestone was
   skipped because no primary arm produced edge; this proposal flips
   the dependency by *baking the regime conditioning into the loss
   function*, not into a downstream filter.

2. **Add a fundamentally new data source.** The 25-column M6 set is
   exhausted; the SHAP rankings from nb03 + M3 say the model has
   already greedily decided which features it values. Candidate
   adjacencies from the PRD's "out of scope" parking lot:
   **microstructure** (order-book depth, NBBO spreads — daily-bar
   approximations are doable from existing OHLCV), **alt-data** (Google
   Trends / X mentions / regulatory filings beyond the 8-K/10-K/10-Q
   already in Phase 3), or **flows** (13F + ETF creations/redemptions
   for systematic flow signal). These each require an ingestor and
   shouldn't be tested in isolation — pair them with the M2 vol_scaled
   labels for an apples-to-apples comparison against the M6 baseline.

3. **Different model class — abandon the GBM/transformer dichotomy.**
   Both GBM and transformers are *function-approximation* model
   classes; neither has a native treatment of the regime structure the
   evidence keeps pointing to. **Regime-conditional ensembling** with
   ARIMA as the in-trend default and a feature-based model active only
   in pre-specified regimes (`high_vol` or `vix_regime=2`) is the most
   defensible non-LLM next bet — the gate evidence in §3 says ARIMA is
   the right baseline almost everywhere; the productive question is
   *where* a feature-based model could attribute marginal edge, not
   whether one can replace ARIMA wholesale. **Bayesian state-space**
   models (Kalman + regime-switching) are a closely related family
   that may absorb the regime axis more naturally than a tree
   ensemble.

The user decision space at the end of Phase 4A is: pick (1), (2), or
(3) as the next PRD; Phase 5 (autonomous research agents,
`docs/PHASE_5_AGENTS.md`) is then either the *vehicle* for executing
the chosen direction (continuous-agent harness running ablation
matrices on whichever workstream) or remains a separate compound bet.

## 7 — Trials registry and deflation

Phase 4A ran approximately 62 effective per-regime comparisons across
M2–M6:

| Milestone | Comparisons | Detail |
|---|---:|---|
| M2 — label-scheme ablation | 12 | 3 schemes × 4 columns (aggregate + 3 eras) on ARIMA control slice |
| M3 — feature ablation | 30 | 7 candidates × 4 regimes (aggregate + 3 eras) on GBM slice, plus LOO spot-check on 2 survivors |
| M5 — FRED leakage A/B | 8 | leaked-vs-lagged × 4 regime axes (era + vol) on GBM slice |
| M6 — exit gate | 12 | 3 GBM arms × 4 columns on full panel |
| **Total** | **62** | |

**Deflated-Sharpe note.** Bailey & López de Prado (*Deflated Sharpe
Ratio*, 2014) propose that a researcher's observed best-Sharpe should
be deflated by the multiple-testing structure that produced it. For
N ≈ 62 trials and a per-trial Sharpe standard error on the order of
0.35 (the rough M3 noise-guard estimate), the expected best-of-N
draw under a no-skill null is approximately
`E[max_N(SE × Z)] ≈ 0.35 × √(2 ln 62) ≈ 0.71`. The best Phase 4A
GBM arm (`triple_barrier`, aggregate Sharpe +0.177) is below that
threshold. The Phase 2.5 T4 failure (DSR = 0.364, well below the
T4 = 0.50 minimum) recorded in CLAUDE.md is the structural precedent:
even when an aggregate Sharpe looked positive, the deflation
calculation said the headline number was indistinguishable from
random search noise. The same logic applies to anything we would
have promoted from §3 — `triple_barrier`'s aggregate edge does not
clear deflation, so the cross-arm Borda result is reportable as a
finding (M2 verdict does not hold under GBM) but not actionable as
a strategy claim.

The honest reading is that no Phase 4A arm produced a Sharpe that
both (a) cleared the pre-committed regime-gate and (b) would survive
deflation given the trial count. The bar for Phase 4A's exit was
deliberately set at *clearing the regime gate* rather than at
*surviving deflation*, because the gate was meant to be the
falsifiable pre-commitment. Both conditions fail here.

## 8 — Reproducibility appendix

### Config hashes (one per arm)

| Arm | SHA-256 (truncated) |
|---|---|
| arima | `f3b75332527b7b58e952522a1df093bd2dede78320b7a17747d995dcfe06fc49` |
| signed | `90e7cb484232a9df47c222f0bd624e7e6b39a58644da82ea5469e7917566fe9f` |
| vol_scaled | `b3bc413577a2a9476641d0987ef1504055f0bba4c7d7fa7ea7b080399776ac58` |
| triple_barrier | `1157463a5bdd048ece21ccbdf55d58b0a712357583936d8b0f0fc16512ba7e91` |

### Git SHA at run time

`397f68acc56c5fe146e4d61e18d5f6c3b976168e` — Milestone 4 (catalog) was
the last committed milestone at the time of the M6 arm runs. The M6
runner script (`scripts/run_phase4a_arms.py`) and this notebook +
report are committed by the orchestrator after this document lands.

### Checkpoint paths

- `data/phase4a/arima/{oos_returns.parquet, oos_forecast_errors.parquet, metadata.json}`
- `data/phase4a/signed/{oos_returns.parquet, oos_forecast_errors.parquet, metadata.json}`
- `data/phase4a/vol_scaled/{oos_returns.parquet, oos_forecast_errors.parquet, metadata.json}`
- `data/phase4a/triple_barrier/{oos_returns.parquet, oos_forecast_errors.parquet, metadata.json}`

### Run-time totals

- ARIMA: 1,003 s (~17 min)
- GBM signed: 1,446 s (~24 min)
- GBM vol_scaled: 1,477 s (~25 min)
- GBM triple_barrier: 1,453 s (~24 min)
- **Total compute: ~90 min** serial; checkpoint-load notebook runs in seconds.

### Final feature set (25 columns; order is contract-relevant for the hash)

`ret_1d, ret_5d, ret_21d, vol_21d, vol_63d, mom_21d, rsi_14, log_volume,
ret_252d, ret_126d, ma200_ratio, ma50_ratio, volume_ratio, DGS10, DFF,
VIXCLS, yield_curve, vix_regime, curve_inverted, vol_regime_ratio,
trend_regime, sentiment_score, doc_count, has_coverage, xs_rank_vol_21d`

### Walk-forward + simulator parameters (frozen across all four arms)

```
train_window:     504
test_window:      63
step:             63
embargo:          3
initial_capital:  100,000
commission/share: 0.005
slippage_bps:     5.0
FRED lags:        {DGS10: 1, DFF: 1, VIXCLS: 1}
GBM n_iter:       50
GBM n_splits:     3
GBM random_state: 0
ARIMA order:      (1, 0, 0)
sentiment lookback: 30 days
```

### Cross-references

- Sanity-gate vs nb02 re-run: this report's §3 (ARIMA aggregate
  +0.423 vs nb02 +0.434, |Δ| = 0.011, well inside noise).
- Pre-committed protocol section: `phase-4a-milestone-6-exit-gate.plan.md`
  § "Pre-committed evaluation protocol".
- M5 leakage forensics: nb07 §6–§8.
- M3 feature-ablation detail: nb08 §4–§5 (qualifying features +
  SHAP-vs-ablation Spearman).
- Max-DD simulator caveat: nb09 §7; nb04 has identical-class artifact
  for the no-sentiment GBM (−567% pre-refactor).
