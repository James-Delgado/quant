"""One-off historical backfill — run this once, right after setup.

It pulls `backfill_years` of history for every source. After this, the daily
flow only ever fetches the small incremental gap.

    python scripts/backfill.py
"""
from quant.flows.daily import daily_ingest

if __name__ == "__main__":
    print("Starting full historical backfill — this may take a few minutes...")
    status = daily_ingest(backfill=True)
    print(f"Backfill complete: {status}")
