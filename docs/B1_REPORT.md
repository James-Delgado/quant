# B1 — Target Reframing: Exit-Gate Report

> **Verdict: NO-GO.** No extractable edge from the M6 public feature set on **any**
> of the four pre-committed prediction targets, on the full 33-symbol × ~22-year
> panel across all three required regimes. This is a clean, pre-committed negative
> (METHODOLOGY §11) — it trips the B3/B4 conditional skip paths.
>
> **PRD**: [`.claude/prds/b1-target-reframing.prd.md`](../.claude/prds/b1-target-reframing.prd.md).
> **Gate function (source of truth)**: `backtest/regime_metrics.py::b1_gate_report`.
> **Runner**: `scripts/run_b1_arms.py`. **Verdict notebook**: `notebooks/11_b1_exit_gate.ipynb`.
> **Methodology**: [`docs/METHODOLOGY.md`](METHODOLOGY.md). The §20/§21 analog of
> [`PHASE_4A_REPORT.md`](PHASE_4A_REPORT.md).

## 1 · The question

Phase 4A proved next-bar (and 1-day signed) **return** is structurally unlearnable
from the current public feature set on the Dow-30+ETF sandbox
([`PHASE_4A_REPORT.md`](PHASE_4A_REPORT.md)). B1 held the feature set fixed and
varied only the **target**, asking whether a *different prediction object* —
drawdown risk, realized volatility, or a longer directional horizon — is more
learnable from the same information set. Four targets were pre-committed in
`features/targets.py` (`TARGET_CATALOG`), each with its metric, baseline,
materiality threshold, and deflation method pinned before any compute
(METHODOLOGY §1).

B1-M2's slice ablation (`notebooks/10_b1_target_ablation.ipynb`) was **provisional**:
the 2018-start slice had **zero `qe_bull` OOS bars**, so it spoke only to `covid`
+ `rate_cycle` and flagged one carry-forward candidate (`directional_21d`,
materiality edge in `covid` only). B1-M3 is the **confirmatory full-panel run**
(METHODOLOGY §11), which restores all three required regimes.

## 2 · The gate (verbatim — `b1_gate_report`)

A `(target, arm)` result clears the gate iff the **conjunction** of three stages
holds (the function is the source of truth; this prose describes it, METHODOLOGY §2):

1. **Materiality** — *every* criterion in `spec.materiality` met in ≥ `min_pass`
   (= 2) of the required regimes `(qe_bull, covid, rate_cycle)`. Directional
   targets (T3/T4) must clear **both** an AUC criterion (ΔAUC ≥ 0.02) and a Sharpe
   criterion (ΔSharpe ≥ 0.10); T1 is gated on ΔAUC ≥ 0.02, T2 on ΔMAE ≥ 5%
   relative reduction.
2. **Significance** — the paired stationary-block-bootstrap (21-day blocks) 90% CI
   of the gated-metric delta **excludes 0** in ≥ 1 required regime.
3. **Deflation** — deflated Sharpe > 0 (directional, Bailey–López de Prado) or the
   skill-z analog > 0 (T1/T2), with the deflation N read from the trial-count
   ledger. **N = 74** here (62 cumulative pre-B1-M3 trials + this matrix's pinned
   12 self-comparisons = 4 targets × 3 regimes).

All thresholds were pinned in `features/targets.py` / the B1 PRD before any arm
ran; changing one after a result is visible invalidates the run (METHODOLOGY §1).

## 3 · Result — no target clears

Full 33-symbol panel, 179,420 OOS rows × 5,481 dates, OOS span **2004-06-18 →
2026-03-31**, all three required regimes populated (qe_bull 83,026 / covid 16,665
/ rate_cycle 35,112 rows).

| Target | Metric | Materiality (regimes met / need 2) | Significance | Deflation | **GATE** |
|---|---|---:|:--:|---|:--:|
| `drawdown_21d` | ROC-AUC | **0 / 3** | True† | skill-z = **−5.88** (FAIL) | **FAIL** |
| `realized_vol_21d` | MAE | **1 / 3** | True | skill-z = **−31.86** (FAIL) | **FAIL** |
| `directional_5d` | AUC + Sharpe | **0 / 3** | True | DSR = **0.000** (FAIL) | **FAIL** |
| `directional_21d` | AUC + Sharpe | **0 / 3** | True | DSR = **0.009** (FAIL) | **FAIL** |

**Borda composite** (balanced across all three regimes, METHODOLOGY §10):
`realized_vol_21d` (12.0) > `directional_21d` (9.0) > `directional_5d` (5.0) >
`drawdown_21d` (4.0). The Borda *leader* still fails the gate outright — Borda
ranks the least-bad margin, not an edge.

> **† Why "Significance = True" is not evidence _for_ any target.** The gate's
> significance stage is **sign-agnostic** ("CI excludes 0"). For `drawdown_21d`
> and `realized_vol_21d` the CI excludes 0 in the **wrong direction** — the GBM is
> *significantly worse* than its baseline (e.g. drawdown AUC delta −0.110 / −0.162
> / −0.044 across regimes, every CI strictly below 0). Significance passing here
> means "reliably distinguishable," not "reliably better." **Materiality** is the
> directional stage, and it is the binding failure: the GBM does not beat its
> baseline by the pinned margin in ≥ 2 regimes for any target.

### 3.1 · The closest call: `directional_21d`

The one target whose *tradeable* arm looks alive on the surface — and the clearest
illustration of why the multi-criterion gate matters:

- **Aggregate** GBM Sharpe **+0.336** vs ARIMA **+0.063** — the GBM beats the
  ARIMA control on the headline number, and in `covid` the per-regime Sharpe is
  **+1.42** with a bootstrap CI `[0.39, 2.37]` that **excludes 0** (a genuine
  crisis-regime Sharpe signal), `rate_cycle` **+0.50** (CI includes 0).
- **But it ranks no better than chance.** The AUC delta vs the ARIMA-sign baseline
  is +0.016 / −0.072 / +0.008 across regimes — **below the 0.02 bar in every
  regime**, and negative in `covid`. Because a directional target must clear
  **both** AUC and Sharpe, materiality is met in **0** regimes despite the covid
  Sharpe.
- **And it does not survive deflation.** DSR = 0.009 ≪ 0.5 at N = 74: the observed
  Sharpe (0.336) is far below the expected best-of-74 no-skill benchmark (≈ 0.85).
  The aggregate edge is within what a 74-comparison search would surface by chance.

This is exactly the Phase 4A structural finding restored on a new target: *the GBM
finds a covid mean-reversion signal it cannot rank and cannot deflate away* — it
monetises in one regime but neither generalises (AUC) nor survives multiple-testing
correction. The slice's lone carry-forward candidate fails its confirmatory run.

### 3.2 · The other three

- **`realized_vol_21d`** (Borda leader) beats EWMA/ARIMA-on-log-vol only in
  `rate_cycle` (ΔMAE 0.056 ≥ 0.05, met) and is *worse* in `qe_bull`/`covid`;
  aggregate skill-z = **−31.86** — the GBM is decisively worse than a RiskMetrics
  EWMA persistence forecast at predicting 21-day realized vol. (EWMA aggregate MAE
  was already strong; a learned model adds nothing.)
- **`drawdown_21d`** GBM AUC is *below* the better of (climatology, vol-implied DD
  proxy) in **all three** regimes; the EWMA-vol-implied proxy alone ranks drawdown
  risk better than the 25-feature GBM (consistent with the slice finding,
  PRIORITIES `B1-DD-VOLIMPLIED-BASELINE`). Base rate P(>5% DD over 21 bars) = 0.46.
- **`directional_5d`** GBM Sharpe (0.017) is crushed by the ARIMA control (0.377);
  AUC ≈ 0.5 in every regime. The shorter horizon is, if anything, *more* dominated
  by the AR(1) trend prior than the 21-day horizon.

## 4 · Conditional skip-path determination (METHODOLOGY §5, binding)

The pre-committed verdict logic (B1 PRD §Sequencing notes):

- **Gate failed on all four targets** → the binding outcome is **"no extractable
  edge from this feature set on any of the four targets."** This:
  - **trips B3** (options-implied alternative data) — its conditional note in
    `PRIORITIES.yaml` activates (`B3-PRD` → `ready`). The constraint B1 isolates is
    the *information set* or the *universe*, not the target framing: four distinct
    prediction objects, three regimes, the full panel, and none beat a naive
    per-target baseline after deflation.
  - **B4** (universe shift) stays gated — drafted only if B1 **and** B3 both
    surface no edge.
  - A failed B1 **cannot** be revived by alternative justification without a new
    PRD (METHODOLOGY §5). This report is the pre-committed terminal verdict for the
    four B1 targets.
- **Phase-5 Trigger 1** (an ablation cell that clears its pre-committed gate)
  remains **unmet** — B1 produced no passing cell.

## 5 · Declared deviations (METHODOLOGY §9)

- **Significance stage is sign-agnostic.** As noted in §3, `significance_passed`
  is True for targets where the GBM is significantly *worse*. This is by design —
  the gate's conjunction (materiality **and** significance **and** deflation)
  fails correctly because the directional materiality stage fails. No target's
  verdict is affected; flagged so the table is not misread.
- **DSR `sharpe_std`** uses the pinned scalar default (`DEFAULT_SHARPE_STD = 0.35`)
  rather than an empirical cross-trial Sharpe dispersion — the open follow-up
  `A-DSR-LEDGER-SHARPE`. Bounded impact: the two directional DSRs (0.000, 0.009)
  are nowhere near the 0.5 threshold; no plausible `sharpe_std` flips them.
- **T1 drawdown baseline** is an EWMA-vol-implied DD *proxy* (a monotone vol-rank
  score), not a calibrated ARIMA-on-vol DD probability — the declared
  `B1-DD-VOLIMPLIED-BASELINE` deviation. It only *strengthens* the negative: the
  proxy already beats the GBM, so a better baseline widens the gap.
- **Initial alignment bug (fixed before the verdict run).** The first runner launch
  dropped all 33 symbols: the lake price index is `America/New_York` while
  `build_features` returns UTC, and a `tz_localize(None)` strip mis-aligned
  feature vs label indices by the UTC offset. Fixed with `_to_naive_utc`
  (tz_convert→strip); verified on real data before the verdict run. The smoke test
  could not catch it (synthetic panel is tz-naive). No bearing on the numbers above
  — they come from the corrected run.

## 6 · Reproducibility appendix

All four arms ran at git `c252279`, GBM `n_iter=50 / n_splits=3 / seed=0`, ARIMA
`(1,0,0)`, walk-forward `train=504 / test=63 / step=63 / embargo=3`, 25-column M6
feature set (`FINAL_FEATURE_COLUMNS`, parity-tested vs `run_phase4a_arms.py`).
Per-arm config hashes (the frozen run identity, `metadata.json`):

| Target | config_hash | horizon | OOS rows | elapsed | ledger verdict |
|---|---|---:|---:|---:|---|
| `drawdown_21d` | `649b764de208` | 21 | 179,420 | 1,604 s | gate_failed |
| `realized_vol_21d` | `f137b0391950` | 21 | 179,420 | 1,945 s | gate_failed |
| `directional_5d` | `670f763005e7` | 5 | 179,420 | 2,135 s | gate_failed |
| `directional_21d` | `592558f7f5ae` | 21 | 179,420 | 2,174 s | gate_failed |

Each arm appended one ledger entry (`prd=b1, milestone=B1-M3, n_comparisons=3,
verdict=gate_failed`) via `run_b1_arms.py --log-ledger`; cumulative trial N
advances 62 → 74. The verdict notebook
(`notebooks/11_b1_exit_gate.ipynb`) is checkpoint-only and re-derives the table
above from `data/b1/{target}/` in seconds, re-fitting nothing (METHODOLOGY §7).

---

*Status: FINAL — B1 target-reframing terminal verdict (2026-06-27). NO-GO; B3
activated, B4 stays gated, Phase-5 Trigger 1 unmet. Changing any pinned threshold
post-hoc requires a PRD revision and a new ledger entry, not an in-flight override.*
