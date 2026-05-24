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
from prefect import flow, get_run_logger, task

from quant.config import settings
from quant.storage import catalog, lake

DATASET = "equity_bars_daily"


@task(retries=3, retry_delay_seconds=30, log_prints=True)
def fetch_bars(symbols: list[str], start: datetime, end: datetime) -> pd.DataFrame:
    """Pull daily bars from Alpaca's free IEX feed.

    Retries 3x: transient network / rate-limit errors should not fail the run.
    Imports of the SDK are local so the module loads even before deps install.
    """
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

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
    """Step 4 — type, add point-in-time stamp, partition by year/month."""
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.drop_duplicates(subset=["symbol", "timestamp"])
    df["year"] = df["timestamp"].dt.year
    df["month"] = df["timestamp"].dt.month
    # `ingested_at` records when WE learned this row — the basis of all
    # point-in-time correctness downstream.
    df["ingested_at"] = pd.Timestamp.now(tz="UTC")
    lake.write_processed(df, dataset=DATASET, partition_cols=["year", "month"])
    return len(df)


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
