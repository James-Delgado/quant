"""Unit tests for the daily orchestration flow."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from quant.flows.daily import daily_ingest


def test_daily_ingest_returns_status_map():
    with (
        patch("quant.flows.daily.ingest_alpaca_bars") as mock_alpaca,
        patch("quant.flows.daily.ingest_tiingo_eod") as mock_tiingo,
        patch("quant.flows.daily.ingest_fred_macro") as mock_fred,
    ):
        status = daily_ingest()

    assert set(status.keys()) == {"alpaca", "tiingo", "fred"}
    assert all(v == "ok" for v in status.values())


def test_daily_ingest_isolates_failures():
    """One failing source must not prevent the others from running."""
    with (
        patch("quant.flows.daily.ingest_alpaca_bars", side_effect=RuntimeError("API down")),
        patch("quant.flows.daily.ingest_tiingo_eod") as mock_tiingo,
        patch("quant.flows.daily.ingest_fred_macro") as mock_fred,
    ):
        status = daily_ingest()

    assert status["alpaca"].startswith("failed:")
    assert status["tiingo"] == "ok"
    assert status["fred"] == "ok"
    mock_tiingo.assert_called_once()
    mock_fred.assert_called_once()


def test_daily_ingest_all_fail_still_returns_map():
    """Even if every source fails, we get a status dict not an exception."""
    err = RuntimeError("network down")
    with (
        patch("quant.flows.daily.ingest_alpaca_bars", side_effect=err),
        patch("quant.flows.daily.ingest_tiingo_eod", side_effect=err),
        patch("quant.flows.daily.ingest_fred_macro", side_effect=err),
    ):
        status = daily_ingest()

    assert all(v.startswith("failed:") for v in status.values())
