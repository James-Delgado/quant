# Phase 0–4A delivery notes (historical)

> Moved out of `CLAUDE.md` (2026-06-26) to keep the entry point lean. These are
> the per-phase/-milestone delivery narratives and exit-gate numbers for Phases
> 1–4A. Current state lives in `CLAUDE.md` (status table) + `docs/PHASE_4A_REPORT.md`
> (Track A verdict) + `docs/PROJECT_ROADMAP.md` (what's next).

Phase 1 delivered: `walkforward.py`, `simulator.py`, `metrics.py`, `harness.py`,
`report.py`, 87-test suite, and an executed system-tour notebook at
`notebooks/01_system_tour.ipynb`.

Phase 2 delivered: `features/labels.py`, `features/engineering.py` (10-feature
matrix with FRED ASOF join), `features/weights.py` (sample uniqueness weighting,
López de Prado), `models/arima_baseline.py`, `models/buyandhold_baseline.py`,
`models/gbm.py` (XGBoost + RandomizedSearchCV(n_iter=50) + TimeSeriesSplit),
`backtest/statistics.py` (Diebold-Mariano with HLN correction),
`run_portfolio_backtest()` + `evaluate_panel()` in `harness.py`,
169-test suite (169 passed / 4 skipped), and an executed Phase 2 notebook at
`notebooks/02_phase2_modeling.ipynb`.

Exit gate result on real data (1261 bars/symbol, AAPL/MSFT/SPY, 2021–2026):
**2/6 gates pass (T2, T5).** OOS Sharpe = −0.833; GBM beats 0/6 baselines.
Both feature-based models (Ridge −0.227, GBM −0.833) are negative while
always-long (Sharpe 0.807) and Momentum (0.435) are positive — the current
feature set produces models that go against the trend on this bull-market universe.
IS Sharpe is intentionally not tracked in `run_portfolio_backtest` (see
the comment block above `oos_returns_parts` assembly in `backtest/harness.py`);
IS = 0.000 is expected, not a bug.

Phase 2.5 delivered: expanded `features/engineering.py` to **17 features** (added
`ret_252d`, `ret_126d`, `ma200_ratio`, `ma50_ratio`, `volume_ratio`, `VIXCLS`,
`yield_curve`); expanded universe to DJIA 30 + ETFs (33 symbols); expanded history
to 20 years (2006–2026, ~5027 bars/symbol); 174-test suite (174 passed / 4 skipped);
re-executed `notebooks/02_phase2_modeling.ipynb` with 6-symbol panel (AAPL, MSFT,
JPM, JNJ, V, SPY); created `notebooks/03_model_interpretation.ipynb`.

Exit gate result on real data (~4767 bars/symbol avg, 6-symbol panel, 2010–2026):
**3/6 gates pass (T1, T2, T5).** OOS Sharpe = +0.487; GBM beats 2/6 baselines
(Ridge −0.001, Momentum −0.246). Adding trend/regime/macro features lifted OOS
Sharpe from −0.833 to +0.487 and T1 CI lower bound turned positive (0.025).
Remaining failures: T3 (GBM still trails Naive/B&H/ARIMA in sustained bull market),
T4 (DSR=0.364 — fat-tailed OOS returns push excess kurtosis to 24; unfavorable
for DSR formula), T6 (max DD = −29.72%, just below the −25% threshold).

Decision: advancing to Phase 3 (LLM sentiment feature) per failure protocol —
T1 passes but T3 does not; document honestly and test sentiment as independent
ablation per `docs/concepts/evaluation-standards.md`.

> **M5 re-statement note (2026-06-12):** all Phase 2.5 numbers reflect
> unlagged FRED joins — a confirmed material look-ahead (see Milestone 5
> below). Corrected full-panel numbers land in M6; do not re-run nb02 before
> then.

**Phase 2.5 re-run on Phase 3 universe (2026-06-07).** `02_phase2_modeling.ipynb`
and `03_model_interpretation.ipynb` were re-executed with `PANEL_SYMS =
settings.equity_universe` (full Dow 30 + SPY/QQQ/IWM, 33 symbols) and the
union-of-indices harness so the GBM-vs-baseline comparison runs on the same data
as `04_phase3_sentiment.ipynb`. On the aligned panel — **OOS 2003-04-03 →
2026-04-21, 116 folds** — GBM OOS Sharpe = **−0.216**, Max DD = **−567.66%**
(margin-call simulator artifact, same as nb04 control), **2/6 gates pass (T2,
T5)**. GBM beats only 2/6 baselines (Ridge −0.329, Momentum −0.339); always-long
(+0.704), ARIMA(1,0,0) (+0.434), and RandomWalk (+0.376) outperform. nb02 GBM now
matches the nb04 "no-sentiment" arm bit-for-bit, so the +0.240 Sharpe and ~500 pp
drawdown lift in nb04 is attributable purely to the sentiment column. The earlier
+0.487 Sharpe result reflected the 6-symbol post-2010 sample; expanding to
25 years (including 2008) reveals the model's mean-reversion bias is not paid
for on the broader universe. nb03 IS diagnostics on the wider panel show macro
features (DFF, yield_curve, DGS10, VIXCLS) now dominating SHAP rankings and IS
hit rate at 65.2%.

Phase 3 delivered: `ingest/edgar.py` (SEC submissions API + 8-K/10-K/10-Q ingestor),
`features/finbert.py` (ProsusAI/finbert scorer with 512-token truncation, MPS support),
`features/sentiment.py` (lookback aggregation + `validate_point_in_time()` guard,
14,251 documents scored), extended `build_features()` to accept `sentiment_df` and
produce a 20-column matrix (17 base + sentiment_score + doc_count + has_coverage),
plus a **union-of-indices refactor of `run_portfolio_backtest()`** (see
`docs/REFACTOR_PORTFOLIO_UNION_INDEX.md`) so each symbol contributes whatever
history it has instead of being truncated to the panel intersection. 267-test
suite (263 passed / 4 skipped), and an executed Phase 3 notebook at
`notebooks/04_phase3_sentiment.ipynb`.

Exit gate result on real data (33-symbol Dow 30 + ETF panel, 116 folds,
**OOS 2003-04-03 → 2026-04-21** — pre-refactor was 2010-onward only):
**2/6 gates pass (T2, T5).** OOS Sharpe = −0.216 (control) → +0.024
(+ sentiment), Max DD = −567% (simulator artifact — no margin-call modeling)
→ −48.74%. The +0.240 Sharpe delta is the project's largest single-feature
lift, but neither arm clears T1/T3/T4/T6 on a 23-year OOS span that now
includes 2008-09. The dominant driver of the difference is the 2008 crisis:
SEC 8-K filing rate spikes during the crisis, FinBERT scores them strongly
negative, and the sentiment feature pushed enough late-2008 predictions
toward `sign(pred)=0` (flat) for the harness to avoid the catastrophic
short-stack loss the no-sentiment GBM took. See Section 9 of
`notebooks/04_phase3_sentiment.ipynb` for the full hypothesis.

> **M5 re-statement note (2026-06-12):** all Phase 3 numbers reflect
> unlagged FRED joins — a confirmed material look-ahead (see Milestone 5
> below). Corrected full-panel numbers land in M6; do not re-run nb04 before
> then.

**Phase 4A — complete (2026-06-13).** Diagnostic subproject — feature/label
redesign + regime-conditional evaluation — closed at Milestone 6 with a
binary, pre-committed exit gate. **Gate FAILED.** All three GBM arms
(signed_returns, vol_scaled, triple_barrier) lose to the ARIMA(1,0,0)
control in every PRD-required regime on the full 33-symbol × 22-year OOS
panel (2004-06-20 → 2026-03-30, 5,394 bars, 87 folds). Primary arm
ΔSharpe vs ARIMA: −1.088 in `qe_bull`, −1.683 in `covid`, −0.847 in
`rate_cycle`; DM p = 1.0000 in every required regime (ARIMA's errors are
strictly smaller). No secondary arm clears the Bonferroni bar (α=0.0167).
**Track A (transformers / foundation models) DEFERRED.** Full report:
[`docs/PHASE_4A_REPORT.md`](docs/PHASE_4A_REPORT.md) — verdict, evidence,
3 candidate "no-go next directions" (target reframing / new data sources /
regime-conditional ensembling), and a trials-registry / deflated-Sharpe
discussion (~62 effective comparisons across M2–M6). PRD and plan:

- PRD: `.claude/prds/phase-4a-feature-and-label-redesign.prd.md` (closed)
- Milestone 1 plan: `.claude/plans/phase-4a-milestone-1-regime-harness.plan.md`
- Milestone 2 plan: `.claude/plans/phase-4a-milestone-2-label-ablation.plan.md`
- Milestone 5 plan: `.claude/plans/phase-4a-milestone-5-fred-leakage.plan.md`
- Milestone 3 plan: `.claude/plans/phase-4a-milestone-3-regime-features.plan.md`
- Milestone 4 plan: `.claude/plans/phase-4a-milestone-4-feature-catalog.plan.md`
- Milestone 6 plan: `.claude/plans/phase-4a-milestone-6-exit-gate.plan.md`
- Concept references: `docs/concepts/regime-evaluation.md`,
  `docs/concepts/label-schemes.md`, `docs/concepts/fred-publication-lag.md`

Milestone 1 (rolling-window + regime-conditional evaluation harness)
delivered: extended `BacktestResult` with `oos_returns` and
`oos_forecast_errors` series; new `backtest/regimes.py` with
`RegimeDetector` Protocol + `VIXThresholdDetector` (volatility axis) +
`DateRangeDetector` (macro-era axis, defaults `qe_bull` / `covid` /
`rate_cycle`); new `backtest/regime_metrics.py` with
`compute_regime_metrics`, `regime_dm_test`, and `phase4a_gate_report`
(matching the PRD success metric exactly); per-regime reporting via
`format_regime_report` and `regime_summary_table` in `backtest/report.py`.
310-test suite (310 passed / 4 skipped) — 47 new tests vs. the Phase 3
baseline.

Milestone 2 (label-scheme ablation matrix) delivered: new
`features/label_schemes.py` with `vol_scaled_returns(prices, horizon,
vol_window)` and `triple_barrier_labels(prices, config)` (López de Prado
AFML §3.5) plus a frozen `TripleBarrierConfig` dataclass and
pre-committed `LDP_DEFAULT` (pt_sigma=2.0, sl_sigma=1.0, vol_window=21,
max_horizon=5); new `backtest/ablation.py` with `run_label_ablation`
mirroring `evaluate_panel`'s kwargs-discipline + per-scheme model
deepcopy; new ablation reporter (`ablation_summary_table`,
`ablation_composite_ranking` via balanced multi-regime Borda count,
`ablation_dm_matrix`, `format_ablation_report`) in `backtest/report.py`.
361-test suite (361 passed / 4 skipped) — 51 new tests vs. Milestone 1
(37 label_schemes + 14 ablation/reporter). nb06 ARIMA control on a
5-symbol × 8-year slice: composite Borda winner `vol_scaled` (mean rank
1.333), `rate_cycle` winner `signed_returns` (control), `triple_barrier`
last (Sharpe −0.479) — verdict on this slice is *"no scheme alone fixes
the `rate_cycle` failure regime"*; Milestone 3 (regime-aware features)
carries the work forward. Milestones 4 and 6 + conditional 2.5
(meta-labeling on M2 winner if one had emerged across all regimes) still
pending; gate not yet evaluated on real data.

Milestone 5 (FRED publication-lag leakage investigation — re-sequenced
ahead of M3, executed 2026-06-12) delivered: pinned
`FRED_PUBLICATION_LAGS = {"DGS10": 1, "DFF": 1, "VIXCLS": 1}` in
`features/engineering.py` and made the publication-lag-shifted ASOF join
the `build_features` default (`fred_publication_lags=None` reproduces the
legacy unlagged join bit-for-bit); fixed a session-timezone CAST artifact
in `_load_fred_wide`; new concept doc
`docs/concepts/fred-publication-lag.md` (ALFRED-verified lag evidence,
decision-time convention, update protocol); 376-test suite (376 passed /
4 skipped) — 15 new tests vs. Milestone 2. nb07 A/B on the 5-symbol ×
8-year slice (GBM preview, n_iter=10, identical rows/seeds across arms):
**verdict LEAK CONFIRMED + MATERIAL** — sign-flip fraction 23.3% of OOS
bars (pinned threshold 5%) and |ΔSharpe| 0.27 aggregate / 0.25 covid /
0.38 rate_cycle (threshold 0.1) all trip. All pre-fix numbers (Phase
2.5/3, nb02–nb06) are unreliable at the ±0.1 Sharpe granularity; M6's
full-panel runs use the corrected join and supersede them. Important
negative finding: the leak does **not** explain nb03's IS macro dominance
— macro-only IS hit-rate *improves* under the lag (56.7% → 59.4%), the
arms are statistically indistinguishable (DM p = 0.72), and SHAP top-5
rankings are stable (4/5 overlap, Spearman ρ = +0.93). The deltas read as
GBM model variance on day-shifted inputs, not lost predictive
information; the IS-dominant/OOS-absent puzzle re-attributes to feature
instability or label misspecification and hands to M3.

Milestone 3 (cross-sectional + regime-aware features + per-feature
ablation, executed 2026-06-13) delivered: new `features/cross_sectional.py`
with `add_cross_sectional_features()` producing same-date percentile-rank
columns (`xs_rank_ret_21d`, `xs_rank_ret_252d`, `xs_rank_vol_21d`) with a
`min_symbols` NaN rule and a no-mutation contract; four regime-indicator
columns appended in `build_features` (`vix_regime` reading thresholds from
`backtest/regimes.VIXThresholdDetector` dataclass defaults — single source
of truth, no re-typed numbers; `curve_inverted`; `vol_regime_ratio` with
0-denominator NaN guard; `trend_regime`); column order pinned by a
regression test (`mom_21d` at index 5 — nb02's `MomentumBaseline` positional
contract). New `backtest/ablation.run_feature_ablation()` mirroring
`run_label_ablation`'s deepcopy/kwargs discipline, plus helpers
`make_add_one_sets()` and `make_leave_one_out_sets()`. New
`backtest/statistics.bootstrap_sharpe_delta_ci()` — paired stationary block
bootstrap (21-day blocks, T1 convention) for the gate's noise guard. New
reporters in `backtest/report.py`: `feature_ablation_table` (per-regime
Sharpe delta vs baseline), `feature_ablation_gate` (PRD metric verbatim:
≥3 features, ≥0.1 lift, ≥1 regime; noise guard = paired-bootstrap 90% CI
excludes 0 OR cross-regime sign-consistency), `format_feature_ablation_report`.
432-test suite (432 passed / 4 skipped) — 71 new tests vs. Milestone 5.
nb08 add-one ablation on the 5-symbol × 8-year slice (GBM preview,
`n_iter=10`, `signed_returns` labels): **verdict PRD GATE FAILED (2/3
qualifying)** — survivors `xs_rank_vol_21d`, `trend_regime`; documented as
noise on this slice `xs_rank_ret_21d`, `xs_rank_ret_252d`, `vix_regime`,
`curve_inverted`, `vol_regime_ratio`. SHAP-vs-ablation Spearman ρ =
**−0.074** on the 7 candidates — IS importance does not transfer OOS on
this slice (the nb03 puzzle again, on the new candidates). Slice verdict
is **provisional**; the two survivors carry forward to M4 (catalog
registration) and M6 (full-panel re-evaluation), which is the
confirmatory test.

Milestone 4 (machine-readable feature catalog, executed 2026-06-13)
delivered: new `features/catalog.py` with a `FeatureRecord` pydantic
model (12 pre-committed fields, `extra="forbid"`), `load_catalog()`
(`yaml.safe_load` → schema validation → duplicate-name check →
`depends_on` referential integrity, every error names the offending
items), and `validate_catalog_coverage()` (two-way set comparison
between produced columns and registered names, raising with
`unregistered`/`phantom` lists). New `features/catalog.yaml` registers
all 27 maximal columns: 12 price + 1 volume (`log_volume`)
+ 3 macro + 1 macro_derived + 4 regime + 3 cross_sectional + 3
sentiment. Per-entry metadata captures `family`/`source`/`formula`/
`lookback_bars` (matched to the glossary warmup table)/
`publication_lag_days` (1 for the FRED-derived columns per M5's pinned
lags, 0 elsewhere)/`point_in_time_rule`/`added_phase`/`glossary_ref`/
`ablation_status`/`regime_notes`/`depends_on`. M3 survivors
(`trend_regime`, `xs_rank_vol_21d`) recorded as `tested_edge`; the five
M3 noise candidates as `tested_no_edge` with the nb08 best-regime lift
in `regime_notes`; the 20 columns M3 did not ablate (13 price + 1 volume
+ 3 macro + 1 macro_derived + 3 sentiment) keep `untested`. New
`tests/test_catalog.py` (14 tests, 446 / 4 skipped suite total)
exercises loader behaviour (duplicates, bad enums, dangling
`depends_on`, unknown top-level keys, unknown per-feature fields,
missing required fields), the drift-enforcement positive + negative
paths (builds a real maximal matrix offline and asserts
`set(produced) == set(catalog)`), and the glossary anchor check (every
`glossary_ref` resolves to a `### <name>` heading in
`feature-glossary.md`). Glossary updated with the catalog/glossary
division-of-labor note at the top and prose entries for the three
sentiment columns the registry now references.

**Rule for future agents and humans: new feature ⇒ glossary entry +
catalog entry + the drift test passes.** Adding a column without
registering it, or removing one without updating the YAML, fails CI by
naming the offender.

Milestone 6 (exit-gate report and go/no-go for Track A, executed
2026-06-13) delivered: new `scripts/run_phase4a_arms.py` — a headless
per-arm runner (`--arm {signed,vol_scaled,triple_barrier,arima}`) with
parquet checkpointing under `data/phase4a/{arm}/`, idempotent re-runs,
a `--smoke` synthetic-panel mode for plumbing tests, and a module
docstring quoting the pre-committed protocol verbatim (including the
sample-weight parity audit: the runner dispatches each scheme to
`run_portfolio_backtest` directly rather than `run_label_ablation` so
`GBMModel(label_horizon=<scheme>)` matches the scheme's true horizon).
Four full-panel arms (33 symbols, 25-column final feature set, corrected
FRED joins, identical walk-forward kwargs) wrote `oos_returns.parquet`,
`oos_forecast_errors.parquet`, and `metadata.json` per arm — total wall
time ~90 min. New `notebooks/09_phase4a_exit_gate.ipynb` (checkpoint-only,
no model fitting — pure load + align + verdict) computes the primary
gate via duck-typed `SimpleNamespace` shims of `BacktestResult` so the
same `compute_regime_metrics` + `regime_dm_test` calls the gate function
uses internally produce a bit-for-bit equivalent verdict. New
`docs/PHASE_4A_REPORT.md` — eight sections (verdict, gate verbatim with
protocol deviations honestly declared, evidence tables, what Phase 4A
changed across M2/M3/M4/M5, regime-by-regime interpretation, go/no-go +
3 candidate next directions, trials registry + deflated-Sharpe note,
reproducibility appendix). Catalog `ablation_status`/`regime_notes`
write-back: M3 survivors (`xs_rank_vol_21d`, `trend_regime`) carry the
nb08 slice-edge note plus an M6 full-panel re-statement clarifying that
the *aggregate* gate failed and the slice-level edges were not
re-isolated at full panel (no marginal per-feature ablation; the M6
design ran scheme arms, not feature arms). 467-test suite (467 passed
/ 4 skipped) holds; no library code changed in this milestone except
the catalog write-back.

**M6 aggregate result (33 symbols, 87 folds, OOS 2004-06-20 →
2026-03-30):** ARIMA control Sharpe **+0.423** (sanity-matched to nb02
re-run's +0.434, |Δ|=0.011); GBM signed **−0.336**, GBM vol_scaled
**−0.339**, GBM triple_barrier **+0.177**. Cross-scheme Borda composite
under GBM: triple_barrier wins (rank 1.4 mean across {aggregate, pre_qe,
qe_bull, covid, rate_cycle}) — M2's ARIMA-control verdict (vol_scaled
winner on the 5-symbol slice) does **not** hold under GBM at full panel.
None of the three GBM arms wins any PRD-required era. The Phase 4A
exit-gate verdict is unambiguous and the project's next move is the
PRD-stated alternative (revisit features / labels / data sources / model
class — *not* transformers). See `docs/PHASE_4A_REPORT.md` for the
written-up evidence, deflated-Sharpe discussion, and concrete next-step
proposals.

