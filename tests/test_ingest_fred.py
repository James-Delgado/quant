"""Unit tests for the FRED ingestor — all SDK calls mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from conftest import make_fred_raw
from quant.ingest.fred_macro import ingest_fred_macro, to_processed
from quant.storage import lake


def test_to_processed_adds_required_columns(lake_root):
    raw = make_fred_raw(5)
    n = to_processed(raw)
    result = lake.read_processed("macro_fred")
    assert "ingested_at" in result.columns
    assert "series_id" in result.columns
    assert n == 5


def test_to_processed_deduplicates_keeping_latest_ingested(lake_root):
    """Re-pulling a revision should update value via latest ingested_at."""
    first_pull = make_fred_raw(3)
    to_processed(first_pull)

    import time; time.sleep(0.05)
    revised = first_pull.copy()
    revised["value"] = revised["value"] + 999  # revised values
    to_processed(revised)

    result = lake.read_processed("macro_fred")
    assert len(result) == 3
    # All values should be the revised ones (latest ingested_at wins)
    assert all(result["value"] > 900)


def test_to_processed_merges_with_existing_data(lake_root):
    """New observations must be appended, not replace existing ones."""
    early = make_fred_raw(3)
    to_processed(early)

    later = make_fred_raw(3)
    later["timestamp"] = pd.date_range("2026-04-01", periods=3, freq="W", tz="UTC")
    to_processed(later)

    result = lake.read_processed("macro_fred")
    assert len(result) == 6


def test_to_processed_handles_nan_values(lake_root):
    """FRED returns NaN for missing observations; those rows must be dropped."""
    raw = make_fred_raw(5)
    raw.loc[2, "value"] = float("nan")
    to_processed(raw)
    result = lake.read_processed("macro_fred")
    assert result["value"].notna().all()
    assert len(result) == 4


def test_to_processed_validates_schema(lake_root):
    import pandera
    raw = make_fred_raw(1)
    raw["value"] = "not_a_number"  # type violation
    with pytest.raises((pandera.errors.SchemaError, Exception)):
        to_processed(raw)


@patch("quant.ingest.fred_macro.Fred", autospec=True)
def test_ingest_fred_macro_revision_overlap(mock_fred_cls, lake_root):
    """Incremental run must use a 45-day overlap window."""
    from datetime import datetime, timezone, timedelta
    from quant.storage import catalog

    # Seed via to_processed so the lake has a valid ingested_at column and
    # latest_timestamp returns a real date for the incremental window check.
    from quant.ingest.fred_macro import to_processed as _tp
    existing = make_fred_raw(5)
    _tp(existing)

    mock_series = pd.Series(
        [4.5, 4.6],
        index=pd.to_datetime(["2026-01-02", "2026-01-09"]),
        name=None,
    )
    mock_fred_cls.return_value.get_series.return_value = mock_series

    with patch("quant.ingest.fred_macro.Fred", mock_fred_cls):
        ingest_fred_macro()

    call_kwargs = mock_fred_cls.return_value.get_series.call_args
    start_str = call_kwargs.kwargs.get("observation_start") or call_kwargs.args[1]

    last_ts = catalog.latest_timestamp("macro_fred")
    expected_start = (last_ts - timedelta(days=45)).date()
    actual_start = pd.Timestamp(start_str).date()

    assert actual_start <= expected_start, (
        f"Overlap start {actual_start} should be <= {expected_start} "
        "(expected 45-day overlap)"
    )
