# Candidate Prediction Targets (Project B1 — Target Reframing)

> **Living reference.** Companion to
> [`label-schemes.md`](label-schemes.md),
> [`evaluation-standards.md`](evaluation-standards.md), and
> [`regime-evaluation.md`](regime-evaluation.md). This document defines the
> four B1 candidate prediction *targets*, their point-in-time rules, and the
> pre-committed materiality thresholds they are gated on. The **source of
> truth is the code** (`src/quant/features/targets.py`); this doc describes
> what is pinned there and must not introduce a second set of numbers. Update
> it when a target is added or a pin is revised *in code* — never retune a
> threshold here to make a target pass the gate.

---

## Why reframe the target

Phase 4A proved, with a pre-committed binary gate, that **next-bar (and
1-day signed) return is structurally unlearnable** from the current public
feature set on the Dow-30 + SPY/QQQ/IWM sandbox. Across three label schemes
(`signed`, `vol_scaled`, `triple_barrier` — see
[`label-schemes.md`](label-schemes.md)), regime-aware features, a corrected
FRED publication-lag join, and regime-conditional evaluation, no GBM arm
beat ARIMA(1,0,0) in any required regime, with Diebold-Mariano p = 1.0000
everywhere (`docs/PHASE_4A_REPORT.md` §3).

The unresolved question is **whether the failure is the universe, the
feature set, or the target.** B1 isolates the **target** axis. Phase 4A held
the target essentially fixed — all three label schemes are transforms of
*return* (sign, vol-scaled magnitude, or a barrier-touch event on the return
path) — and varied features and labels. It never asked whether a *different
prediction object* — drawdown risk, realized volatility, or a longer
directional horizon — is more learnable from the same information set.

Two priors motivate the specific choices:

1. **Volatility and drawdown are more autocorrelated and more
   regime-structured than return sign.** Realized vol clusters (ARCH
   effects); drawdown risk is conditionally predictable from vol state.
2. **Direction at a *longer* horizon may carry more signal than next-bar
   sign**, where microstructure noise dominates.

If B1 surfaces no edge on any of the four targets, that is itself a
high-value negative: it argues the binding constraint is the *information
set* (→ B3 alternative data) or the *universe* (→ B4), not the target
framing — established without spending an alt-data ingestor budget first.

This is the same **"materiality before significance, then deflation"**
discipline Phase 4A used (`evaluation-standards.md`), applied to four new
prediction objects rather than three return-transform labels.

---

## The four targets

Each target is a point-in-time label series produced by a function in
`targets.py`, plus a frozen `TargetSpec` record in the in-code
`TARGET_CATALOG`. All four are dispatched through the single entry point
`make_target_labels(target_id, prices)`, which always supplies the pinned
horizon so the ablation can never drift a horizon away from
`TargetSpec.horizon_bars` (the purge-coupling invariant, below).

### T1 — `drawdown_21d` (binary classification)

```python
drawdown_event_labels(prices, horizon=21, dd_threshold=0.05)
```

`P(max drawdown > 5% over the next 21 bars)`. For each bar `t` the forward
path is `prices[t .. t+horizon]` (entry plus `horizon` forward bars). The
label is `1.0` if the maximum peak-to-trough decline along that path —
measured against the running peak *from the entry* — meets or exceeds
`dd_threshold` in magnitude, else `0.0`. The running peak includes the entry
price, so a path that only ever rises is labelled `0`. The last `horizon`
bars cannot form a full forward window and are `NaN`.

- **Primary metric:** ROC-AUC.
- **Baseline:** climatology base-rate predictor + ARIMA-vol-implied DD
  probability (better of).
- **Pinned materiality:** `ΔAUC ≥ 0.02` (absolute) vs the better baseline
  (`DELTA_AUC_MATERIALITY`).
- **Why AUC, not accuracy:** the drawdown label is imbalanced; accuracy is
  base-rate-sensitive and uninformative. The base rate is reported per
  regime.

### T2 — `realized_vol_21d` (regression)

```python
realized_vol_labels(prices, horizon=21)
```

At bar `t` the label is `log(std(r_{t+1} .. r_{t+horizon}))` where
`r_s = prices[s]/prices[s-1] - 1` and the standard deviation uses `ddof=1`.
**Log**-vol is variance-stabilising and the standard regression target in
the volatility-forecasting literature; the window is non-annualised
(annualisation is a constant additive shift in log space and so does not
move MAE deltas vs a baseline). A forward window of constant prices has zero
realised vol and an undefined log, which **raises** rather than emitting
`-inf` (mirroring `label_schemes.vol_scaled_returns`). The last `horizon`
bars are `NaN`.

- **Primary metric:** MAE on log realized vol.
- **Baseline:** EWMA(λ=0.94) vol forecast + ARIMA-on-log-vol (better of).
- **Pinned materiality:** `ΔMAE ≥ 5%` relative reduction vs the better
  baseline (`REL_MAE_REDUCTION_MATERIALITY`).
- **What it is not:** not a Sharpe claim. T2's edge is *forecast skill vs
  EWMA/ARIMA-on-vol*; it feeds C4 (confidence) and C3 (vol-targeted sizing),
  not a direction, and is not promoted to a strategy without a downstream
  consumer.

### T3 / T4 — `directional_5d` / `directional_21d` (binary classification + Sharpe)

```python
directional_labels(prices, horizon=5)   # T3
directional_labels(prices, horizon=21)  # T4
```

`1.0` if the `horizon`-bar forward return is positive, else `0.0`
(down-or-flat → 0), so the label is ROC-AUC-scorable. The tradeable Sharpe
arm is *not* built in this module: in B1-M2 a model's `sign(pred)` is routed
through the existing simulator separately. The last `horizon` bars are
`NaN`.

- **Primary metric:** ROC-AUC **and** the tradeable Sharpe of `sign(pred)`.
- **Baseline:** majority-class + ARIMA(1,0,0) `horizon`-bar sign (better of
  for AUC; ARIMA for Sharpe).
- **Pinned materiality:** `ΔAUC ≥ 0.02` **and** `ΔSharpe ≥ 0.10` vs ARIMA
  (`DELTA_AUC_MATERIALITY`, `DELTA_SHARPE_MATERIALITY`). A directional target
  must clear **both** to count as an edge — a model that is statistically
  more accurate but not profitably tradeable does not pass.

> The directional-Sharpe-arm sign convention (`sign(pred − 0.5)` for a
> probability output vs the harness's `sign(pred)` at 0) and the OOS
> prediction collector live in `backtest/target_eval.py`; they are documented
> in [`target-evaluation.md`](target-evaluation.md), not here.

---

## Point-in-time invariant (hard)

> Every label at bar `t` is a function of prices at bars **> t** only (the
> forward window). Features never peek into that window.

The horizon constant is handed to `run_backtest` via
`LabelResult.horizon_bars`, so purging/embargo **over-purge by the label
horizon, never under-purge** (`backtest/CLAUDE.md` invariants 1–4). This is
the same conservative-over-purge discipline `triple_barrier_labels` uses with
its `max_horizon`. Under-purging would re-introduce the look-ahead leak that
purging exists to prevent — the most dangerous class of bug in this codebase.

### Horizon ↔ embargo coupling caveat

The new 5- and 21-bar horizons **exceed Phase 4A's 1-bar label**, so the
purge/embargo footprint is materially larger. `backtest/CLAUDE.md`
invariant 4 (test-fold length ≫ `label_horizon + embargo`) must be
re-checked against the **real slice config** rather than silently shrinking
the training set. This is a B1-M2 obligation (the matrix re-derives the
embargo from each target's `label_horizon` and asserts invariants 3–4 on the
real slice); `targets.py` produces the labels but runs no backtest, so it
cannot assert it alone.

---

## Deflation method per target (`dsr` vs `skill_z`)

Stage 3 of the gate deflates the per-target verdict against the cumulative
trial count (`quant.ledger.cumulative_trial_count()`, the A-DSR-GATE
deliverable). Which deflation applies depends on whether the target produces
a tradeable return series:

| Target | `deflation` | Rationale |
|---|---|---|
| `directional_5d`, `directional_21d` | `dsr` | A `sign(pred)` strategy has a return series → Bailey-López de Prado (2014) Deflated Sharpe Ratio applies on the Sharpe arm. |
| `realized_vol_21d` | `skill_z` | No tradeable return series → a forecast-skill z-score analog (`z = mean(skill)/se(skill)`, pass if `> 0`). |
| `drawdown_21d` | `skill_z` | A drawdown *probability* has no return series either. |

### The T1 deflation deviation (declared, METHODOLOGY §9)

The B1 PRD's T1 row literally reads **"DSR > 0"**. But a drawdown
*probability* target has no return series, so DSR is not literally
computable on it. `targets.py` therefore sets
`TARGET_CATALOG["drawdown_21d"].deflation = "skill_z"` — the same skill-z
analog the PRD prose explicitly grants the vol target (T2). This is recorded
as a B1-M1 finding to confirm in B1-M2: if the method choice changes the
gate, it requires a **new ledger entry**, not a mid-matrix override
(METHODOLOGY §1). The skill-z analog itself is documented in
[`target-evaluation.md`](target-evaluation.md).

---

## Pinned thresholds (METHODOLOGY §1)

All materiality cut-offs are pinned as named constants in `targets.py`
**before any compute touched B1**, and are reproduced verbatim into each
`TargetSpec.materiality`. They are consumed by
`backtest.regime_metrics.b1_gate_report` — the gate is the source of the
verdict, the catalog declares the spec.

| Constant (`targets.py`) | Value | Applies to |
|---|---|---|
| `DRAWDOWN_THRESHOLD` | `0.05` | T1 drawdown event magnitude |
| `DRAWDOWN_HORIZON` | `21` | T1 forward window (bars) |
| `VOL_HORIZON` | `21` | T2 forward window (bars) |
| `DIRECTIONAL_HORIZON_SHORT` | `5` | T3 forward window (bars) |
| `DIRECTIONAL_HORIZON_LONG` | `21` | T4 forward window (bars) |
| `DELTA_AUC_MATERIALITY` | `0.02` | classification ΔROC-AUC (absolute) |
| `REL_MAE_REDUCTION_MATERIALITY` | `0.05` | regression relative MAE reduction |
| `DELTA_SHARPE_MATERIALITY` | `0.10` | directional Sharpe-arm ΔSharpe |

Changing any of these after a result is visible invalidates the run and
requires a new ledger entry (METHODOLOGY §1).

---

## Flat module vs registered catalog (the B1-M1 design fork)

`targets.py` is a **flat module with an in-code structured registry**
(`TARGET_CATALOG` of frozen `TargetSpec` records), deliberately *not* a
`targets.yaml` + loader + drift-test in the shape of
`features/catalog.{py,yaml}`.

The fork was decided with the eventual Phase-5 Research agent in mind. That
agent — which would add candidate targets autonomously — needs the catalog
form to add targets *safely* under a bidirectional drift test (METHODOLOGY
§4 contract-before-consumer + §6 drift in both directions), exactly as M4
did for features. But for B1's four hand-picked, PRD-pinned targets a YAML
registry adds **no safety** (YAGNI), and that agent is gated and unbuilt.

The decision: keep it flat, but make the `TargetSpec` dataclass carry every
field a `targets.yaml` entry would hold — `target_type`, `horizon_bars`,
`primary_metric`, `materiality` criteria, `baseline_desc`, `deflation`. The
flat→catalog migration, when Phase 5 activates, is then **mechanical and
reversible**. This doc is the human-readable half of that future catalog;
the migration note lives in the `targets.py` module docstring.

---

## Update protocol

The targets and their pinned thresholds are intended to be stable. To change
one:

1. Open a PR that edits the constant **in `targets.py`** (the source of
   truth) and explains the new value with a rationale or citation.
2. Re-run the affected B1 ablation arm and record a new ledger entry — the
   deflation N must reflect the additional trial (METHODOLOGY §1, §12).
3. Update this document to match the code. Do **not** revise a value *here*
   to make a target pass the gate — that is post-hoc tuning of the
   evaluation harness. The same discipline applies to the T1–T6 gates in
   [`evaluation-standards.md`](evaluation-standards.md) and the label-scheme
   defaults in [`label-schemes.md`](label-schemes.md).

---

## References

- **Code:** `src/quant/features/targets.py` (label functions +
  `TARGET_CATALOG`), `src/quant/backtest/regime_metrics.py`
  (`b1_gate_report`), `src/quant/backtest/target_eval.py` (OOS-prediction
  collector + skill-z).
- **PRD:** `.claude/prds/b1-target-reframing.prd.md` (§Success Metrics pins
  the four targets and thresholds).
- Bailey, D.H., & López de Prado, M. (2014). The Deflated Sharpe Ratio.
  *Journal of Portfolio Management*, 40(5). (DSR deflation for the
  directional Sharpe arms.)
- López de Prado, M. (2018). *Advances in Financial Machine Learning.*
  Wiley. (Triple-barrier and labelling discipline carried over from
  Phase 4A.)

---

*Sister documents:
[label-schemes.md](label-schemes.md),
[target-evaluation.md](target-evaluation.md),
[evaluation-standards.md](evaluation-standards.md),
[regime-evaluation.md](regime-evaluation.md),
[purging-and-embargo.md](purging-and-embargo.md).*
