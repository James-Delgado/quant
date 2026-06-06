"""SEC EDGAR filing ingestor — 8-K (material events) and 10-K/10-Q (periodic).

Follows the same 4-step pattern as alpaca_bars.py:
    1. Determine date range (incremental or full backfill)
    2. Fetch from EDGAR full-text search API
    3. Land raw response immutably in data/raw/
    4. Clean → write to data/processed/text_documents/

SEC policy requires a descriptive User-Agent header with your email address.
Set EDGAR_USER_AGENT in .env — e.g. "James Delgado jado650@berkeley.edu".
See: https://www.sec.gov/os/accessing-edgar-data

Rate limit: SEC enforces 10 requests/second. The fetch task sleeps 0.11s
between requests to stay safely under this limit.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import httpx
import pandas as pd
from prefect import flow, get_run_logger, task

from quant.config import settings
from quant.ingest.schemas import TEXT_DOCUMENT_SCHEMA
from quant.storage import lake

DATASET = "text_documents"
FilingType = Literal["8-K", "10-K", "10-Q"]


def _headers() -> dict[str, str]:
    return {
        "User-Agent": settings.edgar_user_agent,
        "Accept": "application/json",
    }


@task(retries=3, retry_delay_seconds=60, log_prints=True)
def fetch_filings(
    symbols: list[str],
    start: datetime,
    end: datetime,
    form_types: list[FilingType] | None = None,
) -> pd.DataFrame:
    """Pull SEC EDGAR filings for a list of symbols.

    Uses the EDGAR full-text search API. Rate-limited to 10 req/s per SEC policy.
    published_at is the SEC filing date — authoritative and never revised.
    """
    if form_types is None:
        form_types = ["8-K", "10-K", "10-Q"]

    logger = get_run_logger()
    rows: list[dict] = []
    forms_param = ",".join(form_types)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    with httpx.Client(headers=_headers(), timeout=30.0) as client:
        for symbol in symbols:
            url = (
                "https://efts.sec.gov/LATEST/search-index?"
                f"q=%22{symbol}%22"
                f"&dateRange=custom&startdt={start_str}&enddt={end_str}"
                f"&forms={forms_param}"
            )
            try:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("EDGAR fetch failed for %s: %s", symbol, exc)
                time.sleep(0.11)
                continue

            hits = data.get("hits", {}).get("hits", [])
            for hit in hits:
                src = hit.get("_source", {})
                filed_at_str = src.get("file_date") or src.get("period_of_report")
                if not filed_at_str:
                    continue
                try:
                    published_at = pd.Timestamp(filed_at_str, tz="UTC")
                except Exception:
                    continue

                rows.append({
                    "document_id": hit.get("_id", ""),
                    "source": "edgar",
                    "symbol": symbol,
                    "form_type": src.get("form_type", ""),
                    "published_at": published_at,
                    "ingested_at": pd.Timestamp.now(tz="UTC"),
                    "text": src.get("display_names", "") or src.get("entity_name", ""),
                    "accession_number": src.get("accession_no", ""),
                    "url": src.get("file_url", ""),
                })

            time.sleep(0.11)  # respect SEC 10 req/s limit

    logger.info("Fetched %d filings for %d symbols", len(rows), len(symbols))
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=[
        "document_id", "source", "symbol", "form_type",
        "published_at", "ingested_at", "text", "accession_number", "url",
    ])


@task
def land_raw(df: pd.DataFrame) -> None:
    lake.write_raw(df, source="edgar", dataset=DATASET, dt=date.today())


@task
def to_processed(df: pd.DataFrame) -> int:
    """Deduplicate by document_id, validate schema, write processed layer."""
    if df.empty:
        return 0

    df = df.copy()
    df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
    df["ingested_at"] = pd.to_datetime(df["ingested_at"], utc=True)
    df = df.dropna(subset=["published_at", "symbol", "text"])
    df = df[df["text"].str.strip().str.len() > 0]

    existing = lake.read_processed(DATASET)
    if not existing.empty:
        df = pd.concat([existing, df], ignore_index=True)

    df = (
        df.sort_values("ingested_at")
        .drop_duplicates(subset=["document_id"], keep="last")
        .sort_values(["symbol", "published_at"])
        .reset_index(drop=True)
    )

    TEXT_DOCUMENT_SCHEMA.validate(df)
    lake.write_processed(df, dataset=DATASET)
    return len(df)


@flow(name="edgar-ingestor")
def edgar_flow(
    symbols: list[str] | None = None,
    backfill: bool = False,
    form_types: list[FilingType] | None = None,
) -> int:
    """Fetch EDGAR filings and write to text_documents/.

    Incremental by default: starts from the latest published_at already in the
    lake so each daily run only fetches new filings.

    On first run (no existing data) or when backfill=True, pulls
    settings.backfill_years of history — the same setting used by the price and
    macro ingestors, keeping all data ranges in sync.
    """
    logger = get_run_logger()
    if not settings.edgar_user_agent:
        raise ValueError(
            "EDGAR_USER_AGENT is not set. "
            "Add your name and email to .env: EDGAR_USER_AGENT='Name email@example.com'"
        )
    symbols = symbols or settings.equity_universe
    end = datetime.now(tz=timezone.utc)

    existing = lake.read_processed(DATASET)
    last: datetime | None = None
    if not existing.empty and "published_at" in existing.columns:
        latest = pd.to_datetime(existing["published_at"]).max()
        if pd.notna(latest):
            last = latest.to_pydatetime()

    if backfill or last is None:
        start = end - timedelta(days=365 * settings.backfill_years)
        logger.info("Backfill run from %s (%d years)", start.date(), settings.backfill_years)
    else:
        # 7-day overlap: EDGAR sometimes back-dates amended filing dates, so a
        # small overlap prevents gaps without re-fetching years of history.
        start = last - timedelta(days=7)
        logger.info("Incremental run from %s (7-day overlap)", start.date())

    if start >= end:
        logger.info("Already up to date — nothing to fetch.")
        return 0

    logger.info("EDGAR ingest: %d symbols, %s to %s", len(symbols), start.date(), end.date())
    df = fetch_filings(symbols, start, end, form_types)
    if df.empty:
        logger.info("No filings found — nothing to land")
        return 0

    land_raw(df)
    n = to_processed(df)
    logger.info("text_documents/ now has %d rows", n)
    return n


if __name__ == "__main__":
    edgar_flow()
