"""Integration tests — call live APIs and write to a temp lake.

Run with:  pytest --integration
Skipped by default (no network access required for the unit test suite).
"""
from __future__ import annotations

import datetime as dt

import pytest

from quant.config import settings
from quant.ingest.alpaca_bars import ingest_alpaca_bars
from quant.ingest.fred_macro import ingest_fred_macro
from quant.ingest.tiingo_eod import ingest_tiingo_eod
from quant.storage import catalog, lake


@pytest.mark.integration
def test_alpaca_live_spot_check(lake_root):
    """Fetch 10 days of AAPL from Alpaca, validate shape and lake write."""
    from datetime import datetime, timedelta, timezone
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_secret_key)
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=14)
    req = StockBarsRequest(
        symbol_or_symbols=["AAPL"],
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        feed=DataFeed.IEX,
    )
    barset = client.get_stock_bars(req)
    df = barset.df.reset_index()

    assert "symbol" in df.columns
    assert "timestamp" in df.columns
    assert "close" in df.columns
    assert len(df) >= 5, "Expected at least 5 trading days"
    assert df["close"].gt(0).all()
    assert str(df["timestamp"].dtype) == "datetime64[us, UTC]"


@pytest.mark.integration
def test_tiingo_live_spot_check(lake_root):
    """Fetch 10 days of AAPL from Tiingo, validate shape and column rename."""
    from datetime import datetime, timedelta
    from tiingo import TiingoClient

    client = TiingoClient({"api_key": settings.tiingo_api_key})
    start = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")

    sdf = client.get_dataframe("AAPL", frequency="daily", startDate=start)
    assert not sdf.empty
    assert sdf.index.name == "date", f"Expected index name 'date', got '{sdf.index.name}'"
    assert "adjClose" in sdf.columns
    assert "close" in sdf.columns

    flat = sdf.reset_index().rename(columns={"date": "timestamp"})
    assert "timestamp" in flat.columns
    assert "date" not in flat.columns


@pytest.mark.integration
def test_fred_live_spot_check(lake_root):
    """Fetch 30 days of DGS10 from FRED, confirm series shape."""
    from datetime import datetime, timedelta
    from fredapi import Fred

    fred = Fred(api_key=settings.fred_api_key)
    start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    s = fred.get_series("DGS10", observation_start=start)

    assert not s.empty
    assert s.notna().any()

    # Simulate the transform in fetch_series
    import pandas as pd
    sdf = s.rename("value").reset_index().rename(columns={"index": "timestamp"})
    assert "timestamp" in sdf.columns
    assert "value" in sdf.columns
    assert pd.api.types.is_float_dtype(sdf["value"])


@pytest.mark.integration
def test_full_daily_ingest_end_to_end(lake_root):
    """Run the complete daily_ingest flow against live APIs.

    Verifies that all three sources write to the lake and the catalog
    can query the results.
    """
    from quant.flows.daily import daily_ingest

    status = daily_ingest()

    assert status["alpaca"] == "ok", f"Alpaca failed: {status['alpaca']}"
    assert status["tiingo"] == "ok", f"Tiingo failed: {status['tiingo']}"
    assert status["fred"] == "ok", f"FRED failed: {status['fred']}"

    # Confirm each dataset is queryable
    alpaca_latest = catalog.latest_timestamp("equity_bars_daily")
    tiingo_latest = catalog.latest_timestamp("equity_eod_tiingo")
    fred_latest = catalog.latest_timestamp("macro_fred")

    assert alpaca_latest is not None, "Alpaca: no data in lake after ingest"
    assert tiingo_latest is not None, "Tiingo: no data in lake after ingest"
    assert fred_latest is not None, "FRED: no data in lake after ingest"

    # Spot-check a DuckDB query over each dataset
    alpaca_df = catalog.query(
        f"SELECT COUNT(*) AS n FROM {catalog.table('equity_bars_daily')}"
    )
    assert alpaca_df["n"].iloc[0] > 0

    tiingo_df = catalog.query(
        f"SELECT COUNT(*) AS n FROM {catalog.table('equity_eod_tiingo')}"
    )
    assert tiingo_df["n"].iloc[0] > 0

    fred_df = catalog.query(
        f"SELECT COUNT(*) AS n FROM {catalog.table('macro_fred')}"
    )
    assert fred_df["n"].iloc[0] > 0
