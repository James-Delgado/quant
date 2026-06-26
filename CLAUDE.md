# CLAUDE.md — quant project

> **Read this first — entry point for any agent or new contributor:**
> 1. [`docs/AGENT_OPERATION.md`](docs/AGENT_OPERATION.md) — **the standard operating procedure**. If you're picking up a task, this tells you the exact 11-step procedure to follow. The user's default prompt is "pick up the next ready task from `docs/PRIORITIES.yaml`" — this doc is the rest of the instructions.
> 2. [`docs/PROJECT_ROADMAP.md`](docs/PROJECT_ROADMAP.md) — what we're building, the post-4A portfolio (Projects A/B/C/D), ratified decisions.
> 3. [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) — binding methodology + engineering contract (rules 1–20). Read this before writing any code or running any experiment.
> 4. [`docs/PRIORITIES.yaml`](docs/PRIORITIES.yaml) — living task backlog. The next agent action is the lowest-`rank` task with `status: ready`. As of 2026-06-17 that is `A-LEDGER`.
> 5. Completed phase docs (Phase 0–3, Phase 4 spec, refactor docs) live in [`docs/historical/`](docs/historical/).

## Project status

| Phase | Status | Commits |
|-------|--------|---------|
| Phase 0 — Data lake & ingestion | ✅ Complete | `7df86c1` |
| Phase 1 — Purged walk-forward backtester | ✅ Complete | `a456b84`, `6e735bf` |
| Phase 2 — Baseline infrastructure | ✅ Complete | `98061db`–`655b25a` |
| Phase 2 — GBM model + exit gates | ✅ Complete | see below |
| Phase 2.5 — Feature set improvement | ✅ Complete | see below |
| Phase 3 — LLM sentiment feature | ✅ Complete | `phase-3-sentiment` branch |
| Phase 4A — Feature/label redesign + regime-conditional eval | ✅ Complete — **gate FAILED**, Track A deferred | M1 `af8d7da` → M2 `893db9a` → M5 `ef65256` → M3 `d83e5cf` → M4 `397f68a` → M6 `bc40044`; verdict in `docs/PHASE_4A_REPORT.md`; methodology lessons in `docs/PHASE_4A_RETROSPECTIVE.md` |

### Post-Phase 4A portfolio (ratified 2026-06-17)

Roadmap: [`docs/PROJECT_ROADMAP.md`](docs/PROJECT_ROADMAP.md). Backlog: [`docs/PRIORITIES.yaml`](docs/PRIORITIES.yaml).

| Project | Status | Reference |
|---|---|---|
| **Project A** — Research substrate & methodology | ✅ Done / maintain. Future work: trial-count ledger (`A-LEDGER`), DSR-aware gates, OOS-attribution (B2). | `docs/PROJECT_ROADMAP.md` §4 + `docs/METHODOLOGY.md` |
| **Project B** — Predictive research (post-4A) | 🟡 Active. B1 target reframing (4 candidate targets) + B2 OOS attribution method run in parallel after `A-LEDGER` lands. | `docs/PROJECT_ROADMAP.md` §4 Project B; PRDs to be drafted via `/plan-prd` |
| **Project C** — Live execution & deployment infrastructure | 🟡 Active in parallel with B. C1 live data → C2 LEAN/paper (ARIMA placeholder) → C3 sizing → C4 confidence → C5 monitoring. | `docs/PROJECT_ROADMAP.md` §4 Project C |
| **Project D** — Continuous research agents (Phase 5) | 📋 Spec mature, **gated**. Both triggers required: any B sub-project clears its pre-committed gate AND B2's OOS attribution method shipped with catalog integration. | `docs/PHASE_5_AGENTS.md` + `docs/PROJECT_ROADMAP.md` §4 Project D / §8 decision 7 |

> **Phase 0–4A delivery narratives** — what each phase/milestone built, the
> exit-gate numbers, and the full M1–M6 Phase 4A story — have moved to
> [`docs/historical/PHASE_DELIVERY_NOTES.md`](docs/historical/PHASE_DELIVERY_NOTES.md)
> to keep this entry point lean. The status table above plus
> [`docs/PHASE_4A_REPORT.md`](docs/PHASE_4A_REPORT.md) (Track A NO-GO verdict) are
> the current-state summary; [`docs/PROJECT_ROADMAP.md`](docs/PROJECT_ROADMAP.md)
> covers what's next.

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
│   ├── label_schemes.py      vol_scaled_returns() + triple_barrier_labels() + LDP_DEFAULT (Phase 4A M2)
│   ├── engineering.py        build_features() — 17 base + 4 regime cols (21; +3 with sentiment_df); lagged FRED join (FRED_PUBLICATION_LAGS, M5)
│   ├── cross_sectional.py    add_cross_sectional_features() — xs_rank_* panel percentile ranks (Phase 4A M3)
│   ├── catalog.py            FeatureRecord + load_catalog() + validate_catalog_coverage() (Phase 4A M4)
│   ├── catalog.yaml          machine-readable registry — 27 columns × 12 metadata fields (Phase 4A M4)
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
│   ├── ablation.py           run_label_ablation() (M2) + run_feature_ablation() / make_add_one_sets() / make_leave_one_out_sets() (M3)
│   ├── regimes.py            RegimeDetector + VIXThresholdDetector + DateRangeDetector (M1)
│   ├── regime_metrics.py     compute_regime_metrics() + regime_dm_test() + phase4a_gate_report() (M1)
│   ├── statistics.py         diebold_mariano() — DM test with HLN small-sample correction
│   ├── report.py             format_report() / summary_table() / regime + ablation reporters
│   └── CLAUDE.md             agent instructions for the backtest package
└── utils/calendar.py         trading-day calendar (gap detection)

scripts/
└── run_phase4a_arms.py       headless runner for the 4 M6 arms — per-arm
                              parquet checkpoints under data/phase4a/{arm}/
                              (Phase 4A M6)
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
# Full test suite (467 tests, ~77s, no network):
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
# nb05 (Phase 4A M1 regime harness walk-through) and nb06 (Phase 4A M2 label
# ablation matrix) use ARIMA control on a 5-symbol × 8-year slice — fast:
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=900 notebooks/05_phase4a_regime_harness.ipynb
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=900 notebooks/06_phase4a_label_ablation.ipynb
# nb07 (Phase 4A M5 FRED leakage A/B) runs four GBM preview backtests
# (n_iter=10) + two IS SHAP fits on the 5-symbol slice — needs 1800s timeout:
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=1800 notebooks/07_phase4a_fred_leakage.ipynb
# nb08 (Phase 4A M3 feature ablation) runs 8 add-one + up to 3 leave-one-out
# GBM preview backtests (n_iter=10) + one IS SHAP fit on the 5-symbol slice
# — needs 3600s timeout:
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=3600 notebooks/08_phase4a_feature_ablation.ipynb
# nb09 (Phase 4A M6 exit-gate verdict) is checkpoint-only — loads four parquet
# arms from data/phase4a/ and renders the gate report. Runs in seconds:
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=900 notebooks/09_phase4a_exit_gate.ipynb
# The four M6 arms themselves run via the headless runner (NOT a notebook).
# Each arm writes to data/phase4a/{arm}/; nb09 only consumes the checkpoints.
.venv/bin/python scripts/run_phase4a_arms.py --arm arima
.venv/bin/python scripts/run_phase4a_arms.py --arm signed
.venv/bin/python scripts/run_phase4a_arms.py --arm vol_scaled
.venv/bin/python scripts/run_phase4a_arms.py --arm triple_barrier

# Lint / format:
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format src/ tests/
```

## Notebook outputs and git

`nbstripout` is wired as a git filter (`.gitattributes`). Outputs are
automatically stripped on `git add`. Always commit notebooks before running
them so the clean baseline is preserved.

## Docs layout

Reorganized 2026-06-17: active references live at `docs/` top level;
completed-phase docs moved to `docs/historical/`.

```
docs/
├── AGENT_OPERATION.md             standard operating procedure — the 11-step task workflow
├── PROJECT_ROADMAP.md             master pivot doc — portfolio, sequencing, ratified decisions
├── METHODOLOGY.md                 binding contract — rules 1–20 (research + engineering)
├── PRIORITIES.yaml                living task backlog — agents pick top `ready` task
├── PHASE_4A_REPORT.md             Phase 4A exit-gate verdict (NO-GO for Track A)
├── PHASE_4A_RETROSPECTIVE.md      Phase 4A lessons-learned narrative (points at METHODOLOGY.md)
├── PHASE_5_AGENTS.md              Project D vision spec (gated; not started)
├── ENV.md                         environment variables and runtime settings
├── CONTRIBUTING.md                dev setup, test instructions, adding ingestors
├── historical/                    completed-phase specs (frozen — read-only reference)
│   ├── PHASE_0_INFRASTRUCTURE.md
│   ├── PHASE_1_BACKTESTER.md
│   ├── PHASE_2_MODELING.md
│   ├── PHASE_2.5_FEATURE_IMPROVEMENT.md
│   ├── PHASE_3_SENTIMENT.md
│   ├── PHASE_4_ADVANCED.md
│   └── REFACTOR_PORTFOLIO_UNION_INDEX.md
└── concepts/                      reference docs (definitions, conventions, deep-dives)
    ├── purging-and-embargo.md     leakage controls and embargo sizing
    ├── cost-model.md              trade simulator cost assumptions
    ├── metrics-glossary.md        performance metric definitions
    ├── feature-glossary.md        all model features — rationale + formulas
    ├── evaluation-standards.md    exit gate thresholds T1–T6
    ├── regime-evaluation.md       regime-conditional eval concepts (Phase 4A M1)
    ├── label-schemes.md           signed/vol-scaled/triple-barrier (Phase 4A M2)
    └── fred-publication-lag.md    publication-lag corrections (Phase 4A M5)
```

Future docs created by upcoming work (PRDs, sub-project reports) follow
the same convention: live at top level while active, move to
`historical/` when complete and superseded.

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
