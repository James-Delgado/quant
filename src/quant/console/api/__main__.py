"""Run the console API locally via uvicorn.

Canonical invocation:

    python -m quant.console.api [--host HOST] [--port PORT] [--no-monitor]

Binds to 127.0.0.1 by default — the console is single-user and local
(PRD §2 non-goals: no multi-user auth); exposing it beyond localhost is a
deliberate operator override, not a default.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m quant.console.api",
        description="Serve the console view-models live over FastAPI (E2)",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Bind address (default: {DEFAULT_HOST} — localhost only)",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})"
    )
    parser.add_argument(
        "--no-monitor",
        action="store_true",
        help=(
            "Skip the lake-backed feature monitor (the Feature Catalog panel "
            "serves registry-only rows). Mirrors `console export --no-monitor`."
        ),
    )
    args = parser.parse_args(argv)

    # Imported here so `--help` never triggers app/settings imports.
    import uvicorn

    from quant.console.api.app import create_app

    app = create_app(feature_monitor=not args.no_monitor)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
