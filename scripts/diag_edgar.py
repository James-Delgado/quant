"""One-time diagnostic: probe the EDGAR EFTS API to find why searches 500."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from quant.config import settings

HEADERS = {"User-Agent": settings.edgar_user_agent, "Accept": "application/json"}


def get(client, url, label):
    print(f"\n{'─'*60}\n[{label}]\n  {url}")
    try:
        r = client.get(url)
        print(f"  status={r.status_code}")
        if r.status_code >= 400:
            print(f"  body: {r.text[:400]}")
            return None
        data = r.json()
        hits = data.get("hits", {}).get("hits", [])
        total = data.get("hits", {}).get("total", {})
        print(f"  total={total}  page_hits={len(hits)}")
        if hits:
            src = hits[0].get("_source", {})
            print(f"  first._source keys: {list(src.keys())}")
            print(f"  first.file_url: {src.get('file_url','—')[:80]}")
        return data
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return None


with httpx.Client(headers=HEADERS, timeout=30.0) as c:

    # baseline: does the API work at all?
    get(c, "https://efts.sec.gov/LATEST/search-index?q=%22AAPL%22&forms=8-K&size=1",
        "AAPL 8-K — no date range (baseline)")
    time.sleep(0.15)

    # 20-year range on a long ticker
    get(c, "https://efts.sec.gov/LATEST/search-index?q=%22AAPL%22&forms=8-K"
           "&dateRange=custom&startdt=2006-06-01&enddt=2026-06-06&size=1",
        "AAPL 8-K — 20yr range")
    time.sleep(0.15)

    # short ticker with no date range
    get(c, "https://efts.sec.gov/LATEST/search-index?q=%22BA%22&forms=8-K&size=1",
        "BA 8-K — no date range")
    time.sleep(0.15)

    # exact failing request
    get(c, "https://efts.sec.gov/LATEST/search-index?q=%22BA%22&forms=8-K"
           "&dateRange=custom&startdt=2006-06-01&enddt=2026-06-06&size=1",
        "BA 8-K — 20yr range (failing case)")
    time.sleep(0.15)

    # alternative: company submissions API (doesn't use full-text search)
    get(c, "https://data.sec.gov/submissions/CIK0000012927.json",
        "Boeing submissions API — CIK-based, no full-text search")
    time.sleep(0.15)

    # ticker → CIK lookup table
    get(c, "https://www.sec.gov/files/company_tickers.json",
        "company_tickers.json — ticker→CIK map")
