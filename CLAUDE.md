# CLAUDE.md — quant project

## Project status

| Phase | Status | Commits |
|-------|--------|---------|
| Phase 0 — Data lake & ingestion | ✅ Complete | `7df86c1` |
| Phase 1 — Purged walk-forward backtester | ✅ Complete | `a456b84`, `6e735bf` |
| Phase 2 — Baseline infrastructure | ✅ Complete | `98061db`–`655b25a` |
| Phase 2 — GBM model + exit gates | ✅ Complete | see below |
| Phase 2.5 — Feature set improvement | ✅ Complete | see below |
| Phase 3 — LLM sentiment feature | ✅ Complete | `phase-3-sentiment` branch |
| Phase 4A — Feature/label redesign + regime-conditional eval | 🟡 In progress | Milestone 1 (regime harness) landed locally; see PRD + plan |

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

**Phase 4A — in progress.** The Phase 3 GBM does not beat ARIMA OOS, so the
Phase 4 entry gate (*"prototype shows a real, honest, cost-net edge"*) is
not met and Track A (transformers/foundation models) is deferred. Phase 4A
is a focused diagnostic subproject — feature/label redesign + regime-
conditional evaluation — that earns the right to Phase 4. PRD and plan:

- PRD: `.claude/prds/phase-4a-feature-and-label-redesign.prd.md`
- Milestone 1 plan: `.claude/plans/phase-4a-milestone-1-regime-harness.plan.md`
- Concept reference: `docs/concepts/regime-evaluation.md`

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
baseline. Milestones 2–6 still pending; gate not yet evaluated on real
data.

## Codebase map

```
src/quant/
├── config.py                 typed Settings (pydantic-settings), loads .env
├── storage/
│   ├── lake.py               write_raw / write_processed / read_processed
│   └── catalog.py            query(sql) / table(dataset) — DuckDB over Parquet
├── ingest/
│   ├── schemas.py            pandera schemas — OHLCV, FRED, TEXT_DOCUMENT, SENTIMENT_SCORED
│   ├── alpaca_bars.py        Alpaca daily OHLCV ingestor
│   ├── tiingo_eod.py         Tiingo adjusted EOD ingestor
│   ├── fred_macro.py         FRED macro series ingestor
│   ├── edgar.py              SEC EDGAR 8-K/10-K/10-Q ingestor → text_documents/ (Phase 3)
│   └── rss.py                RSS feed ingestor → text_documents/ (Phase 3)
├── flows/
│   └── daily.py              Prefect flow: runs all ingestors, isolates failures
├── features/
│   ├── labels.py             generate_labels() → LabelResult(series, horizon_bars)
│   ├── engineering.py        build_features() — 17 features + optional sentiment_df (19 cols)
│   ├── weights.py            compute_sample_weights() — López de Prado uniqueness weights
│   ├── finbert.py            FinBERT scorer — score_documents() → sentiment_scored/ (Phase 3)
│   └── sentiment.py          aggregate_sentiment() + validate_point_in_time() (Phase 3)
├── models/
│   ├── arima_baseline.py     ARIMABaseline — AR(1) on I(0) returns, single fit/fold
│   ├── buyandhold_baseline.py BuyAndHoldBaseline — always-long benchmark
│   └── gbm.py                GBMModel — XGBoost + RandomizedSearchCV(n_iter=50) inside walk-forward
├── backtest/
│   ├── walkforward.py        purged walk-forward split generator
│   ├── simulator.py          vectorised trade simulator (next-bar fills, costs)
│   ├── metrics.py            Sharpe / Sortino / Calmar / drawdown / hit-rate
│   ├── harness.py            run_backtest() / run_portfolio_backtest() / evaluate_panel()
│   ├── statistics.py         diebold_mariano() — DM test with HLN small-sample correction
│   ├── report.py             format_report() / summary_table() / print_report()
│   └── CLAUDE.md             agent instructions for the backtest package
└── utils/calendar.py         trading-day calendar (gap detection)
```

Key invariant: **purge + embargo leakage controls must stay intact in
`walkforward.py` and `harness.py`**. Read `docs/concepts/purging-and-embargo.md`
before touching split logic. The harness self-tests enforce this automatically.

## Python environment

The project uses a venv at `.venv/`. **Never use `source .venv/bin/activate`** —
it triggers a permission prompt every time. Call binaries directly:

```bash
.venv/bin/python   script.py
.venv/bin/pip      install package
.venv/bin/pytest   tests/
.venv/bin/jupyter  nbconvert ...
.venv/bin/ruff     check src/
```

The venv Python already has its site-packages on `sys.path`. Activation is a
shell convenience for interactive prompts only.

## Running things

```bash
# Full test suite (267 tests, ~52s, no network):
.venv/bin/pytest tests/ -v

# With coverage:
.venv/bin/pytest tests/ --cov=src --cov-report=term-missing

# Live API tests (requires .env credentials):
.venv/bin/pytest tests/ --integration

# Execute notebooks in place:
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=300 notebooks/01_system_tour.ipynb
# nb02 runs 6 baselines + one full GBM (n_iter=50) + DM walk-forward across
# the 33-symbol union panel × 116 folds — needs 3600s timeout:
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=3600 notebooks/02_phase2_modeling.ipynb
# Interpretation notebook trains IS GBM on ~196k rows (33 symbols × ~5000 bars)
# with n_iter=50, n_splits=3 — needs 5400s timeout:
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=5400 notebooks/03_model_interpretation.ipynb
# Phase 3 ablation (two full GBM runs + gate eval, 33-symbol union panel,
# ~116 folds × ~150 XGB fits/fold) — needs 3600s timeout (was 600s pre-refactor):
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=3600 notebooks/04_phase3_sentiment.ipynb

# Lint / format:
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format src/ tests/
```

## Notebook outputs and git

`nbstripout` is wired as a git filter (`.gitattributes`). Outputs are
automatically stripped on `git add`. Always commit notebooks before running
them so the clean baseline is preserved.

## Docs layout

```
docs/
├── ENV.md                          environment variables and runtime settings
├── CONTRIBUTING.md                 dev setup, test instructions, adding ingestors
├── PHASE_0_INFRASTRUCTURE.md       canonical project overview and architecture
├── PHASE_1_BACKTESTER.md           backtester spec (purged walk-forward CV)
├── PHASE_2_MODELING.md             Phase 2 spec (baselines done; GBM next)
├── PHASE_2.5_FEATURE_IMPROVEMENT.md Phase 2.5 spec (feature set improvement)
├── PHASE_3_SENTIMENT.md            Phase 3 spec (LLM sentiment feature)
├── REFACTOR_PORTFOLIO_UNION_INDEX.md  union-of-indices refactor of run_portfolio_backtest
└── concepts/
    ├── purging-and-embargo.md      deep-dive on leakage controls and embargo sizing
    ├── cost-model.md               trade simulator cost assumptions and sources
    ├── metrics-glossary.md         definitions for all reported performance metrics
    ├── feature-glossary.md         definitions and rationale for all 17 model features
    └── evaluation-standards.md     exit gate thresholds T1–T6 with statistical rationale
```

## Session logging (required)

A living session log lives at:
`~/.claude/projects/-Users-jamesdelgado-Projects-quant/sessions/YYYY-MM-DD.md`

**When to write:** at the end of any session where significant work was done,
OR when the context window is approaching its limit. Do NOT wait to be asked.

```markdown
## HH:MM UTC — [one-line goal]
**Goal:** What the session set out to accomplish
**Status:** Complete | In Progress | Blocked
**Commits:** short hash(es), or "none"
**Key changes:** bullet list of files or modules touched
**Summary:** 2-4 sentences on what was done and why
**Next:** What the next agent/session should do first
```

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the
Skill tool as your FIRST action.

- Product ideas, brainstorming → invoke office-hours
- Bugs, errors, "why is this broken" → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Architecture review → invoke plan-eng-review
