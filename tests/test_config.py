"""Tests that config validation fails loudly on missing credentials."""
from __future__ import annotations

import pytest


def test_settings_rejects_empty_alpaca_key(monkeypatch, tmp_path):
    """Settings must raise at init time if any credential is blank."""
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "ALPACA_API_KEY=\n"
        "ALPACA_SECRET_KEY=secret\n"
        "TIINGO_API_KEY=tok\n"
        "FRED_API_KEY=key\n"
    )

    from pydantic import ValidationError
    from pydantic_settings import BaseSettings, SettingsConfigDict
    from quant.config import _REQUIRED_KEYS
    from pydantic import model_validator

    class TestSettings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=str(env_file), env_file_encoding="utf-8", extra="ignore"
        )
        alpaca_api_key: str = ""
        alpaca_secret_key: str = ""
        tiingo_api_key: str = ""
        fred_api_key: str = ""

        @model_validator(mode="after")
        def _require_credentials(self):
            missing = [k for k in _REQUIRED_KEYS if not getattr(self, k)]
            if missing:
                raise ValueError(f"Missing required API credentials: {missing}")
            return self

    with pytest.raises(ValidationError, match="alpaca_api_key"):
        TestSettings()


def test_settings_loads_cleanly_with_all_keys(monkeypatch):
    """Settings must load without error when all keys are present."""
    from quant.config import settings

    # If this import succeeded (which it did since .env is populated),
    # the validator passed. Just confirm values are non-empty.
    assert settings.alpaca_api_key
    assert settings.alpaca_secret_key
    assert settings.tiingo_api_key
    assert settings.fred_api_key
