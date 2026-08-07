"""Console API (E2) — FastAPI service over the E1 console readers.

Canonical entry points:

    from quant.console.api import create_app   # app factory (tests inject sources)
    python -m quant.console.api                # run locally via uvicorn
"""

from quant.console.api.app import (
    DATA_PREFIX,
    FEEDBACK_PATH,
    HEALTH_PATH,
    RECOMPUTE_PATH,
    TOKEN_ENV_VAR,
    create_app,
)

__all__ = [
    "DATA_PREFIX",
    "FEEDBACK_PATH",
    "HEALTH_PATH",
    "RECOMPUTE_PATH",
    "TOKEN_ENV_VAR",
    "create_app",
]
