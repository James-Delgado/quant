"""Pandera schemas for each processed dataset.

Called at the end of every to_processed() task to catch API drift early —
wrong column names, unexpected types, or null values in required fields
raise a clear SchemaError instead of silently writing garbage to the lake.
"""
from __future__ import annotations

import pandera as pa
from pandera import Column, DataFrameSchema

# ---------------------------------------------------------------------------
# equity_bars_daily  (Alpaca IEX feed, daily OHLCV)
# ---------------------------------------------------------------------------
ALPACA_BARS_SCHEMA = DataFrameSchema(
    {
        "symbol": Column(str, nullable=False),
        "timestamp": Column("datetime64[us, UTC]", nullable=False),
        "open": Column(float, pa.Check.gt(0), nullable=False),
        "high": Column(float, pa.Check.gt(0), nullable=False),
        "low": Column(float, pa.Check.gt(0), nullable=False),
        "close": Column(float, pa.Check.gt(0), nullable=False),
        "volume": Column(float, pa.Check.ge(0), nullable=False),
        # trade_count and vwap are IEX-specific; may be absent on some bars
        "trade_count": Column(float, nullable=True, required=False),
        "vwap": Column(float, nullable=True, required=False),
        "year": Column(int, pa.Check.isin(range(2000, 2100)), nullable=False),
        "month": Column(int, pa.Check.isin(range(1, 13)), nullable=False),
        "ingested_at": Column("datetime64[us, UTC]", nullable=False),
    },
    coerce=False,
    strict=False,  # allow extra columns (SDK may add fields in future versions)
)

# ---------------------------------------------------------------------------
# equity_eod_tiingo  (Tiingo adjusted EOD prices)
# ---------------------------------------------------------------------------
TIINGO_EOD_SCHEMA = DataFrameSchema(
    {
        "symbol": Column(str, nullable=False),
        "timestamp": Column("datetime64[us, UTC]", nullable=False),
        "close": Column(float, nullable=False),
        "high": Column(float, nullable=False),
        "low": Column(float, nullable=False),
        "open": Column(float, nullable=False),
        "volume": Column(pa.Int64, pa.Check.ge(0), nullable=False),
        # Adjusted columns — core value of Tiingo over Alpaca
        "adjClose": Column(float, nullable=False),
        "adjHigh": Column(float, nullable=False),
        "adjLow": Column(float, nullable=False),
        "adjOpen": Column(float, nullable=False),
        "adjVolume": Column(pa.Int64, pa.Check.ge(0), nullable=False),
        "divCash": Column(float, nullable=False),
        "splitFactor": Column(float, pa.Check.gt(0), nullable=False),
        "year": Column(int, pa.Check.isin(range(2000, 2100)), nullable=False),
        "month": Column(int, pa.Check.isin(range(1, 13)), nullable=False),
        "ingested_at": Column("datetime64[us, UTC]", nullable=False),
    },
    coerce=False,
    strict=False,
)

# ---------------------------------------------------------------------------
# macro_fred  (FRED series in long form)
# ---------------------------------------------------------------------------
FRED_MACRO_SCHEMA = DataFrameSchema(
    {
        "timestamp": Column("datetime64[us, UTC]", nullable=False),
        "value": Column(float, nullable=False),
        "series_id": Column(str, nullable=False),
        "ingested_at": Column("datetime64[us, UTC]", nullable=False),
    },
    coerce=False,
    strict=False,
)
