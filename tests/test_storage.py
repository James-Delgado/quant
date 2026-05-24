"""Smoke tests for the storage layer — confirms the lake round-trips data
and the catalog reads it back. Run with: pytest
"""
import datetime as dt

import pandas as pd

from quant.storage import catalog, lake


def test_processed_round_trip():
    df = pd.DataFrame(
        {
            "symbol": ["TEST", "TEST"],
            "timestamp": pd.to_datetime(["2026-01-02", "2026-01-03"], utc=True),
            "close": [100.0, 101.0],
            "year": [2026, 2026],
            "month": [1, 1],
        }
    )
    lake.write_processed(df, dataset="_pytest_bars", partition_cols=["year", "month"])

    back = lake.read_processed("_pytest_bars")
    assert len(back) == 2
    assert set(back["symbol"]) == {"TEST"}

    latest = catalog.latest_timestamp("_pytest_bars")
    assert latest is not None
    assert latest.date() == dt.date(2026, 1, 3)


def test_latest_timestamp_missing_dataset():
    assert catalog.latest_timestamp("_does_not_exist") is None
