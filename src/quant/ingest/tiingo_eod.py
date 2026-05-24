"""Tiingo end-of-day prices — clean, split/dividend-adjusted daily history.

Same four-step shape as alpaca_bars.py. Tiingo gives an independent EOD source:
useful as a cross-check against Alpaca and for its adjusted-close column.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from prefect import flow, get_run_logger, task

from quant.config import settings
from quant.storage import catalog, lake

DATASET = "equity_eod_tiingo"


@task(retries=3, retry_delay_seconds=30)
def fetch_eod(symbols: list[str], start: datetime) -> pd.DataFrame:
    """Fetch one symbol at a time (Tiingo is per-ticker), pausing briefly to
    stay under the free-tier hourly rate limit."""
    from tiingo import TiingoClient

    client = TiingoClient({"api_key": settings.tiingo_api_key})
    frames: list[pd.DataFrame] = []
    for symbol in symbols:
        try:
            sdf = client.get_dataframe(
                symbol, frequency="daily", startDate=start.strftime("%Y-%m-%d")
            )
        except Exception:  # one bad ticker must not abort the batch
            continue
        if sdf.empty:
            continue
        sdf = sdf.reset_index().rename(columns={"index": "timestamp", "date": "timestamp"})
        sdf["symbol"] = symbol
        frames.append(sdf)
        time.sleep(0.2)  # gentle throttle
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


@task
def land_raw(df: pd.DataFrame) -> None:
    lake.write_raw(df, source="tiingo", dataset=DATASET, dt=date.today())


@task
def to_processed(df: pd.DataFrame) -> int:
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.drop_duplicates(subset=["symbol", "timestamp"])
    df["year"] = df["timestamp"].dt.year
    df["month"] = df["timestamp"].dt.month
    df["ingested_at"] = pd.Timestamp.now(tz="UTC")
    lake.write_processed(df, dataset=DATASET, partition_cols=["year", "month"])
    return len(df)


@flow(name="ingest-tiingo-eod")
def ingest_tiingo_eod(backfill: bool = False) -> None:
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

    df = fetch_eod(settings.equity_universe, start)
    if df.empty:
        logger.warning("Tiingo returned no rows for the requested window.")
        return

    land_raw(df)
    n = to_processed(df)
    logger.info(f"Ingested {n} rows across {df['symbol'].nunique()} symbols.")


if __name__ == "__main__":
    ingest_tiingo_eod()
