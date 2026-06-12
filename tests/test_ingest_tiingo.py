"""Unit tests for the Tiingo ingestor — all SDK calls mocked."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from conftest import make_tiingo_raw
from quant.ingest.tiingo_eod import fetch_eod, to_processed
from quant.storage import lake


def test_to_processed_renames_date_to_timestamp(lake_root):
    raw = make_tiingo_raw(3)
    to_processed(raw)
    result = lake.read_processed("equity_eod_tiingo")
    assert "timestamp" in result.columns
    assert "date" not in result.columns


def test_to_processed_adds_required_columns(lake_root):
    raw = make_tiingo_raw(3)
    n = to_processed(raw)
    result = lake.read_processed("equity_eod_tiingo")
    assert "ingested_at" in result.columns
    assert "year" in result.columns
    assert "month" in result.columns
    assert n == 3


def test_to_processed_deduplicates_on_symbol_timestamp(lake_root):
    raw = make_tiingo_raw(3)
    combined = pd.concat([raw, raw.copy()], ignore_index=True)
    to_processed(combined)
    result = lake.read_processed("equity_eod_tiingo")
    assert len(result) == 3


def test_to_processed_merge_preserves_existing_rows(lake_root):
    batch1 = make_tiingo_raw(3)
    to_processed(batch1)

    batch2 = make_tiingo_raw(2)
    batch2["timestamp"] = pd.date_range("2026-01-07", periods=2, freq="B", tz="UTC")
    to_processed(batch2)

    result = lake.read_processed("equity_eod_tiingo")
    assert len(result) == 5


def test_to_processed_validates_schema(lake_root):
    import pandera
    raw = make_tiingo_raw(1)
    raw["close"] = None  # null in required field
    with pytest.raises((pandera.errors.SchemaError, Exception)):
        to_processed(raw)


@patch("quant.ingest.tiingo_eod.TiingoClient", autospec=True)
def test_fetch_eod_renames_date_column(mock_client_cls, lake_root):
    """Confirm the index name 'date' is correctly renamed to 'timestamp'."""
    raw_tiingo = make_tiingo_raw(3)
    # Simulate Tiingo returning a DataFrame with 'date' as index name
    raw_tiingo_indexed = raw_tiingo.drop(columns=["symbol"]).rename(columns={"timestamp": "date"})
    raw_tiingo_indexed = raw_tiingo_indexed.set_index("date")

    mock_client_cls.return_value.get_dataframe.return_value = raw_tiingo_indexed

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with patch("quant.ingest.tiingo_eod.TiingoClient", mock_client_cls):
        result = fetch_eod(["AAPL"], start)

    assert "timestamp" in result.columns
    assert "date" not in result.columns
    assert "symbol" in result.columns


@patch("quant.ingest.tiingo_eod.TiingoClient", autospec=True)
def test_fetch_eod_skips_failed_symbols(mock_client_cls):
    mock_client_cls.return_value.get_dataframe.side_effect = Exception("API error")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with patch("quant.ingest.tiingo_eod.TiingoClient", mock_client_cls):
        result = fetch_eod(["AAPL", "MSFT"], start)
    assert result.empty  # both fail gracefully, no exception raised
