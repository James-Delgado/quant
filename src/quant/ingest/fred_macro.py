"""FRED macroeconomic series — yields, rates, VIX, CPI, unemployment.

Macro data is small and slow-moving, so the processed layer is a single
unpartitioned file. Each series is stored in long form (one row per
series/date) so adding a new series never changes the schema.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
from fredapi import Fred
from prefect import flow, get_run_logger, task

from quant.config import settings
from quant.ingest.schemas import FRED_MACRO_SCHEMA
from quant.storage import catalog, lake

DATASET = "macro_fred"


@task(retries=3, retry_delay_seconds=30)
def fetch_series(series_ids: list[str], start: datetime) -> pd.DataFrame:
    """Pull each FRED series and stack into a long-form DataFrame."""
    fred = Fred(api_key=settings.fred_api_key)
    frames: list[pd.DataFrame] = []
    for sid in series_ids:
        try:
            s = fred.get_series(sid, observation_start=start.strftime("%Y-%m-%d"))
        except Exception:
            continue
        if s is None or s.empty:
            continue
        sdf = s.rename("value").reset_index().rename(columns={"index": "timestamp"})
        sdf["series_id"] = sid
        frames.append(sdf)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


@task
def land_raw(df: pd.DataFrame) -> None:
    lake.write_raw(df, source="fred", dataset=DATASET, dt=date.today())


@task
def to_processed(df: pd.DataFrame) -> int:
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.dropna(subset=["value"])
    df["ingested_at"] = pd.Timestamp.now(tz="UTC")
    # Macro re-pulls an overlap window, so merge with what we already have and
    # keep the latest observation per (series, date). The dataset is small, so
    # rewriting the single file in full is the simplest correct approach.
    existing = lake.read_processed(DATASET)
    if not existing.empty:
        df = pd.concat([existing, df], ignore_index=True)
    df = (
        df.sort_values("ingested_at")
        .drop_duplicates(subset=["series_id", "timestamp"], keep="last")
        .sort_values(["series_id", "timestamp"])
    )
    FRED_MACRO_SCHEMA.validate(df)
    lake.write_processed(df, dataset=DATASET, partition_cols=None)
    return len(df)


@flow(name="ingest-fred-macro")
def ingest_fred_macro(backfill: bool = False) -> None:
    logger = get_run_logger()
    end = datetime.now(tz=timezone.utc)
    last = catalog.latest_timestamp(DATASET)
    if backfill or last is None:
        start = end - timedelta(days=365 * settings.backfill_years)
        logger.info(f"Backfill run from {start.date()}")
    else:
        # Macro series get revised with long lags: CPI and UNRATE revisions
        # can arrive 30+ days later. 45-day overlap catches these safely.
        start = last - timedelta(days=45)
        logger.info(f"Incremental run from {start.date()} (with 45-day revision overlap)")

    df = fetch_series(settings.fred_series, start)
    if df.empty:
        logger.warning("FRED returned no observations.")
        return

    land_raw(df)
    n = to_processed(df)
    logger.info(f"Ingested {n} observations across {df['series_id'].nunique()} series.")


if __name__ == "__main__":
    ingest_fred_macro()
