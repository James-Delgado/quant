# Algorithmic Trading System — Project Overview

> **Purpose of this document.** This is the canonical reference for the project.
> It is written for future agents and contributors who need to understand —
> from a cold start — what is being built, why, what has been decided, and what
> the current state is. Read this before making changes or proposing work.
> When decisions change, update this document.

---

## 1. Mission and scope

The project is a personal algorithmic trading system, built and financed by a
single individual on personal hardware. It has **two distinct sub-projects**,
deliberately tackled in sequence:

- **Project A — Predictive modeling.** Build state-of-the-art models for
  predicting price movement and producing trade signals. *This is the current
  focus.*
- **Project B — Trading platform.** Build the infrastructure that connects to
  brokerages and crypto accounts and executes trades. *Deferred until Project A
  has a working prototype.*

These are kept separate because they have different requirements, risks, and
skill sets. All work described below concerns **Project A** unless stated
otherwise.

### Constraints that shape every decision

- **Solo operator.** No team. Tooling and architecture must be maintainable by
  one person.
- **Personal hardware and budget.** Primary machine is a 2023 MacBook with an
  Apple M2 Pro. There is no standing cloud budget; cloud spend must be
  justified, ephemeral, and small.
- **Personal financing.** Capital at risk is the operator's own. Risk
  management and honest evaluation matter more than ambition.

---

## 2. Guiding principles (the sober framing)

This project explicitly rejects the "predict the price, get rich" framing.
Future agents must internalize the following, because it governs what counts
as success:

- **The signal is weak and adversarial.** Price prediction is hard not because
  models are weak but because the signal-to-noise ratio is low, markets are
  non-stationary, and other algorithms actively learn against you. Published
  results show machine learning on technical indicators tops out near
  coin-flip accuracy, and reinforcement-learning trading studies frequently
  show no real profitability despite statistically significant backtests.
- **The goal is a small, real, risk-adjusted edge — and not blowing up.** Not
  high prediction accuracy. A model that is "right" on direction can still
  lose money after costs.
- **Validation methodology outranks model architecture.** The most common
  failure mode is overfitting a backtest and concluding an edge exists when it
  does not. More effort goes into honest evaluation than into modeling.
- **Backtests overstate reality.** Partial-equilibrium backtests ignore market
  impact and adaptation. Every performance number is treated as optimistic
  until proven otherwise out-of-sample and net of realistic costs.
- **Start simple. Earn complexity.** Each phase must beat its baseline before
  the next, more complex phase begins.

---

## 3. The modeling landscape (Project A research summary)

A survey of current (2024–2026) approaches was conducted. Summary of the
options and their role in this project:

| Approach | Role here | Notes |
|---|---|---|
| Classical statistical (ARIMA, GARCH) | **Baseline** | Linear; cannot capture nonlinearity. GARCH retained for volatility/risk sizing. |
| Gradient-boosted trees (XGBoost, LightGBM) | **Primary starting model** | Best signal-to-effort ratio. Excels on tabular/engineered features. CPU-friendly. |
| Recurrent deep learning (LSTM, GRU) | Benchmark only | Largely superseded; finicky on noisy financial series. |
| Transformers & hybrids (TFT, T-Mamba, etc.) | **Later experiment** | Current academic SOTA. Single consumer GPU suffices. Margins over boosting often small/fragile. |
| Time-series foundation models (TimesFM, Chronos) | Optional baseline | Zero-shot; weak on adversarial financial targets. Cheap to try. |
| LLM-based (FinBERT, FinGPT) | **Feature source** | Used for sentiment from news/filings, feeding the price model — not as a standalone price predictor. |
| Reinforcement learning (FinRL, PPO/DQN) | Deferred | Attractive but high risk of wasted effort. Best for execution optimization. |

**Decision:** Start with gradient-boosted trees. Add LLM-derived sentiment as a
feature. Only after a working, honestly-evaluated prototype exists, assess
whether to build a transformer, fine-tune a foundation model, or pursue RL.
That assessment is explicitly deferred — it is not pre-committed.

---

## 4. Roadmap and phases

Phases are gated: **do not advance until the current phase beats its baseline
net of realistic transaction costs and slippage.**

| Phase | Deliverable | Status |
|---|---|---|
| **0** | Data pipeline + local data lake | **Complete** — built, live-API verified (Alpaca/Tiingo/FRED), 35-test unit suite green |
| **1** | Rigorous purged walk-forward backtester | Designed; next to build |
| **2** | Gradient-boosted (XGBoost) model vs. ARIMA baseline | Not started |
| **3** | FinBERT sentiment feature integrated into the model | Not started |
| **4+** | Assess & build advanced models (transformer / foundation model / RL); execution layer via LEAN | Not started |
| (Separate track) | Polymarket event-betting experiment | Not started; see §8 |

---

## 5. Key architectural decisions

### 5.1 Decouple training from execution

The single most important architectural principle. **Model training and trade
execution are two separate systems with opposite requirements.** Training is
bursty, compute-heavy, and offline. Execution is lightweight, always-on, and
latency-sensitive. Conflating them leads to either paying for idle GPUs or
cramming models into environments too small for them.

### 5.2 Role of QuantConnect / LEAN

LEAN (the open-source engine under QuantConnect) is **not** used for the
research phase — its syntax and slow iteration make ML research painful.
Instead:

- Research, the backtester, and modeling (Phases 0–3) use a **plain Python
  stack**.
- **LEAN, run locally** (free, open-source; cloud subscription optional), is
  adopted later as the **execution and final-validation layer**. Running it
  locally avoids the cloud live-node RAM cap. Trained models live outside LEAN;
  the LEAN algorithm consumes their predictions. This gives a "backtest ==
  live" guarantee without ML-iteration friction.

### 5.3 Data sources

All sources below have free tiers; **the project starts at $0 data cost.**

| Data type | Source (free) | Paid upgrade path |
|---|---|---|
| US equities, intraday | Alpaca (10 yrs of 1-min, free, also a broker) | Polygon.io (~$79/mo) |
| US equities, daily EOD | Tiingo free tier | Tiingo paid (~$10–30/mo) |
| Crypto | CoinGecko + exchange APIs | CoinGecko paid |
| Fundamentals / filings | SEC EDGAR (free, official) | FMP / Tiingo |
| Macro | FRED (free) | — |
| News (sentiment) | RSS, GDELT, EDGAR 8-Ks | NewsAPI / Finnhub (~$50–100/mo) |

### 5.4 Storage

Parquet files + DuckDB. Parquet for columnar storage; DuckDB as an embedded
SQL query engine over the Parquet files (no server). ArcticDB is the
recommended step-up if versioned time-series storage is later needed. No
database server is provisioned.

### 5.5 Pipeline design

Prefect for orchestration. Core principles: **raw-first and immutable** (every
API pull is landed untouched before transformation), **idempotent** (re-runs
overwrite, never duplicate), **incremental** (fetch only the gap since last
run), **point-in-time correct** (every row stamped with when it was knowable),
and **failure-isolated** (one source failing does not abort the others).

### 5.6 Hardware and cost strategy

- **Phases 0–3 run entirely on the M2 Pro, for $0.** Gradient boosting and
  FinBERT inference are CPU-friendly; the pipeline is I/O-bound.
- **GPU training (later, occasional):** use *ephemeral* spot instances (AWS
  g5/g4dn, or cheaper RunPod/Vast.ai), spun up per training run and shut down
  after. **Never rent a persistent GPU** — idle GPU instances are the classic
  money-burn.
- **Live hosting (later):** a small **always-on CPU instance** (~$10–30/mo).
  Live inference (e.g. XGBoost) does not need a GPU.
- **The daily ingestion cron job and the live trading algo** are co-located on
  that one small CPU instance, run as **isolated processes** (separate
  `systemd` services/containers) so a failure in one cannot take down the
  other. Model **retraining stays off this box.** The data store is backed up
  to S3 so the instance is disposable.

---

## 6. Phase 0 — data pipeline (built)

A starter repository has been designed and built. It ingests market and macro
data on a schedule into a local Parquet lake.

### Repository structure

```
quant/
├── pyproject.toml          deps + project metadata
├── .env.example            API keys (copy to .env)
├── src/quant/
│   ├── config.py           typed settings + universe definition
│   ├── storage/lake.py     Parquet read/write (raw + processed layers)
│   ├── storage/catalog.py  DuckDB SQL query layer
│   ├── ingest/schemas.py   pandera schemas — type-checks every ingestor output
│   ├── ingest/alpaca_bars.py   template ingestor (daily equity bars)
│   ├── ingest/tiingo_eod.py    EOD prices (cross-check source)
│   ├── ingest/fred_macro.py    macro series, with revision-overlap handling
│   ├── flows/daily.py      orchestrator: runs all ingestors, isolates failures
│   └── utils/calendar.py   trading-day calendar (gap detection)
├── scripts/backfill.py     one-off historical pull
├── tests/
│   ├── conftest.py         prefect_test_harness + lake_root fixture + mock factories
│   ├── test_storage.py     lake + catalog layer
│   ├── test_ingest_alpaca.py / test_ingest_tiingo.py / test_ingest_fred.py
│   ├── test_flows.py       daily orchestrator (failure isolation)
│   ├── test_config.py      credential validation
│   └── test_integration.py live-API smoke tests (opt-in via --integration)
└── data/{raw,processed}/   the lake (gitignored)
```

### Data lake layout

- `data/raw/<source>/<dataset>/dt=<YYYY-MM-DD>/data.parquet` — immutable
  landing zone, one file per ingestion date.
- `data/processed/<dataset>/year=<YYYY>/month=<MM>/data.parquet` — cleaned,
  typed, Hive-partitioned by year/month (avoids the tiny-files problem).

### Datasets ingested

| Source | Dataset | Content |
|---|---|---|
| Alpaca | `equity_bars_daily` | Daily OHLCV bars (free IEX feed) |
| Tiingo | `equity_eod_tiingo` | Adjusted EOD prices (independent cross-check) |
| FRED | `macro_fred` | 10y yield, fed funds, VIX, CPI, unemployment |

### Ingestor design

Every ingestor follows the same four-step shape: determine date range
(incremental by default; `--backfill` for full history) → fetch (a Prefect
task with retries) → land raw immutably → clean and write the processed layer.
`alpaca_bars.py` is the canonical template; the other two are variations.

### Status

Phase 0 is complete. All three sources have been verified against the live APIs
(Alpaca IEX feed, Tiingo, FRED). SDK call signatures and DataFrame shapes were
confirmed and any drift was fixed in the ingestors and pandera schemas.

A 35-test unit suite covers the storage layer, all three ingestors, the daily
orchestrator, and credential validation. Integration smoke tests (behind
`--integration`) call the real APIs end-to-end. Run with:

```bash
pytest                   # unit tests, no network
pytest --integration     # live API smoke tests
```

### Known caveats

- **Survivorship bias.** The 10-stock universe is all current survivors. The
  backtester (Phase 1) must account for this. Remediation (point-in-time
  universe with delisted names) deferred until after Phase 1 validation.
- **Tiingo timestamps** are midnight UTC (`00:00:00+00:00`); Alpaca uses
  `04:00:00+00:00`. Normalise to date when joining the two sources.

---

## 7. Phase 1 — the backtester (next)

The backtester is the project's "scientific instrument." If it is wrong, every
later modeling decision is measured with a broken ruler. It must be built
**before** any model. It has two layers.

### 7.1 Model-evaluation layer — purged walk-forward cross-validation

- **Standard k-fold CV is invalid** for time series — shuffling trains on the
  future to predict the past.
- **Walk-forward testing:** slide a train/test window through history; every
  prediction uses only prior data. Use a *rolling* window (regimes shift, old
  data goes stale). The walk-forward step size equals the production
  retraining cadence.
- **Purging:** trading labels depend on a future window (e.g. "return over the
  next 5 days"), so a training sample near the boundary can have a label that
  reaches into the test set. Purging drops any training sample whose label
  window overlaps the test set.
- **Embargo:** features are autocorrelated, so test samples right after the
  boundary resemble the last training samples. The embargo drops a small
  buffer around the boundary.
- **CPCV (Combinatorial Purged Cross-Validation):** the advanced version —
  forms many purged train/test combinations to produce a *distribution* of
  backtest outcomes, not a single number. The main defense against backtest
  overfitting. Design the harness so this can be enabled later.

### 7.2 Trade-simulation layer — realism

A correct validation scheme still produces fantasy returns if the trade
simulation is naive. The simulator must model: transaction costs, slippage and
spread (buy at ask, sell at bid), realistic fills (no execution at the exact
bar close; model partial fills and market impact at size), and latency. Two
data-side biases must be eliminated: **survivorship bias** (the universe must
be point-in-time correct and include delisted/bankrupt names) and
**point-in-time fundamentals** (use values as known then, not later
restatements).

### 7.3 Metrics

Judge the strategy, not the model. Use risk-adjusted and trading metrics —
Sharpe, Sortino, max drawdown, Calmar, hit rate, profit factor, turnover —
not accuracy/RMSE. Always compare in-sample vs out-of-sample (a large gap
signals overfitting). Account for **multiple testing** with the Deflated
Sharpe Ratio.

### 7.4 Tooling

`mlfinlab` implements purged/combinatorial CV, the Deflated Sharpe Ratio, and
volume/dollar bars. Event-driven frameworks (Backtrader) handle fill
simulation. LEAN remains the execution-grade cross-check before going live.

---

## 8. Later phases and separate tracks

- **Phase 2 — gradient-boosted model.** XGBoost/LightGBM for cross-sectional
  return ranking or directional classification, evaluated against an ARIMA
  baseline through the Phase 1 backtester. Effort is mostly feature
  engineering and honest validation.
- **Phase 3 — sentiment feature.** Ingest news/filings, run FinBERT inference,
  produce a timestamped sentiment score that becomes another column in the
  feature store feeding the model.
- **Phase 4+ — advanced modeling.** Only after Phase 3 shows an edge: assess
  and possibly build a transformer (e.g. TFT), fine-tune a financial
  foundation model, or explore RL for execution. Decision deferred.
- **Polymarket (separate track).** Event/prediction markets are a different
  modeling problem (binary, event-driven) and are **not** part of the
  price-prediction pipeline. If pursued, they belong downstream of the
  sentiment/LLM stack. **The current US regulatory status of Polymarket must
  be verified before any trading** — it has a complicated and shifting
  history. Treat as Phase 4+ at the earliest.

---

## 9. Technology stack summary

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | Ecosystem; native on Apple Silicon |
| Package manager | `uv` | Fast, modern |
| Dataframes | pandas (interop), polars (fast ETL) | pandas for library compatibility; polars as optimization |
| Storage | Parquet + DuckDB | Embedded, serverless, fast |
| Orchestration | Prefect | Retries, scheduling, failure isolation, run UI |
| Config | pydantic-settings | Typed, fails loudly on missing keys |
| Data SDKs | alpaca-py, tiingo, fredapi | Official provider clients |
| Calendar | pandas-market-calendars | Trading-day gap detection |
| Validation (Phase 1) | mlfinlab | Purged/combinatorial CV, Deflated Sharpe |
| Modeling (Phase 2) | XGBoost / LightGBM | Best signal-to-effort on tabular data |
| Sentiment (Phase 3) | FinBERT | Lightweight financial sentiment |
| Execution (Phase 4+) | LEAN (local) | Realistic fills; backtest == live |

---

## 10. Open questions and things to verify

- **Polymarket US regulatory status:** must be checked against current
  official sources before any event-market trading.
- **Advanced model choice (Phase 4):** transformer vs. foundation model vs. RL
  is deliberately undecided pending Phase 3 results.
- **Data upgrade triggers:** when (if) to move from free Alpaca/Tiingo data to
  paid Polygon, based on intraday data-quality needs.

---

## 11. Glossary

- **Walk-forward testing** — evaluation that slides a train/test window through
  time so predictions only ever use past data.
- **Purging** — removing training samples whose (future-dependent) labels
  overlap the test period.
- **Embargo** — dropping a small buffer of samples around the train/test
  boundary to defeat serial-correlation leakage.
- **CPCV** — Combinatorial Purged Cross-Validation; yields a distribution of
  backtest paths rather than one curve.
- **Point-in-time correctness** — using only data as it was actually known at
  a given moment; the basis of leak-free evaluation.
- **Survivorship bias** — overstating returns by testing on a universe that
  excludes failed/delisted companies.
- **Deflated Sharpe Ratio** — a Sharpe ratio adjusted for the number of
  strategy configurations tried (multiple-testing correction).
- **LEAN** — the open-source algorithmic trading engine underlying
  QuantConnect; here used locally as the execution layer.
- **Data lake** — the layered Parquet directory store (raw + processed).

---

## Current status and next step

- **Done:** Project framing, modeling research, architecture decisions, Phase 0
  built and fully verified (live APIs confirmed, 35-test suite green).
- **Immediate next step:** run the 5-year backfill (`python scripts/backfill.py`)
  then build the Phase 1 purged walk-forward backtester.
- **Not yet started:** Phases 1–4, Project B (trading platform).

*Keep this document current. When a decision changes or a phase completes,
update the relevant section and the status table in §4.*
