"""SEC EDGAR filing ingestor — 8-K (material events) and 10-K/10-Q (periodic).

Uses the EDGAR submissions API (data.sec.gov/submissions/) to fetch filings
BY a specific company, identified by CIK. This is more accurate than full-text
search because:
  - Only returns filings filed BY the company (no competitor/analyst noise)
  - Works correctly for single-letter tickers like V (Visa) or IBM
  - Provides primaryDocument directly (no index.json/index.htm lookup needed)
  - One request per filing vs three before

Steps:
    1. Determine date range (incremental or full backfill)
    2. Resolve tickers to CIKs via company_tickers.json (one shared request)
    3. Fetch each company's filing list from the submissions API
    4. Fetch primary document text for each filing in range
    5. Clean, validate, and write to data/processed/text_documents/

SEC policy requires a descriptive User-Agent header with your email address.
Set EDGAR_USER_AGENT in .env — e.g. "James Delgado jado650@berkeley.edu".
See: https://www.sec.gov/os/accessing-edgar-data

Rate limit: SEC enforces 10 requests/second. Each request is preceded by a
0.11s sleep. Per filing: 1 document request. Per symbol: 1–N submissions
requests (N = number of paginated history files). Typical throughput: ~0.2s
per filing vs ~0.44s before.
"""
from __future__ import annotations

import html
import re
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

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def _headers() -> dict[str, str]:
    return {
        "User-Agent": settings.edgar_user_agent,
        "Accept": "application/json",
    }


def _strip_html(raw: str, max_chars: int = 20_000) -> str:
    """Strip HTML/XML tags, decode entities, collapse whitespace, cap length."""
    text = html.unescape(_TAG_RE.sub(" ", raw))
    text = _WS_RE.sub(" ", text).strip()
    return text[:max_chars]


def _load_cik_map(client: httpx.Client, logger) -> dict[str, str]:
    """Fetch ticker → CIK map from EDGAR company_tickers.json.

    Returns {TICKER: cik_string} e.g. {"AAPL": "320193"}.
    One shared request per edgar_flow run.
    """
    try:
        time.sleep(0.11)
        resp = client.get(_TICKERS_URL)
        resp.raise_for_status()
        return {v["ticker"].upper(): str(v["cik_str"]) for v in resp.json().values()}
    except Exception as exc:
        logger.warning("Failed to load CIK map: %s", exc)
        return {}


def _iter_submissions(
    client: httpx.Client,
    cik: str,
    logger,
    form_types: set[str],
    start: datetime,
    end: datetime,
) -> list[dict]:
    """Return filings for a CIK filtered by form type and date range.

    Fetches the main CIK submissions JSON (most recent filings) then paginates
    through any additional history files for older filings. Each returned dict
    has: accession, filed_at (tz-aware), form, primary_doc.
    """
    cik_padded = cik.zfill(10)
    rows: list[dict] = []

    def _parse_block(block: dict) -> None:
        for acc, date_str, form, doc in zip(
            block.get("accessionNumber", []),
            block.get("filingDate", []),
            block.get("form", []),
            block.get("primaryDocument", []),
        ):
            if form not in form_types:
                continue
            try:
                filed_at = pd.Timestamp(date_str, tz="UTC")
            except Exception:
                continue
            if start <= filed_at <= end:
                rows.append({
                    "accession": acc,
                    "filed_at": filed_at,
                    "form": form,
                    "primary_doc": doc or "",
                })

    url = f"{_SUBMISSIONS_BASE}/CIK{cik_padded}.json"
    try:
        time.sleep(0.11)
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Submissions fetch failed for CIK %s: %s", cik, exc)
        return []

    _parse_block(data.get("filings", {}).get("recent", {}))

    # Older filings are paginated into supplemental files, newest-first.
    for file_info in data.get("filings", {}).get("files", []):
        fname = file_info.get("name", "")
        if not fname:
            continue
        # Each file's "date" is the most recent filingDate inside it.
        # If that date precedes our start, all subsequent files are older still.
        file_end = file_info.get("date", "")
        if file_end:
            try:
                if pd.Timestamp(file_end, tz="UTC") < start:
                    break
            except Exception:
                pass
        try:
            time.sleep(0.11)
            resp = client.get(f"{_SUBMISSIONS_BASE}/{fname}")
            resp.raise_for_status()
            older = resp.json()
            # Real SEC paginated files are flat at the top level; tests wrap them
            # in {"filings": {"recent": {...}}} for symmetry — accept both shapes.
            _parse_block(older.get("filings", {}).get("recent", older))
        except Exception as exc:
            logger.warning("Submissions file fetch failed (%s): %s", fname, exc)

    return rows


def _fetch_filing_text(
    client: httpx.Client,
    cik: str,
    accession: str,
    primary_doc: str,
    logger,
    max_chars: int = 20_000,
) -> str:
    """Fetch and strip the primary document for one EDGAR filing.

    Constructs the archive URL directly from CIK + accession + primaryDocument.
    No index lookup needed — the submissions API provides the filename directly.
    """
    if not primary_doc:
        return ""

    acc_nodash = accession.replace("-", "")
    doc_url = f"{_ARCHIVES_BASE}/{cik}/{acc_nodash}/{primary_doc}"

    try:
        time.sleep(0.11)
        resp = client.get(doc_url)
        if resp.status_code == 429:
            logger.warning("Rate limited by SEC (429) — sleeping 60s: %s", doc_url)
            time.sleep(60)
            return ""
        resp.raise_for_status()
        return _strip_html(resp.text, max_chars=max_chars)
    except Exception as exc:
        logger.warning("Document fetch failed (%s): %s", doc_url, exc)
        return ""


@task(retries=3, retry_delay_seconds=60, log_prints=True)
def fetch_filings(
    symbols: list[str],
    start: datetime,
    end: datetime,
    form_types: list[FilingType] | None = None,
) -> pd.DataFrame:
    """Fetch EDGAR filings for a list of ticker symbols via the submissions API.

    Resolves tickers to CIKs via company_tickers.json, then pulls each
    company's own filing history. One HTTP request per filing (document text)
    vs three before (search + index + document).
    published_at is the SEC filing date — authoritative and never revised.
    """
    if form_types is None:
        form_types = ["8-K", "10-K", "10-Q"]
    form_set = set(form_types)

    logger = get_run_logger()
    rows: list[dict] = []
    failed_symbols: list[str] = []

    with httpx.Client(headers=_headers(), timeout=30.0) as client:
        cik_map = _load_cik_map(client, logger)
        if not cik_map:
            logger.warning("CIK map is empty — aborting fetch")
            return pd.DataFrame(columns=[
                "document_id", "source", "symbol", "form_type",
                "published_at", "ingested_at", "text", "accession_number", "url",
            ])

        for symbol in symbols:
            cik = cik_map.get(symbol.upper())
            if not cik:
                logger.warning("No CIK found for %s — skipping", symbol)
                failed_symbols.append(symbol)
                continue

            filings = _iter_submissions(client, cik, logger, form_set, start, end)
            logger.info("  %s (CIK %s): %d filings in range", symbol, cik, len(filings))

            for f in filings:
                text = _fetch_filing_text(
                    client, cik, f["accession"], f["primary_doc"], logger
                )
                acc_nodash = f["accession"].replace("-", "")
                rows.append({
                    "document_id": f["accession"],
                    "source": "edgar",
                    "symbol": symbol,
                    "form_type": f["form"],
                    "published_at": f["filed_at"],
                    "ingested_at": pd.Timestamp.now(tz="UTC"),
                    "text": text,
                    "accession_number": f["accession"],
                    "url": f"{_ARCHIVES_BASE}/{cik}/{acc_nodash}/{f['primary_doc']}",
                })

    if failed_symbols:
        logger.warning(
            "No CIK for %d/%d symbols: %s",
            len(failed_symbols), len(symbols), failed_symbols,
        )
    empty_text = sum(1 for r in rows if not r["text"])
    if empty_text:
        logger.warning(
            "%d/%d filings had empty text after document fetch",
            empty_text, len(rows),
        )
    logger.info("Fetched %d total filings for %d symbols", len(rows), len(symbols))
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

    logger = get_run_logger()
    df = df.copy()
    df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
    df["ingested_at"] = pd.to_datetime(df["ingested_at"], utc=True)
    df = df.dropna(subset=["published_at", "symbol", "text"])

    before = len(df)
    df = df[df["text"].str.strip().str.len() > 0]
    dropped = before - len(df)
    if dropped:
        logger.warning(
            "Dropped %d/%d rows with empty text — possible document fetch or API failure",
            dropped, before,
        )
    if df.empty:
        logger.warning(
            "All fetched rows had empty text — check EDGAR_USER_AGENT and API connectivity"
        )
        return 0

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
        # 7-day overlap: EDGAR sometimes back-dates amended filing dates.
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
