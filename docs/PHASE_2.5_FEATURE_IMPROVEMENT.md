# Phase 2.5 — Feature Set Improvement

> **Spec document.** Depends on Phase 2 (`PHASE_2_MODELING.md`) and its
> backtesting harness. Phase 3 (`PHASE_3_SENTIMENT.md`) may not begin until
> the Phase 2.5 exit gate is cleared.

---

## Motivation

Phase 2 established the modeling infrastructure and ran honest exit-gate
evaluation. **Result: 2/6 gates passed (T2, T5 only).** OOS Sharpe = −0.833
for GBM, −0.227 for Ridge; both models are anti-predictive against the
always-long benchmark (Sharpe = 0.807).

Root-cause diagnosis:

1. **Mean-reversion feature bias.** Every price feature in Phase 2 (`rsi_14`,
   `mom_21d`, lagged returns) encodes a mean-reversion assumption. In the
   2021–2026 bull market these signals are systematically wrong — the model
   learns to fade a trend that keeps continuing. GBM's hit rate of 42% (vs 50%
   random) confirms the features are actively anti-predictive, not just noisy.

2. **No regime context.** The model cannot distinguish bull from bear market.
   A strategy that fades extended moves in a ranging market will destroy value
   in a trending one. Adding trend and regime features lets the model condition
   its bets on market structure.

3. **Limited training history.** With only 5 years of data (~1261 bars) and
   `TRAIN_W=200`, the walk-forward produces 19 folds over a single sustained
   bull run. That is insufficient for regime diversity. A 20-year history spans
   the dot-com crash, the GFC, the 2009–2021 bull, the 2022 bear, and the
   2023–2025 recovery.

4. **Thin universe.** Three tickers (AAPL, MSFT, SPY) is too narrow for
   cross-sectional learning. Expanding to the full DJIA 30 plus broad ETFs
   gives ~33 times more samples per fold without increasing the time span.

This phase addresses all four causes before advancing to Phase 3's LLM
sentiment signal.

---

## Entry gate (prerequisites)

- Phase 2 complete. All 169 tests pass. DGS10 NaN bug fixed (see
  `features/engineering.py` — `_load_fred_wide` applies `ffill()`).
- `scripts/validate_catalog.py` shows `[OK]` for all symbols with NaN ≤ 5%.

---

## Scope — what to build

1. **Expanded data history** — 20 years (2005–present) via `BACKFILL_YEARS=20`
   in `.env`.
2. **Expanded universe** — all 30 DJIA components plus SPY, QQQ, IWM.
3. **New price features** — trend, momentum, and regime indicators to
   complement the existing mean-reversion feature set.
4. **New macro features** — VIX (already ingested, not yet used as a feature)
   and yield-curve spread (derived from DGS10 and DFF already in the pipeline).
5. **Re-run Phase 2 notebook** — evaluate T1–T6 exit gates with improved data
   and features.
6. **Model interpretation notebook** — SHAP analysis, feature importances, and
   prediction decomposition to rationalize what the models are learning.

---

## Design detail

### A — Data expansion

#### History window

Set `BACKFILL_YEARS=20` in `.env` (see `.env.example`). The `tiingo_eod`
ingestor uses this setting; Tiingo's adjusted EOD data is confirmed available
back to at least 2005 for all DJIA components (verified by API test on
2005-01-03 AAPL data). FRED series (DGS10, DFF, VIXCLS) cover the full window.

**Alpaca (`equity_bars_daily`) is not the modeling data source.** Alpaca's
IEX free feed only goes back 5–7 years; `equity_bars_daily` is retained for
possible future intraday work but is not used in `build_features()` or the
backtester. **Tiingo adjusted EOD (`equity_eod_tiingo`) is the authoritative
price source** for all feature computation and labeling.

Re-run only the Tiingo and FRED ingestors in backfill mode:

```bash
.venv/bin/python -c "
from quant.ingest.tiingo_eod import ingest_tiingo_eod
ingest_tiingo_eod(backfill=True)
"
.venv/bin/python -c "
from quant.ingest.fred_macro import ingest_fred_macro
ingest_fred_macro(backfill=True)
"
```

Expected catalog after backfill: ≥ 5000 bars per symbol, 2005–present.
Validate with `scripts/validate_catalog.py`.

#### Universe

`config.py:equity_universe` updated to the full DJIA 30 (as of 2025) plus
SPY, QQQ, IWM — 33 symbols total.

| Category | Symbols |
|---|---|
| DJIA 30 | AAPL AMGN AMZN AXP BA CAT CRM CSCO CVX DIS GS HD HON IBM JNJ JPM KO MCD MMM MRK MSFT NKE NVDA PG SHW TRV UNH V VZ WMT |
| Broad-market ETFs | SPY QQQ IWM |

**Survivorship bias caveat.** All 33 symbols survived to 2025; back-testing
them over 20 years implicitly selects winners. Acceptable for Phase 2.5 feature
validation, but must be noted when interpreting results. A future phase should
use point-in-time index membership data.

---

### B — Feature engineering

All additions go into `src/quant/features/engineering.py`.

#### New price features — `_compute_price_features()`

Five new entries in the `feats` dict, following the existing pattern:

| Name | Formula | Rationale |
|---|---|---|
| `ret_252d` | `close.pct_change(252)` | Annual momentum — Jegadeesh & Titman (1993); most replicated return-predictability finding in finance |
| `ret_126d` | `close.pct_change(126)` | 6-month momentum — captures intermediate trend |
| `ma200_ratio` | `close / close.rolling(200).mean()` | Price vs 200-day SMA; below 1.0 = bear regime, above 1.0 = bull. Primary regime filter used by systematic trend-followers |
| `ma50_ratio` | `close / close.rolling(50).mean()` | 50-day trend; complements 200-day with a faster signal |
| `volume_ratio` | `volume / volume.rolling(63).mean()` | Relative volume — abnormal volume often precedes directional moves; normalises across symbols |

**NaN warmup note.** `ret_252d` and `ma200_ratio` require 252/200 bars of
history before producing valid values. With 20 years of data and `TRAIN_W=200`,
this costs approximately the first year of each symbol's history; folds start
no earlier than bar 252. Rows with any NaN are already dropped before passing
to `run_backtest()`.

**Existing features are kept.** `rsi_14`, `mom_21d`, lagged returns, and
volatility features remain. GBM can learn to condition them on the regime
features — e.g., "fade RSI when `ma200_ratio < 1.0`, follow through when
`ma200_ratio > 1.0`." This conditioning is the primary hypothesis of this phase.

#### New macro features — `_FRED_SERIES` and `_attach_fred_features()`

| Name | Source | Formula | Rationale |
|---|---|---|---|
| `vix` | `VIXCLS` (already ingested) | direct | Fear gauge — high VIX historically precedes positive risk premia; provides macro sentiment distinct from rates |
| `yield_curve` | `DGS10`, `DFF` (already loaded) | `DGS10 − DFF` | Term spread — inverted yield curve (negative) has preceded every US recession in 50 years; most cited macro leading indicator |

`VIXCLS` publishes daily on business days (same-day, no revision lag — same
class as DGS10 and DFF). Add it to `_FRED_SERIES`; the existing `ffill()` in
`_load_fred_wide` handles its weekend gaps correctly.

`yield_curve` is a derived column computed post-merge (one subtraction). Zero
additional data ingestion required.

**Total feature count: 10 → 17** (5 new price + VIXCLS + yield_curve).

---

### C — Re-run Phase 2 notebook

Re-execute `notebooks/02_phase2_modeling.ipynb` with:
- 20-year Tiingo history
- Expanded DJIA + ETF universe (update `PANEL_SYMS` to include 5–10 DJIA names)
- 17-feature matrix

Evaluate T1–T6. Document results honestly per the failure protocol in
`docs/concepts/evaluation-standards.md`. Update `CLAUDE.md` with the new
exit-gate result.

---

### D — Model interpretation notebook

New file: `notebooks/03_model_interpretation.ipynb`

This notebook is a **diagnostic tool only** — it does not affect exit gates.
One GBM is trained on the full dataset (no walk-forward) for interpretation
purposes. This is clearly labelled as in-sample and not used for performance
claims.

| # | Section | Purpose |
|---|---|---|
| 1 | Feature importances | Gain, cover, and frequency from XGBoost — three views that often disagree; disagreement is informative |
| 2 | SHAP summary | Beeswarm plot of global feature impact; shows direction and magnitude of each feature's contribution |
| 3 | SHAP dependence plots | Top 3 features: how does SHAP value vary with feature level? Reveals trend-following vs mean-reversion learned per feature |
| 4 | Prediction decomposition | Waterfall chart for 3 sample bars (one correct long, one correct short, one wrong); shows which features drove each prediction |
| 5 | Signal vs outcome scatter | Predicted return vs actual; colour-coded by correct/incorrect direction; reveals calibration |
| 6 | Rolling directional accuracy | 63-bar rolling hit rate over OOS period; identifies sub-periods where the model works or fails |
| 7 | Confusion matrix | Accuracy split by predicted sign; answers "is GBM wrong when going long or short?" |
| 8 | Ridge coefficients | Signed coefficients with inter-fold variability; Ridge is directly interpretable and provides a lower-complexity baseline |

Tooling: `xgboost` native SHAP (`model.get_booster().predict(shap=True)`),
`matplotlib`, `pandas`. No new dependencies required.

---

## Implementation order

```
Phase A — Data expansion
  A1. User sets BACKFILL_YEARS=20 in .env (see .env.example)
  A2. Run Tiingo backfill (33 symbols × 20 years)
  A3. Run FRED backfill
  A4. Validate: scripts/validate_catalog.py shows ≥ 5000 bars/symbol

Phase B — Feature engineering
  B1. Add 5 new price features to _compute_price_features()
  B2. Add VIXCLS to _FRED_SERIES
  B3. Add yield_curve derived column in _attach_fred_features()
  B4. Update tests: new column assertions + NaN-warmup checks
  B5. Confirm all tests pass (target ~175+)

Phase C — Re-run Phase 2 notebook
  C1. Update PANEL_SYMS in notebook to include more DJIA names
  C2. Execute notebooks/02_phase2_modeling.ipynb
  C3. Record T1–T6 results; update CLAUDE.md

Phase D — Interpretation notebook
  D1. Create notebooks/03_model_interpretation.ipynb
  D2. Render all 8 sections cleanly
  D3. Document key findings inline
```

---

## Deliverables

- `config.py` — updated `equity_universe` (DJIA 30 + ETFs) ✅
- `.env.example` — `BACKFILL_YEARS=20` documented ✅
- `.env` — `BACKFILL_YEARS=20` set by user
- `features/engineering.py` — 7 new features (5 price + VIXCLS + yield_curve)
- `tests/test_features.py` — tests for all 17 feature columns
- `notebooks/02_phase2_modeling.ipynb` — re-executed with new features and data
- `notebooks/03_model_interpretation.ipynb` — new interpretation notebook
- `CLAUDE.md` — updated feature count, test count, and Phase 2.5 exit gate result

Items marked ✅ are already complete.

---

## Exit gate (success criteria)

Phase 3 may begin after Phase 2.5 clears **at minimum T1 and T3**. If both
still fail after this phase, advance to Phase 3 per the failure protocol —
document honestly and test the LLM sentiment signal as an independent ablation.

| # | Criterion | Threshold |
|---|---|---|
| T1 | OOS Sharpe 95% CI lower bound | > 0.0 |
| T2 | IS/OOS Sharpe ratio | < 2.0 |
| T3 | GBM beats all six baselines net of costs | all six |
| T4 | Deflated Sharpe Ratio (N ≤ 50 configs) | > 0.5 |
| T5 | Diebold-Mariano p-value vs Ridge (one-sided) | < 0.10 |
| T6 | OOS max drawdown | > −25% |

---

## Risks and pitfalls

**Survivorship bias.** All 33 DJIA symbols survived to 2025. Any model trained
over 20 years partially learns "properties of long-term winners" rather than
generalizable signal. Note in results; future work should use a point-in-time
index-membership dataset.

**252-bar warmup reduces early folds.** With `TRAIN_W=200`, folds cannot start
until bar 252. On 20 years of data this costs ~1 year of history; ~4040 usable
bars remain across ~80 folds.

**More symbols ≠ more independent samples.** DJIA stocks are highly correlated
(same market cycle). Cross-sectional diversity improves per-fold sample counts
but does not provide regime diversity — that comes from the longer history.

**Tiingo rate limits.** The free tier allows ~50 requests/hour. With 33 symbols
and a 0.2 s throttle, a full 20-year backfill takes approximately 7 seconds of
API time but may hit daily request limits. The ingestor is idempotent — re-run
if rate-limited.

**Interpretation notebook is in-sample only.** SHAP values on the full dataset
reflect in-sample fit. Do not use them to make OOS performance claims.

---

## What comes next

Phase 3 adds an LLM-derived **sentiment feature** and tests via ablation
whether it measurably improves GBM over Ridge. Phase 2.5 provides the corrected
feature baseline against which the sentiment signal is ablated.

---

## Addendum — fair-comparison rerun on the Phase 3 universe (2026-06-07)

After Phase 3 expanded the panel (`PANEL_SYMS = settings.equity_universe`, 33
symbols) and adopted the union-of-indices `run_portfolio_backtest()`
(`docs/REFACTOR_PORTFOLIO_UNION_INDEX.md`), `02_phase2_modeling.ipynb` and
`03_model_interpretation.ipynb` were re-executed on the same data the
sentiment ablation uses. OOS span is now **2003-04-03 → 2026-04-21 across 116
walk-forward folds**.

**GBM (no sentiment) — head-to-head vs. baselines:**

| Model              | OOS Sharpe | Max DD       | Notes                          |
|--------------------|-----------:|-------------:|--------------------------------|
| Naive (always +1)  | **+0.704** | −42.60%      | unconditional long             |
| BuyAndHold         | **+0.704** | −42.60%      | identical to Naive             |
| ARIMA(1,0,0)       |     +0.434 | −39.98%      | mostly long in practice        |
| RandomWalk         |     +0.376 | −39.98%      | mostly long in practice        |
| **GBM (no sentiment)** | **−0.216** | **−567.66%** | margin-call simulator artifact |
| Ridge              |     −0.329 | −81.82%      | feature-based, mean-reverting  |
| Momentum           |     −0.339 | −67.04%      | trend-follower on `mom_21d`    |

GBM-without-sentiment beats only Ridge and Momentum; it loses to every
unconditional baseline. Gates: **2/6 pass (T2, T5)**; T1/T3/T4/T6 fail.

**Implication for Phase 2.5.** The earlier +0.487 Sharpe / 3 of 6 gates that
this spec helped produce was real on the 6-symbol post-2010 panel, but it did
not survive expansion to 25 years across 33 names. The 17-feature set's
directional bias (visible in `03_model_interpretation.ipynb` — macro features
DFF / yield_curve / DGS10 / VIXCLS now dominate SHAP, IS hit rate 65%) is
unfavorable on this broader universe.

**Comparison with Phase 3.** GBM + sentiment recovers to Sharpe +0.024 / MaxDD
−48.74% — better than the no-sentiment control, but still below the
unconditional baselines. See `docs/PHASE_3_SENTIMENT.md` addendum for the
ablation reading.
