"""The data lake: a layered directory of Parquet files.

Layout
------
data/raw/<source>/<dataset>/dt=<YYYY-MM-DD>/data.parquet
    Immutable landing zone. One file per ingestion date, holding every symbol
    pulled that day. Never edited. This is the audit trail and lets you
    rebuild the processed layer without re-hitting any API.

data/processed/<dataset>/year=<YYYY>/month=<MM>/data.parquet
    Cleaned, typed, deduplicated. Hive-partitioned by year/month so files stay
    a useful size (avoids the "thousands of tiny files" problem) and DuckDB can
    prune partitions when querying.

Why one file per day in raw (not per symbol per day): partitioning by symbol
*and* date would create thousands of tiny files, which is slow to scan. One
file per day keeps the count manageable while staying fully idempotent.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from quant.config import settings


def write_raw(df: pd.DataFrame, *, source: str, dataset: str, dt: date) -> Path:
    """Land a raw API pull immutably. Re-running the same day overwrites the
    same file, so the operation is idempotent."""
    path = settings.raw_dir / source / dataset / f"dt={dt.isoformat()}" / "data.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, engine="pyarrow", index=False)
    return path


def write_processed(
    df: pd.DataFrame, *, dataset: str, partition_cols: list[str] | None = None
) -> Path:
    """Write the cleaned layer. `existing_data_behavior='delete_matching'`
    means re-running replaces only the partitions being written — so a
    re-run for May 2026 cannot leave stale May rows behind."""
    base = settings.processed_dir / dataset
    base.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    ds.write_dataset(
        table,
        base,
        format="parquet",
        partitioning=partition_cols or None,
        partitioning_flavor="hive" if partition_cols else None,
        existing_data_behavior="delete_matching" if partition_cols else "overwrite_or_ignore",
        basename_template="part-{i}.parquet",
    )
    return base


def read_processed(dataset: str) -> pd.DataFrame:
    """Load an entire processed dataset into a pandas DataFrame.
    For large datasets prefer querying through quant.storage.catalog instead."""
    base = settings.processed_dir / dataset
    if not base.exists():
        return pd.DataFrame()
    return ds.dataset(base, format="parquet", partitioning="hive").to_table().to_pandas()
