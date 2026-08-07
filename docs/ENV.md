# Environment Variables

<!-- AUTO-GENERATED from .env.example and src/quant/config.py -->

Copy `.env.example` to `.env` and fill in the four API keys. All providers have
free tiers — no payment is required for Phase 0.

## Required credentials

| Variable | Required | Provider | How to get it |
|----------|----------|----------|---------------|
| `ALPACA_API_KEY` | Yes | Alpaca Markets | Sign up at alpaca.markets; works on unfunded paper accounts |
| `ALPACA_SECRET_KEY` | Yes | Alpaca Markets | Generated alongside the API key |
| `TIINGO_API_KEY` | Yes | Tiingo | Free token at tiingo.com |
| `FRED_API_KEY` | Yes | Federal Reserve (FRED) | Free key at fredaccount.stlouisfed.org/apikeys |

Missing or blank values raise a `ValidationError` at startup before any API
call is made — fail loud, not silently at runtime.

## Runtime settings (no .env entry — set in config.py or override in code)

| Setting | Default | Description |
|---------|---------|-------------|
| `data_root` | `<project_root>/data` | Root of the local Parquet lake |
| `backfill_years` | `5` | How many years of history to pull on first run |
| `equity_universe` | `AAPL MSFT NVDA AMZN GOOGL META TSLA SPY QQQ IWM` | Symbols to ingest from Alpaca and Tiingo |
| `fred_series` | `DGS10 DFF VIXCLS CPIAUCSL UNRATE` | FRED series IDs to ingest (10y yield, fed funds, VIX, CPI, unemployment) |

These can be overridden by adding the corresponding env var (pydantic-settings
reads them automatically). For example, `DATA_ROOT=/mnt/ssd/quant/data` works
without touching config.py.

<!-- END AUTO-GENERATED -->

## Optional — console API (E2)

| Variable | Required | Used by | Description |
|----------|----------|---------|-------------|
| `CONSOLE_API_TOKEN` | No | `python -m quant.console.api` | Simple local bearer token for the console API's mutate routes (E2-M3). `POST /recompute` refuses to run without it (fail-closed); `POST /feedback` requires it only once set (otherwise it stays open under the localhost-only default bind). Send as `Authorization: Bearer <token>`. Deliberately **not** a `config.py` Setting — the API must be importable without credentials, and the token is read from the environment at request time. |
