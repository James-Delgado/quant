"""Alpaca daily equity bars — the template ingestor.

This is the worked example. tiingo_eod.py and fred_macro.py follow the same
four-step shape:
    1. determine the date range (incremental, or full backfill)
    2. fetch from the API  (a @task, with retries)
    3. land the raw pull immutably
    4. clean -> write the processed layer

Run directly:        python -m quant.ingest.alpaca_bars
Or via the daily flow: python -m quant.flows.daily
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from prefect import flow, get_run_logger, task

from quant.config import settings
from quant.ingest.schemas import ALPACA_BARS_SCHEMA
from quant.storage import catalog, lake

DATASET = "equity_bars_daily"


@task(retries=3, retry_delay_seconds=30, log_prints=True)
def fetch_bars(symbols: list[str], start: datetime, end: datetime) -> pd.DataFrame:
    """Pull daily bars from Alpaca's free IEX feed.

    Retries 3x: transient network / rate-limit errors should not fail the run.
    """
    client = StockHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_secret_key)
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        feed=DataFeed.IEX,  # free tier
    )
    barset = client.get_stock_bars(request)
    if not barset.data:
        return pd.DataFrame()
    # barset.df is MultiIndex (symbol, timestamp); flatten to plain columns.
    return barset.df.reset_index()


@task
def land_raw(df: pd.DataFrame) -> None:
    """Step 3 — immutable raw landing, keyed by ingestion date."""
    lake.write_raw(df, source="alpaca", dataset=DATASET, dt=date.today())


@task
def to_processed(df: pd.DataFrame) -> int:
    """Step 4 — type, add point-in-time stamp, merge with existing, partition by year/month.

    Merge-then-rewrite avoids the PyArrow delete_matching hazard: writing only
    the incremental slice with delete_matching would erase the rest of the
    month's partition. Instead, read what we already have, union with the new
    pull, dedup keeping latest ingested_at, and rewrite the whole dataset.
    Equity data for 10 symbols × 5 years is ~12 k rows — fast enough to
    reload on every run.
    """
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["ingested_at"] = pd.Timestamp.now(tz="UTC")
    df["year"] = df["timestamp"].dt.year.astype("int64")
    df["month"] = df["timestamp"].dt.month.astype("int64")

    existing = lake.read_processed(DATASET)
    if not existing.empty:
        existing["timestamp"] = pd.to_datetime(existing["timestamp"], utc=True)
        existing["ingested_at"] = pd.to_datetime(existing["ingested_at"], utc=True)
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df

    combined = (
        combined.sort_values("ingested_at")
        .drop_duplicates(subset=["symbol", "timestamp"], keep="last")
        .sort_values(["symbol", "timestamp"])
        .reset_index(drop=True)
    )
    combined["year"] = combined["timestamp"].dt.year.astype("int64")
    combined["month"] = combined["timestamp"].dt.month.astype("int64")

    ALPACA_BARS_SCHEMA.validate(combined)
    lake.write_processed(combined, dataset=DATASET, partition_cols=["year", "month"])
    return len(combined)


@flow(name="ingest-alpaca-bars")
def ingest_alpaca_bars(backfill: bool = False) -> None:
    """Incremental by default: fetch only [last stored day + 1, now].
    Pass backfill=True for the first run to pull `backfill_years` of history.
    """
    logger = get_run_logger()
    end = datetime.now(tz=timezone.utc)

    last = catalog.latest_timestamp(DATASET)
    if backfill or last is None:
        start = end - timedelta(days=365 * settings.backfill_years)
        logger.info(f"Backfill run from {start.date()}")
    else:
        start = last + timedelta(days=1)
        logger.info(f"Incremental run from {start.date()}")

    if start >= end:
        logger.info("Already up to date — nothing to fetch.")
        return

    df = fetch_bars(settings.equity_universe, start, end)
    if df.empty:
        logger.warning("Alpaca returned no bars for the requested window.")
        return

    land_raw(df)
    n = to_processed(df)
    logger.info(f"Ingested {n} rows across {df['symbol'].nunique()} symbols.")


if __name__ == "__main__":
    ingest_alpaca_bars()
