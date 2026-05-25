# quant — algorithmic trading research platform

A personal quantitative research system built on free data and a laptop.
Phases 0 and 1 are complete; Phase 2 (predictive modeling) is next.

| Phase | Status | Description |
|-------|--------|-------------|
| 0 — Data lake & ingestion | ✅ | Three ingestors, Parquet lake, DuckDB query layer |
| 1 — Purged walk-forward backtester | ✅ | Leak-free CV, realistic simulator, metrics, 87-test suite |
| 2 — Predictive modeling | 🔜 | Gradient-boosted trees evaluated through the Phase 1 harness |
| 3 — Sentiment & alternative data | 📋 | Spec only |
| 4 — Execution & live trading | 📋 | Spec only |

## What it does

**Data layer (Phase 0):** three ingestors, one daily orchestrating flow.

| Source | Dataset | Content | Free? |
|--------|---------|---------|-------|
| Alpaca | `equity_bars_daily` | Daily OHLCV bars (IEX feed) | yes |
| Tiingo | `equity_tiingo` | Adjusted EOD prices + corporate actions | yes |
| FRED | `macro_fred` | Yields, rates, VIX, CPI, unemployment | yes |

**Backtester (Phase 1):** purged walk-forward cross-validation with realistic
cost modelling. Eliminates the two main sources of backtesting bias — label
overlap leakage (purging) and serial-correlation leakage (embargo).

## Repo layout

```
quant/
├── pyproject.toml              deps + project metadata
├── requirements-lock.txt       pinned venv snapshot (209 packages)
├── .env.example                copy to .env, add your four free API keys
├── .gitattributes              nbstripout filter — strips notebook outputs on commit
├── src/quant/
│   ├── config.py               typed settings loaded from .env
│   ├── storage/
│   │   ├── lake.py             Parquet read/write (raw + processed layers)
│   │   └── catalog.py          DuckDB SQL query layer over the lake
│   ├── ingest/
│   │   ├── schemas.py          pandera schemas — catches API drift at ingestion
│   │   ├── alpaca_bars.py      Alpaca daily OHLCV ingestor
│   │   ├── tiingo_eod.py       Tiingo adjusted EOD ingestor
│   │   └── fred_macro.py       FRED macro series ingestor
│   ├── flows/
│   │   └── daily.py            Prefect orchestrator — runs all ingestors
│   ├── backtest/
│   │   ├── walkforward.py      purged walk-forward split generator
│   │   ├── simulator.py        vectorised next-bar trade simulator
│   │   ├── metrics.py          Sharpe / Sortino / Calmar / drawdown / hit-rate
│   │   ├── harness.py          run_backtest() — model + data → BacktestResult
│   │   └── report.py           format_report() / summary_table()
│   └── utils/calendar.py       trading-day calendar
├── notebooks/
│   └── 01_system_tour.ipynb    interactive walkthrough of all components
├── scripts/backfill.py         one-off historical pull (5 years)
├── tests/                      87 unit tests + 4 skipped integration tests
└── data/                       the lake (gitignored)
    ├── raw/                    immutable API pulls
    └── processed/              cleaned, hive-partitioned by year/month
```

## Setup

```bash
# uv is the fast modern installer — brew install uv if needed
uv venv
uv pip install -e ".[dev]"

cp .env.example .env   # paste in your four free API keys
```

**Do not use `source .venv/bin/activate` in scripts or agent sessions** — call
venv binaries directly (`.venv/bin/python`, `.venv/bin/pytest`, etc.).

## Tests

```bash
# Unit tests — 87 tests, ~16 seconds, no network:
.venv/bin/pytest

# With coverage:
.venv/bin/pytest --cov=src --cov-report=term-missing

# Live API smoke tests (requires .env credentials):
.venv/bin/pytest --integration
```

## Commands

<!-- AUTO-GENERATED from pyproject.toml + scripts/ -->

| Command | Description |
|---------|-------------|
| `uv pip install -e ".[dev]"` | Install package + all dev dependencies |
| `.venv/bin/pytest` | Run unit test suite (87 tests, ~16s, no network) |
| `.venv/bin/pytest --integration` | Run live-API smoke tests (requires `.env` credentials) |
| `.venv/bin/pytest --cov=src --cov-report=term-missing` | Run tests with coverage report |
| `.venv/bin/ruff check src/ tests/` | Lint |
| `.venv/bin/ruff format src/ tests/` | Auto-format |
| `.venv/bin/mypy src/` | Static type checking |
| `python scripts/backfill.py` | One-time: pull 5 years of history for all sources |
| `python -m quant.flows.daily` | Run one incremental daily ingest |
| `python -m quant.flows.daily --serve` | Start scheduled ingest daemon (22:30 UTC on weekdays) |
| `prefect server start` | Start Prefect UI at http://127.0.0.1:4200 |

<!-- END AUTO-GENERATED -->

## Query the lake

```python
import quant.storage.catalog as catalog

df = catalog.query(f"""
    SELECT symbol, timestamp, close
    FROM {catalog.table('equity_tiingo')}
    WHERE symbol = 'AAPL'
    ORDER BY timestamp
""")
```

## Run a backtest

```python
from sklearn.linear_model import LogisticRegression
from quant.backtest.harness import run_backtest
from quant.backtest.report import format_report

result = run_backtest(
    model=LogisticRegression(),
    features=features,   # pd.DataFrame, DatetimeIndex
    labels=labels,       # pd.Series, same index
    prices=prices,       # OHLCV DataFrame
    train_window=252,
    test_window=63,
    label_horizon=2,
    embargo=5,
)
print(format_report(result))
```

## Design principles

- **Raw-first & immutable** — every pull lands in `data/raw/` before any
  cleaning. Rebuild processed layer any time without re-hitting an API.
- **Idempotent** — re-running any day overwrites that day's partition only.
- **Incremental** — each ingestor fetches only the gap since the last run.
- **Leak-free evaluation** — purging removes label-window overlap; embargo
  removes serial-correlation leakage. Both controls are enforced by the
  harness self-tests.
- **Explicit costs** — commission, slippage, and liquidity cap are documented
  parameters, not buried magic numbers.

## Docs

| File | Contents |
|------|----------|
| [docs/ENV.md](docs/ENV.md) | Environment variables and runtime settings |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | Dev setup, adding new ingestors |
| [docs/PHASE_0_INFRASTRUCTURE.md](docs/PHASE_0_INFRASTRUCTURE.md) | Full project overview and architecture |
| [docs/PHASE_1_BACKTESTER.md](docs/PHASE_1_BACKTESTER.md) | Backtester spec and design |
| [docs/PHASE_2_MODELING.md](docs/PHASE_2_MODELING.md) | Next phase spec |
| [docs/concepts/purging-and-embargo.md](docs/concepts/purging-and-embargo.md) | Deep-dive on leakage controls |
| [docs/concepts/cost-model.md](docs/concepts/cost-model.md) | Trade simulator cost assumptions |
| [docs/concepts/metrics-glossary.md](docs/concepts/metrics-glossary.md) | Performance metric definitions |
