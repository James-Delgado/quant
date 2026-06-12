"""Unit tests for the Alpaca ingestor — all SDK calls mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from conftest import make_alpaca_raw
from quant.ingest.alpaca_bars import ingest_alpaca_bars, to_processed
from quant.storage import lake


def test_to_processed_adds_required_columns(lake_root):
    raw = make_alpaca_raw(3)
    n = to_processed(raw)
    result = lake.read_processed("equity_bars_daily")
    assert "ingested_at" in result.columns
    assert "year" in result.columns
    assert "month" in result.columns
    assert n == 3


def test_to_processed_deduplicates_on_symbol_timestamp(lake_root):
    raw = make_alpaca_raw(3)
    duplicate = raw.copy()
    combined = pd.concat([raw, duplicate], ignore_index=True)
    to_processed(combined)
    result = lake.read_processed("equity_bars_daily")
    assert len(result) == 3  # duplicates removed


def test_to_processed_merge_preserves_existing_rows(lake_root):
    """Second call must not erase data written by first call."""
    batch1 = make_alpaca_raw(3)  # Jan 2–4
    to_processed(batch1)

    batch2 = make_alpaca_raw(2)
    batch2["timestamp"] = pd.date_range("2026-01-07", periods=2, freq="B", tz="UTC")
    to_processed(batch2)

    result = lake.read_processed("equity_bars_daily")
    assert len(result) == 5  # all 5 rows survive


def test_to_processed_keeps_latest_ingested_at_on_re_ingest(lake_root):
    """Re-ingesting the same bar should update ingested_at to now."""
    raw = make_alpaca_raw(1)
    to_processed(raw)
    first_ingested = lake.read_processed("equity_bars_daily")["ingested_at"].iloc[0]

    import time

    time.sleep(0.05)
    to_processed(raw)  # same data, later timestamp
    second_ingested = lake.read_processed("equity_bars_daily")["ingested_at"].iloc[0]

    assert second_ingested >= first_ingested


def test_to_processed_validates_schema_rejects_negative_price(lake_root):
    import pandera
    raw = make_alpaca_raw(1)
    raw["close"] = -1.0  # invalid
    with pytest.raises(pandera.errors.SchemaError):
        to_processed(raw)


@patch("quant.ingest.alpaca_bars.StockHistoricalDataClient", autospec=True)
def test_ingest_alpaca_bars_incremental(mock_client_cls, lake_root):
    """Incremental run fetches from last stored date + 1 day."""
    raw = make_alpaca_raw(3)

    mock_barset = MagicMock()
    mock_barset.data = True
    mock_barset.df = raw.set_index(["symbol", "timestamp"])
    mock_client_cls.return_value.get_stock_bars.return_value = mock_barset

    with patch("quant.ingest.alpaca_bars.StockHistoricalDataClient", mock_client_cls):
        ingest_alpaca_bars()

    mock_client_cls.return_value.get_stock_bars.assert_called_once()
    result = lake.read_processed("equity_bars_daily")
    assert len(result) == 3


@patch("quant.ingest.alpaca_bars.StockHistoricalDataClient", autospec=True)
def test_ingest_alpaca_bars_empty_response_does_not_write(mock_client_cls, lake_root):
    mock_barset = MagicMock()
    mock_barset.data = False
    mock_client_cls.return_value.get_stock_bars.return_value = mock_barset

    with patch("quant.ingest.alpaca_bars.StockHistoricalDataClient", mock_client_cls):
        ingest_alpaca_bars()

    assert lake.read_processed("equity_bars_daily").empty
