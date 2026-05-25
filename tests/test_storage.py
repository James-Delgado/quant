"""Tests for the storage layer (lake + catalog).

All tests use the `lake_root` fixture from conftest.py to avoid writing to
the real data/ directory.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from quant.storage import catalog, lake


# ---------------------------------------------------------------------------
# lake.write_raw / lake.write_processed / lake.read_processed
# ---------------------------------------------------------------------------

def test_write_raw_creates_file(lake_root, tmp_path):
    df = pd.DataFrame({"symbol": ["AAPL"], "close": [182.0]})
    path = lake.write_raw(df, source="test", dataset="bars", dt=dt.date(2026, 1, 2))
    assert path.exists()
    assert path.suffix == ".parquet"


def test_write_raw_is_idempotent(lake_root):
    df1 = pd.DataFrame({"close": [100.0]})
    df2 = pd.DataFrame({"close": [200.0]})  # different data, same key
    lake.write_raw(df1, source="s", dataset="d", dt=dt.date(2026, 1, 2))
    lake.write_raw(df2, source="s", dataset="d", dt=dt.date(2026, 1, 2))

    import pyarrow.parquet as pq
    from quant.config import settings
    path = settings.raw_dir / "s" / "d" / "dt=2026-01-02" / "data.parquet"
    result = pq.read_table(path).to_pandas()
    assert result["close"].iloc[0] == 200.0  # second write wins


def test_processed_round_trip(lake_root):
    df = pd.DataFrame(
        {
            "symbol": ["AAPL", "AAPL"],
            "timestamp": pd.to_datetime(["2026-01-02", "2026-01-03"], utc=True),
            "close": [182.0, 183.0],
            "year": [2026, 2026],
            "month": [1, 1],
        }
    )
    lake.write_processed(df, dataset="bars", partition_cols=["year", "month"])
    back = lake.read_processed("bars")
    assert len(back) == 2
    assert set(back["symbol"]) == {"AAPL"}


def test_read_processed_missing_dataset_returns_empty(lake_root):
    result = lake.read_processed("does_not_exist")
    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_write_processed_preserves_existing_data_across_months(lake_root):
    """Regression test for the delete_matching partition bug.

    Writing January data and then writing February data must not erase January.
    The merge-and-rewrite pattern in the ingestors prevents this, but we test
    the storage layer behaviour directly here.
    """
    jan = pd.DataFrame(
        {
            "symbol": ["AAPL"],
            "timestamp": pd.to_datetime(["2026-01-15"], utc=True),
            "close": [180.0],
            "year": [2026],
            "month": [1],
        }
    )
    feb = pd.DataFrame(
        {
            "symbol": ["AAPL"],
            "timestamp": pd.to_datetime(["2026-02-15"], utc=True),
            "close": [190.0],
            "year": [2026],
            "month": [2],
        }
    )
    lake.write_processed(jan, dataset="bars", partition_cols=["year", "month"])
    lake.write_processed(feb, dataset="bars", partition_cols=["year", "month"])

    back = lake.read_processed("bars")
    assert len(back) == 2, "Both months must survive independent writes"


def test_write_processed_no_partition(lake_root):
    df = pd.DataFrame({"series_id": ["DGS10"], "value": [4.5]})
    lake.write_processed(df, dataset="macro", partition_cols=None)
    back = lake.read_processed("macro")
    assert len(back) == 1


# ---------------------------------------------------------------------------
# catalog.latest_timestamp
# ---------------------------------------------------------------------------

def test_latest_timestamp_happy_path(lake_root):
    df = pd.DataFrame(
        {
            "symbol": ["AAPL", "AAPL"],
            "timestamp": pd.to_datetime(["2026-01-02", "2026-01-05"], utc=True),
            "close": [180.0, 181.0],
            "year": [2026, 2026],
            "month": [1, 1],
        }
    )
    lake.write_processed(df, dataset="bars", partition_cols=["year", "month"])
    latest = catalog.latest_timestamp("bars")
    assert latest is not None
    assert latest.date() == dt.date(2026, 1, 5)


def test_latest_timestamp_missing_dataset_returns_none(lake_root):
    assert catalog.latest_timestamp("does_not_exist") is None


def test_latest_timestamp_empty_dataset_returns_none(lake_root):
    df = pd.DataFrame({"timestamp": pd.Series([], dtype="datetime64[us, UTC]"), "year": [], "month": []})
    lake.write_processed(df, dataset="empty_bars", partition_cols=["year", "month"])
    assert catalog.latest_timestamp("empty_bars") is None


# ---------------------------------------------------------------------------
# catalog.query
# ---------------------------------------------------------------------------

def test_catalog_query(lake_root):
    df = pd.DataFrame(
        {
            "symbol": ["AAPL"],
            "timestamp": pd.to_datetime(["2026-01-02"], utc=True),
            "close": [182.0],
            "year": [2026],
            "month": [1],
        }
    )
    lake.write_processed(df, dataset="bars", partition_cols=["year", "month"])

    result = catalog.query(
        f"SELECT symbol, close FROM {catalog.table('bars')} WHERE symbol = 'AAPL'"
    )
    assert len(result) == 1
    assert result["close"].iloc[0] == pytest.approx(182.0)
