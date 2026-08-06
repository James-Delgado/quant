"""FastAPI app factory — read endpoints at export-schema parity (E2-M1).

Routes mirror the static export tree one-for-one under ``/data/``: the path the
React data client fetches for ``strategies.json`` is identical whether its base
URL points at ``public/data/`` (static mode) or this service (api mode), so the
E2-M4 data-source swap is a base-URL change with zero view/logic rewrite
(PRD §4 M4, DECISIONS #3).

Parity is by construction, not by convention (METHODOLOGY §6): every endpoint
calls the SAME reader the static export uses (``export.TOP_LEVEL_READERS`` plus
the detail/provenance fan-out readers), serializes through the export's
``sanitize`` step, and validates against the SAME generated schema before
responding. A payload that would fail ``write_export`` fails here too — a 500
instead of a silently malformed response (fail-fast, METHODOLOGY §9).

No new business logic lives here; the app only routes, serializes, and
validates. Auth, ``POST /feedback``, and ``/health`` are E2-M2/M3 scope.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute

from quant.console import readers, schemas
from quant.console.export import (
    MANIFEST_FILENAME,
    TOP_LEVEL_READERS,
    build_manifest,
    sanitize,
)
from quant.console.sources import ConsoleSources

_LOG = logging.getLogger(__name__)

API_TITLE = "quant console API"
API_VERSION = "0.1.0"

# URL prefix for the export-mirror routes. Matches the frontend data client's
# static layout (``public/data/``) so static and api mode share paths.
DATA_PREFIX = "/data"


def _validated(payload: Any, schema: dict, name: str) -> Any:
    """Return ``payload`` iff it passes its export schema; 500 otherwise.

    The same fail-fast contract as ``export.write_export``: a payload that does
    not match the shared schema must never reach the client. Full validation
    errors go to the server log; the response carries only the payload name.
    """
    errors = schemas.validate(payload, schema, name=name)
    if errors:
        _LOG.error("API payload failed schema validation: %s", errors)
        raise HTTPException(status_code=500, detail=f"{name} failed schema validation")
    return payload


def create_app(
    sources: ConsoleSources | None = None,
    *,
    feature_monitor: bool = True,
) -> FastAPI:
    """Build the console API app.

    ``sources`` is injectable for tests. When ``None``, the production
    ``ConsoleSources.default(feature_monitor=...)`` is resolved lazily on the
    FIRST request — never at app-creation time, so importing/constructing the
    app requires no settings or credentials — and memoized for the app's
    lifetime, so the feature monitor's per-instance panel memo is reused across
    requests instead of rebuilding the panel per call. ``feature_monitor``
    mirrors the CLI's ``--no-monitor`` gate (ignored when ``sources`` is given).
    """
    app = FastAPI(title=API_TITLE, version=API_VERSION)
    state: dict[str, ConsoleSources | None] = {"sources": sources}
    # FastAPI runs sync endpoints in a threadpool, so two concurrent first
    # requests could otherwise both resolve the default sources (duplicate
    # feature-panel builds). The lock makes lazy resolution happen exactly once.
    resolve_lock = threading.Lock()

    def get_sources() -> ConsoleSources:
        if state["sources"] is None:
            with resolve_lock:
                if state["sources"] is None:
                    state["sources"] = ConsoleSources.default(feature_monitor=feature_monitor)
        return state["sources"]

    def register_top_level(path: str, reader: Callable[[ConsoleSources], Any]) -> None:
        def endpoint() -> Any:
            payload = sanitize(reader(get_sources()))
            return _validated(payload, schemas.EXPORT_SCHEMAS[path], path)

        # ``name=path`` stamps each route with its export-path identity so the
        # export<->API drift test can assert route-set equality (METHODOLOGY §6).
        app.get(f"{DATA_PREFIX}/{path}", name=path)(endpoint)

    for path, reader in TOP_LEVEL_READERS.items():
        register_top_level(path, reader)

    @app.get(DATA_PREFIX + "/strategy/{strategy_id}.json", name="strategy/")
    def strategy_detail(strategy_id: str) -> Any:
        detail = readers.load_strategy(strategy_id, get_sources())
        if detail is None:
            raise HTTPException(status_code=404, detail=f"unknown strategy: {strategy_id}")
        name = f"strategy/{strategy_id}.json"
        return _validated(sanitize(detail), schemas.STRATEGY_DETAIL_SCHEMA, name)

    @app.get(DATA_PREFIX + "/provenance/{strategy_id}.json", name="provenance/")
    def provenance(strategy_id: str) -> Any:
        prov = readers.load_provenance(strategy_id, get_sources())
        if prov is None:
            raise HTTPException(status_code=404, detail=f"unknown strategy: {strategy_id}")
        name = f"provenance/{strategy_id}.json"
        return _validated(sanitize(prov), schemas.PROVENANCE_SCHEMA, name)

    @app.get(f"{DATA_PREFIX}/{MANIFEST_FILENAME}", name=MANIFEST_FILENAME)
    def manifest() -> Any:
        # Live counterpart of the export's freshness stamp: ``generated_at`` is
        # the request-time clock (via ``sources.now()``), sources carry artifact
        # mtimes — same schema, same friendly labels, no filesystem paths.
        payload = build_manifest(get_sources())
        return _validated(payload, schemas.MANIFEST_SCHEMA, MANIFEST_FILENAME)

    @app.get("/")
    def index() -> dict[str, Any]:
        paths = sorted(
            route.path
            for route in app.routes
            if isinstance(route, APIRoute) and route.path.startswith(DATA_PREFIX)
        )
        return {"service": API_TITLE, "version": API_VERSION, "data_routes": paths}

    return app
