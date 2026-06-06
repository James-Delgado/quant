"""The daily orchestrator — one Prefect flow that runs every ingestor.

Failure isolation: each ingestor is wrapped so that one source failing (an API
outage, an expired key) is logged and does not abort the others. A run that
partially fails still updates the sources that worked.

Run once now:
    python -m quant.flows.daily

Schedule it (long-running process; keep it alive with systemd in production):
    python -m quant.flows.daily --serve
The cron below is 22:30 UTC on weekdays — after the US market close, once the
day's bars have settled. Set your server's clock to UTC and this stays stable
across daylight-saving changes.
"""
from __future__ import annotations

import sys

from prefect import flow, get_run_logger

from quant.ingest.alpaca_bars import ingest_alpaca_bars
from quant.ingest.edgar import edgar_flow
from quant.ingest.fred_macro import ingest_fred_macro
from quant.ingest.rss import rss_flow
from quant.ingest.tiingo_eod import ingest_tiingo_eod

@flow(name="daily-ingest")
def daily_ingest(backfill: bool = False) -> dict[str, str]:
    """Run every ingestor. Returns a per-source status map."""
    logger = get_run_logger()
    status: dict[str, str] = {}
    # Build dict inside the function so that test patches on module-level names
    # (quant.flows.daily.ingest_alpaca_bars etc.) are picked up via globals().
    ingestors = {
        "alpaca": ingest_alpaca_bars,
        "tiingo": ingest_tiingo_eod,
        "fred": ingest_fred_macro,
        "edgar": edgar_flow,
        "rss": rss_flow,
    }
    for name, ingestor in ingestors.items():
        try:
            ingestor(backfill=backfill)
            status[name] = "ok"
        except Exception as exc:  # isolate: one source must not sink the rest
            logger.error(f"[{name}] ingest failed: {exc!r}")
            status[name] = f"failed: {exc!r}"
    logger.info(f"Daily ingest finished: {status}")
    return status


if __name__ == "__main__":
    if "--serve" in sys.argv:
        # Registers a scheduled deployment and blocks, running on the cron.
        daily_ingest.serve(name="daily-ingest", cron="30 22 * * 1-5")
    else:
        backfill = "--backfill" in sys.argv
        daily_ingest(backfill=backfill)
