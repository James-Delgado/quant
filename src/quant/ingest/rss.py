"""RSS feed ingestor for financial news.

Follows the same 4-step pattern as alpaca_bars.py:
    1. Determine date range
    2. Fetch and parse RSS feeds via httpx
    3. Land raw XML immutably in data/raw/
    4. Clean → write to data/processed/text_documents/

RSS feed URLs are configured in settings.rss_feed_urls. Symbol attribution
is derived from the feed URL (?s=SYMBOL parameter) or left as "macro" for
market-wide context.

published_at is taken from the feed item's <pubDate> field (RFC 2822) and
normalized to UTC. Items with missing or unparseable pubDate are dropped —
never filled with ingestion time, which would manufacture a false timestamp.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date
from email.utils import parsedate_to_datetime

import httpx
import pandas as pd
from prefect import flow, get_run_logger, task

from quant.config import settings
from quant.ingest.schemas import TEXT_DOCUMENT_SCHEMA
from quant.storage import lake

DATASET = "text_documents"
_SYMBOL_FROM_URL = re.compile(r"[?&]s=([A-Z]{1,5})", re.IGNORECASE)


def _parse_symbol(feed_url: str) -> str:
    m = _SYMBOL_FROM_URL.search(feed_url)
    return m.group(1).upper() if m else "macro"


def _parse_pubdate(raw: str | None) -> pd.Timestamp | None:
    """Parse RFC 2822 pubDate to UTC Timestamp. Returns None on failure."""
    if not raw:
        return None
    try:
        return pd.Timestamp(parsedate_to_datetime(raw), tz="UTC")
    except Exception:
        return None


@task(retries=3, retry_delay_seconds=30, log_prints=True)
def fetch_feeds(feed_urls: list[str]) -> pd.DataFrame:
    """Fetch and parse RSS feeds. Drops items with missing/unparseable pubDate."""
    logger = get_run_logger()
    rows: list[dict] = []
    ingested_at = pd.Timestamp.now(tz="UTC")

    with httpx.Client(
        headers={"User-Agent": settings.edgar_user_agent or "quant-rss-ingestor/1.0"},
        timeout=20.0,
        follow_redirects=True,
    ) as client:
        for feed_url in feed_urls:
            symbol = _parse_symbol(feed_url)
            try:
                resp = client.get(feed_url)
                resp.raise_for_status()
                xml = resp.text
            except Exception as exc:
                logger.warning("RSS fetch failed for %s: %s", feed_url, exc)
                continue

            items = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
            if not items:
                continue

            for item_xml in items:
                def _tag(name: str) -> str | None:
                    m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", item_xml, re.DOTALL)
                    return m.group(1).strip() if m else None

                pub_raw = _tag("pubDate")
                published_at = _parse_pubdate(pub_raw)
                if published_at is None:
                    # Drop — never substitute ingestion time for publication time.
                    continue

                title = _tag("title") or ""
                description = _tag("description") or ""
                link = _tag("link") or ""
                text = f"{title}. {description}".strip(". ")

                doc_id = hashlib.sha1(
                    f"{feed_url}:{link}:{pub_raw}".encode()
                ).hexdigest()

                rows.append({
                    "document_id": doc_id,
                    "source": f"rss_{symbol.lower()}",
                    "symbol": symbol,
                    "form_type": None,
                    "published_at": published_at,
                    "ingested_at": ingested_at,
                    "text": text,
                    "accession_number": None,
                    "url": link,
                })

    logger.info("Parsed %d RSS items from %d feeds", len(rows), len(feed_urls))
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=[
        "document_id", "source", "symbol", "form_type",
        "published_at", "ingested_at", "text", "accession_number", "url",
    ])


@task
def land_raw(df: pd.DataFrame) -> None:
    lake.write_raw(df, source="rss", dataset=DATASET, dt=date.today())


@task
def to_processed(df: pd.DataFrame) -> int:
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


@flow(name="rss-ingestor")
def rss_flow(feed_urls: list[str] | None = None) -> int:
    """Fetch configured RSS feeds and write to text_documents/."""
    logger = get_run_logger()
    feed_urls = feed_urls or settings.rss_feed_urls
    if not feed_urls:
        logger.info("No RSS feed URLs configured — skipping")
        return 0

    logger.info("RSS ingest: %d feeds", len(feed_urls))
    df = fetch_feeds(feed_urls)
    if df.empty:
        return 0

    land_raw(df)
    return to_processed(df)


if __name__ == "__main__":
    rss_flow()
