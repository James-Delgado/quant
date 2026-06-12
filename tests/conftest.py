"""Shared pytest fixtures.

Key design:
- All tests that touch the lake use tmp_path + a patched settings object so
  they never write to the real data/ directory.
- Integration tests are gated behind @pytest.mark.integration and skipped
  unless --integration is passed or the INTEGRATION env var is set.
- Mock factories for each SDK live here so individual test files stay lean.
"""
from __future__ import annotations


from pathlib import Path

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Prefect test harness (session-scoped: one SQLite server per test run)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def prefect_harness():
    """Run all tests against a temporary in-memory Prefect backend.

    Session scope means the server starts once for the whole pytest run, not
    once per test. Without this, each @flow call would spin up and tear down
    its own ephemeral server (3+ seconds of overhead per flow test).
    """
    from prefect.testing.utilities import prefect_test_harness

    with prefect_test_harness():
        yield


# ---------------------------------------------------------------------------
# Integration test gate
# ---------------------------------------------------------------------------

def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="Run tests that call live external APIs (requires .env credentials).",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: mark test as requiring live API credentials (skipped by default).",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--integration"):
        return
    skip = pytest.mark.skip(reason="Pass --integration to run live API tests.")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


# ---------------------------------------------------------------------------
# Isolated lake fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def lake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Patch settings.data_root to a temp directory for the duration of a test.

    All lake.write_* and catalog.* calls in the test will hit this temp path
    instead of the real data/ directory.
    """
    from quant import config as cfg

    monkeypatch.setattr(cfg.settings, "data_root", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Sample DataFrames matching live API shapes (confirmed 2026-05-24)
# ---------------------------------------------------------------------------

def make_alpaca_raw(n: int = 3) -> pd.DataFrame:
    """Minimal Alpaca bars DataFrame as returned by barset.df.reset_index()."""
    dates = pd.date_range("2026-01-02", periods=n, freq="B", tz="UTC")
    return pd.DataFrame(
        {
            "symbol": ["AAPL"] * n,
            "timestamp": dates,
            "open": [180.0 + i for i in range(n)],
            "high": [185.0 + i for i in range(n)],
            "low": [179.0 + i for i in range(n)],
            "close": [182.0 + i for i in range(n)],
            "volume": [1_000_000.0] * n,
            "trade_count": [20_000.0] * n,
            "vwap": [181.5 + i for i in range(n)],
        }
    )


def make_tiingo_raw(n: int = 3) -> pd.DataFrame:
    """Minimal Tiingo EOD DataFrame as returned by the client (post reset_index/rename)."""
    dates = pd.date_range("2026-01-02", periods=n, freq="B", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": dates,
            "close": [182.0 + i for i in range(n)],
            "high": [185.0 + i for i in range(n)],
            "low": [179.0 + i for i in range(n)],
            "open": [180.0 + i for i in range(n)],
            "volume": [1_000_000] * n,
            "adjClose": [182.0 + i for i in range(n)],
            "adjHigh": [185.0 + i for i in range(n)],
            "adjLow": [179.0 + i for i in range(n)],
            "adjOpen": [180.0 + i for i in range(n)],
            "adjVolume": [1_000_000] * n,
            "divCash": [0.0] * n,
            "splitFactor": [1.0] * n,
            "symbol": ["AAPL"] * n,
        }
    )


def make_fred_raw(n: int = 5) -> pd.DataFrame:
    """Minimal FRED long-form DataFrame as produced by fetch_series."""
    dates = pd.date_range("2026-01-02", periods=n, freq="W", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": dates,
            "value": [4.5 + i * 0.1 for i in range(n)],
            "series_id": ["DGS10"] * n,
        }
    )
