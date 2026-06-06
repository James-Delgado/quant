"""Central configuration. Loaded once from environment / .env file.

Import the singleton everywhere:  from quant.config import settings
"""
from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = three levels up from src/quant/config.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_REQUIRED_KEYS = ("alpaca_api_key", "alpaca_secret_key", "tiingo_api_key", "fred_api_key")
# edgar_user_agent is validated lazily in ingest/edgar.py — not required until
# Phase 3 ingestion runs, so it doesn't break existing environments.


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- API credentials (filled from .env) ---
    # No defaults: missing keys raise at startup, not at the first API call.
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    tiingo_api_key: str = ""
    fred_api_key: str = ""

    # --- Phase 3: text ingestion + sentiment ---
    # SEC EDGAR requires a User-Agent header containing your email address.
    # See: https://www.sec.gov/os/accessing-edgar-data
    edgar_user_agent: str = ""
    # RSS feed URLs to poll for financial news. Symbol-specific feeds
    # (e.g. Yahoo Finance ?s=AAPL) are listed by the ingestor at runtime.
    rss_feed_urls: list[str] = [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US",
    ]
    # FinBERT inference device: "auto" | "mps" | "cuda" | "cpu"
    # "auto" selects mps > cuda > cpu at runtime.
    finbert_device: str = "auto"

    @model_validator(mode="after")
    def _require_credentials(self) -> "Settings":
        missing = [k for k in _REQUIRED_KEYS if not getattr(self, k)]
        if missing:
            raise ValueError(
                f"Missing required API credentials: {missing}. "
                "Copy .env.example to .env and fill in your keys."
            )
        return self

    # --- Data lake location ---
    data_root: Path = PROJECT_ROOT / "data"

    # --- Universe definitions ---
    # All 30 Dow Jones Industrial Average components (as of 2025) plus
    # broad-market ETFs for regime context.
    equity_universe: list[str] = [
        # DJIA 30
        "AAPL", "AMGN", "AMZN", "AXP",  "BA",   "CAT",  "CRM",  "CSCO",
        "CVX",  "DIS",  "GS",   "HD",   "HON",  "IBM",  "JNJ",  "JPM",
        "KO",   "MCD",  "MMM",  "MRK",  "MSFT", "NKE",  "NVDA", "PG",
        "SHW",  "TRV",  "UNH",  "V",    "VZ",   "WMT",
        # Broad-market ETFs
        "SPY", "QQQ", "IWM",
    ]
    # FRED macro series: 10y yield, fed funds, VIX, CPI, unemployment.
    fred_series: list[str] = ["DGS10", "DFF", "VIXCLS", "CPIAUCSL", "UNRATE"]

    # How far back to pull on a first-time (backfill) run.
    backfill_years: int = 5

    @property
    def raw_dir(self) -> Path:
        return self.data_root / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_root / "processed"


settings = Settings()
