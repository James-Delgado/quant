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

# ---------------------------------------------------------------------------
# text_documents  (parsed text from EDGAR filings and RSS feeds)
# ---------------------------------------------------------------------------
TEXT_DOCUMENT_SCHEMA = DataFrameSchema(
    {
        "document_id": Column(str, nullable=False),
        "source": Column(str, nullable=False),       # "edgar" | "rss_*"
        "symbol": Column(str, nullable=False),
        "published_at": Column("datetime64[us, UTC]", nullable=False),
        "ingested_at": Column("datetime64[us, UTC]", nullable=False),
        "text": Column(str, nullable=False),
        # Optional metadata — present in EDGAR, may be absent in RSS
        "form_type": Column(str, nullable=True, required=False),
        "accession_number": Column(str, nullable=True, required=False),
        "url": Column(str, nullable=True, required=False),
    },
    coerce=False,
    strict=False,
)

# ---------------------------------------------------------------------------
# sentiment_scored  (FinBERT inference output per document)
# ---------------------------------------------------------------------------
SENTIMENT_SCORED_SCHEMA = DataFrameSchema(
    {
        "document_id": Column(str, nullable=False),
        "symbol": Column(str, nullable=False),
        "published_at": Column("datetime64[us, UTC]", nullable=False),
        "scored_at": Column("datetime64[us, UTC]", nullable=False),
        "model_name": Column(str, nullable=False),
        "model_version": Column(str, nullable=False),
        "sentiment_positive": Column(float, pa.Check.between(0.0, 1.0), nullable=False),
        "sentiment_negative": Column(float, pa.Check.between(0.0, 1.0), nullable=False),
        "sentiment_neutral": Column(float, pa.Check.between(0.0, 1.0), nullable=False),
        # Net score: positive − negative, range [−1, 1]
        "sentiment_score": Column(float, pa.Check.between(-1.0, 1.0), nullable=False),
    },
    coerce=False,
    strict=False,
)
