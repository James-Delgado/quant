# quant — Phase 0: data pipeline & local data lake

The foundational layer of the price-prediction project. It ingests market and
macro data on a schedule and lands it in a local Parquet lake that the
backtester (Phase 1) and models (Phase 2+) read from. Runs entirely on a
laptop — no cloud, no GPU, no paid data.

## What it does

Three ingestors, one daily orchestrating flow:

| Source | Dataset | Content | Free? |
|--------|---------|---------|-------|
| Alpaca | `equity_bars_daily` | Daily OHLCV bars (IEX feed) | yes |
| Tiingo | `equity_eod_tiingo`  | Adjusted EOD prices (cross-check) | yes |
| FRED   | `macro_fred`         | Yields, rates, VIX, CPI, unemployment | yes |

## Repo layout

```
quant/
├── pyproject.toml          deps + project metadata
├── .env.example            copy to .env, add your free API keys
├── src/quant/
│   ├── config.py           typed settings + the universe definition
│   ├── storage/
│   │   ├── lake.py         Parquet read/write (raw + processed layers)
│   │   └── catalog.py      DuckDB SQL query layer over the lake
│   ├── ingest/
│   │   ├── schemas.py      pandera schemas — catches API drift at ingestion
│   │   ├── alpaca_bars.py  TEMPLATE ingestor — read this one first
│   │   ├── tiingo_eod.py   same four-step shape
│   │   └── fred_macro.py   same shape, with revision-overlap handling
│   ├── flows/
│   │   └── daily.py        orchestrator: runs all ingestors, isolates failures
│   └── utils/calendar.py   trading-day calendar (gap detection)
├── scripts/backfill.py     one-off full historical pull
├── tests/
│   ├── conftest.py         fixtures: lake_root (isolated tmp), mock factories
│   ├── test_storage.py     lake + catalog layer
│   ├── test_ingest_alpaca.py
│   ├── test_ingest_tiingo.py
│   ├── test_ingest_fred.py
│   ├── test_flows.py       daily orchestrator (failure isolation)
│   ├── test_config.py      credential validation
│   └── test_integration.py live-API smoke tests (--integration flag)
└── data/                   the lake (gitignored)
    ├── raw/                immutable API pulls — the audit trail
    └── processed/          cleaned, typed, partitioned by year/month
```

## Setup (Apple Silicon M2 — all wheels are native ARM)

```bash
# uv is the fast modern installer; `brew install uv` if needed
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

cp .env.example .env        # then paste in your four free API keys
```

## Tests

```bash
# Unit tests (no network, ~15 seconds):
pytest

# Live API smoke tests (requires .env credentials, ~30 seconds):
pytest --integration
```

The unit suite runs 35 tests across storage, ingest logic, and orchestration —
all mocked, no network required. Integration tests call the real APIs and verify
end-to-end ingestion into a temp lake.

## Commands

<!-- AUTO-GENERATED from pyproject.toml + scripts/ -->

| Command | Description |
|---------|-------------|
| `uv pip install -e ".[dev]"` | Install package + all dev dependencies |
| `pytest` | Run unit test suite (35 tests, ~15s, no network) |
| `pytest --integration` | Run live-API smoke tests (requires `.env` credentials) |
| `pytest --cov=src --cov-report=term-missing` | Run unit tests with coverage report |
| `ruff check src/ tests/` | Lint (PEP 8 + style rules) |
| `ruff format src/ tests/` | Auto-format code |
| `mypy src/` | Static type checking |
| `python scripts/backfill.py` | One-time: pull 5 years of history for all sources |
| `python -m quant.flows.daily` | Run one incremental daily ingest (all sources) |
| `python -m quant.flows.daily --backfill` | Force a full historical pull |
| `python -m quant.flows.daily --serve` | Start scheduled ingest daemon (22:30 UTC on weekdays) |
| `python -m quant.ingest.alpaca_bars` | Run only the Alpaca ingestor |
| `python -m quant.ingest.tiingo_eod` | Run only the Tiingo ingestor |
| `python -m quant.ingest.fred_macro` | Run only the FRED ingestor |
| `prefect server start` | Start Prefect UI at http://127.0.0.1:4200 |

<!-- END AUTO-GENERATED -->

## Query the lake

```python
from quant.storage import catalog

df = catalog.query(f"""
    SELECT symbol, timestamp, close
    FROM {catalog.table('equity_bars_daily')}
    WHERE symbol = 'AAPL' AND year = 2026
    ORDER BY timestamp
""")
```

## Design principles

- **Raw-first & immutable** — every pull lands in `data/raw/` untouched before
  any cleaning. You can always rebuild the processed layer without re-hitting
  an API, and you have a full audit trail.
- **Idempotent** — re-running any day overwrites that day's partition. No
  duplicates, safe to retry.
- **Incremental** — each ingestor checks the catalog for the latest stored
  date and fetches only the gap. `--backfill` forces a full pull.
- **Point-in-time correct** — every processed row carries `ingested_at`,
  recording when *we* learned it. This is the basis of leak-free features.
- **Failure isolation** — one source failing is logged; the others still run.
- **Sane file sizes** — partitioned by year/month, not by symbol-day, to avoid
  the thousands-of-tiny-files problem that kills query speed.

## Docs

| File | Contents |
|------|----------|
| [docs/ENV.md](docs/ENV.md) | All environment variables and runtime settings |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | Dev setup, test instructions, adding new ingestors |
| [docs/PHASE_0_INFRASTRUCTURE.md](docs/PHASE_0_INFRASTRUCTURE.md) | Full project overview, architectural decisions, roadmap |
| [docs/PHASE_1_BACKTESTER.md](docs/PHASE_1_BACKTESTER.md) | Backtester design (purged walk-forward CV) |

## Next: Phase 1

The backtester. It reads the processed lake through `catalog`, runs purged
walk-forward validation, and reports risk-adjusted performance. The
`ingested_at` column added here is what makes leak-free evaluation possible.
