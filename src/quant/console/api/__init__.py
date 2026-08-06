"""Console API (E2) — FastAPI service over the E1 console readers.

Canonical entry points:

    from quant.console.api import create_app   # app factory (tests inject sources)
    python -m quant.console.api                # run locally via uvicorn
"""

from quant.console.api.app import DATA_PREFIX, create_app

__all__ = ["DATA_PREFIX", "create_app"]
