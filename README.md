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
│   │   ├── alpaca_bars.py  TEMPLATE ingestor — read this one first
│   │   ├── tiingo_eod.py   same four-step shape
│   │   └── fred_macro.py   same shape, with revision-overlap handling
│   ├── flows/
│   │   └── daily.py        orchestrator: runs all ingestors, isolates failures
│   └── utils/calendar.py   trading-day calendar (gap detection)
├── scripts/backfill.py     one-off full historical pull
├── tests/                  storage smoke tests
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

## Run it

```bash
# 1. One-time: pull 5 years of history
python scripts/backfill.py

# 2. Thereafter: the daily incremental run (only fetches the gap)
python -m quant.flows.daily

# 3. To run on a schedule (after US close, weekdays):
python -m quant.flows.daily --serve

# Inspect runs in the Prefect UI:
prefect server start        # then open http://127.0.0.1:4200
```

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

## Next: Phase 1

The backtester. It reads the processed lake through `catalog`, runs purged
walk-forward validation, and reports risk-adjusted performance. The
`ingested_at` column added here is what makes leak-free evaluation possible.
