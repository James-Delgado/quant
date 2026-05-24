"""Central configuration. Loaded once from environment / .env file.

Import the singleton everywhere:  from quant.config import settings
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = three levels up from src/quant/config.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- API credentials (filled from .env) ---
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    tiingo_api_key: str = ""
    fred_api_key: str = ""

    # --- Data lake location ---
    data_root: Path = PROJECT_ROOT / "data"

    # --- Universe definitions ---
    # Keep this small to start; widen once the pipeline is proven.
    equity_universe: list[str] = [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
        "SPY", "QQQ", "IWM",  # broad-market ETFs as context
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
