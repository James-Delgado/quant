"""DuckDB query layer over the Parquet lake.

DuckDB is an embedded analytical database — no server, no setup. It reads
Parquet files directly and runs SQL over them, with partition pruning. This is
the read interface for research, backtesting, and the incremental-ingest check.
"""
from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd

from quant.config import settings


def processed_glob(dataset: str) -> str:
    """Glob pattern matching every Parquet file in a processed dataset."""
    return str(settings.processed_dir / dataset / "**" / "*.parquet")


def query(sql: str) -> pd.DataFrame:
    """Run a SQL statement and return a DataFrame. Opens and closes a fresh
    in-memory connection each call — fine for batch jobs and research."""
    con = duckdb.connect()
    try:
        return con.execute(sql).df()
    finally:
        con.close()


def latest_timestamp(dataset: str, ts_col: str = "timestamp") -> dt.datetime | None:
    """Most recent timestamp stored for a dataset, or None if it does not yet
    exist. Ingestors use this to fetch only the gap since the last run."""
    glob = processed_glob(dataset)
    sql = (
        f"SELECT max({ts_col}) AS m "
        f"FROM read_parquet('{glob}', hive_partitioning = true)"
    )
    try:
        result = query(sql)
    except (duckdb.IOException, duckdb.CatalogException):
        return None  # dataset has never been written
    if result.empty or pd.isna(result.iloc[0]["m"]):
        return None
    ts = pd.Timestamp(result.iloc[0]["m"])
    # DuckDB may convert TIMESTAMPTZ to local timezone on the way out. Normalize
    # to UTC so callers get consistent results regardless of system timezone.
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC")
    else:
        ts = ts.tz_localize("UTC")
    return ts.to_pydatetime()


def table(dataset: str) -> str:
    """Convenience snippet to drop into a larger SQL string, e.g.
        catalog.query(f"SELECT * FROM {catalog.table('equity_bars_daily')} "
                       f"WHERE symbol = 'AAPL'")
    """
    return f"read_parquet('{processed_glob(dataset)}', hive_partitioning = true)"
