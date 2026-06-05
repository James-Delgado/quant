# CLAUDE.md — quant project

## Project status

| Phase | Status | Commits |
|-------|--------|---------|
| Phase 0 — Data lake & ingestion | ✅ Complete | `7df86c1` |
| Phase 1 — Purged walk-forward backtester | ✅ Complete | `a456b84`, `6e735bf` |
| Phase 2 — Baseline infrastructure | ✅ Complete | `98061db`–`655b25a` |
| Phase 2 — GBM model + exit gates | ✅ Complete | see below |
| Phase 2.5 — Feature set improvement | ✅ Complete | see below |

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
`backtest/harness.py` line 309); IS = 0.000 is expected, not a bug.

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

| Phase | Status | Commits |
|-------|--------|---------|
| Phase 3 — LLM sentiment feature | 🔜 Next | — |

## Codebase map

```
src/quant/
├── config.py                 typed Settings (pydantic-settings), loads .env
├── storage/
│   ├── lake.py               write_raw / write_processed / read_processed
│   └── catalog.py            query(sql) / table(dataset) — DuckDB over Parquet
├── ingest/
│   ├── schemas.py            pandera schemas for all three sources
│   ├── alpaca_bars.py        Alpaca daily OHLCV ingestor
│   ├── tiingo_eod.py         Tiingo adjusted EOD ingestor
│   └── fred_macro.py         FRED macro series ingestor
├── flows/
│   └── daily.py              Prefect flow: runs all ingestors, isolates failures
├── features/
│   ├── labels.py             generate_labels() → LabelResult(series, horizon_bars)
│   ├── engineering.py        build_features() — 13 price + 3 FRED + yield_curve = 17 features
│   └── weights.py            compute_sample_weights() — López de Prado uniqueness weights
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
# Full test suite (174 tests, ~46s, no network):
.venv/bin/pytest tests/ -v

# With coverage:
.venv/bin/pytest tests/ --cov=src --cov-report=term-missing

# Live API tests (requires .env credentials):
.venv/bin/pytest tests/ --integration

# Execute notebooks in place:
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=300 notebooks/01_system_tour.ipynb
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=600 notebooks/02_phase2_modeling.ipynb
# Interpretation notebook trains IS GBM on 28k rows — needs 600s timeout:
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=600 notebooks/03_model_interpretation.ipynb

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
