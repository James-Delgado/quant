"""Endpoint tests for the E2 console API (E2-M1).

The load-bearing contract is export<->API parity (METHODOLOGY §6, both
directions): every file the static export produces is served byte-equivalently
by a live endpoint, and every ``/data`` route corresponds to an export path —
no API-only payloads, no unserved export files. All tests run on the same
synthetic ``tmp_path`` sources as ``tests/test_console.py`` (via
``make_sources``), so parity is asserted over identical artifacts.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from test_console import make_sources

from quant.console import export as export_mod
from quant.console import schemas
from quant.console.api import DATA_PREFIX, create_app
from quant.console.api.app import API_TITLE
from quant.console.export import (
    MANIFEST_FILENAME,
    TOP_LEVEL_READERS,
    build_export,
    build_manifest,
)
from quant.console.sources import ConsoleSources


@pytest.fixture
def sources(tmp_path: Path) -> ConsoleSources:
    return make_sources(tmp_path)


@pytest.fixture
def client(sources: ConsoleSources) -> TestClient:
    return TestClient(create_app(sources))


# ── Export <-> API parity (the M1 acceptance criterion) ──────────────────────


def test_every_export_payload_served_identically(sources, client):
    """Each export file's live endpoint returns the exact same payload.

    Byte-level parity is stronger than schema parity: both surfaces serialize
    the same reader output through the same ``sanitize`` step, so the JSON
    bodies must be equal, not merely schema-compatible.
    """
    export = build_export(sources)
    assert export  # non-vacuous: the synthetic sources produce payloads
    for path, payload in export.items():
        res = client.get(f"{DATA_PREFIX}/{path}")
        assert res.status_code == 200, path
        assert res.json() == payload, f"API payload diverges from export for {path}"


def test_every_response_validates_against_the_export_schema(sources, client):
    export = build_export(sources)
    for path in export:
        schema = export_mod._schema_for_path(path)
        assert schema is not None, path
        errors = schemas.validate(client.get(f"{DATA_PREFIX}/{path}").json(), schema, name=path)
        assert errors == [], path


def test_route_set_matches_export_contract(sources):
    """Drift test, both directions (METHODOLOGY §6).

    (1) The app's ``/data`` routes are exactly the export contract: the shared
    top-level table + the two fan-out namespaces + the manifest. (2) Every path
    ``build_export`` can produce maps onto one of those routes — the export tree
    cannot grow a file the API does not serve without this test going red.
    """
    app = create_app(sources)
    route_names = {
        r.name for r in app.routes if isinstance(r, APIRoute) and r.path.startswith(DATA_PREFIX)
    }
    expected = set(TOP_LEVEL_READERS) | {"strategy/", "provenance/", MANIFEST_FILENAME}
    assert route_names == expected

    for path in build_export(sources):
        assert path in TOP_LEVEL_READERS or path.startswith(("strategy/", "provenance/")), (
            f"export produces {path} but no API route serves it"
        )


def test_manifest_endpoint_matches_build_manifest(sources, client):
    res = client.get(f"{DATA_PREFIX}/{MANIFEST_FILENAME}")
    assert res.status_code == 200
    body = res.json()
    # Deterministic under the fixture's injected clock → exact equality holds.
    assert body == build_manifest(sources)
    assert body["generated_at"] == "2026-06-28T00:00:00Z"
    assert schemas.validate(body, schemas.MANIFEST_SCHEMA, name=MANIFEST_FILENAME) == []
    labels = [s["source"] for s in body["sources"]]
    assert export_mod.LEDGER_SOURCE_LABEL in labels  # friendly labels, never paths


# ── Error contracts ──────────────────────────────────────────────────────────


def test_unknown_strategy_detail_is_404(client):
    res = client.get(f"{DATA_PREFIX}/strategy/nope.json")
    assert res.status_code == 404
    assert "nope" in res.json()["detail"]


def test_unknown_provenance_is_404(client):
    assert client.get(f"{DATA_PREFIX}/provenance/nope.json").status_code == 404


def test_invalid_payload_is_500_not_silently_served(sources, monkeypatch):
    """A reader payload that fails the shared schema must 500 (fail-fast).

    Mirrors ``write_export``'s ValueError: a malformed payload never reaches
    the client as a 200.
    """
    monkeypatch.setitem(TOP_LEVEL_READERS, "market.json", lambda s: {"bogus": 1})
    client = TestClient(create_app(sources))
    res = client.get(f"{DATA_PREFIX}/market.json")
    assert res.status_code == 500
    assert "market.json" in res.json()["detail"]


# ── App factory behaviour ────────────────────────────────────────────────────


def test_default_sources_resolved_lazily_and_memoized(tmp_path, monkeypatch):
    """``create_app()`` must not touch settings; sources resolve on first request.

    The default is resolved exactly once (memoized) so the feature monitor's
    per-instance panel memo is reused across requests, and the
    ``feature_monitor`` flag is forwarded.
    """
    calls: list[bool] = []
    real = make_sources(tmp_path)

    def fake_default(cls, *, feature_monitor: bool = True):
        calls.append(feature_monitor)
        return real

    monkeypatch.setattr(ConsoleSources, "default", classmethod(fake_default))
    app = create_app(feature_monitor=False)
    assert calls == []  # nothing resolved at creation time
    client = TestClient(app)
    assert client.get(f"{DATA_PREFIX}/market.json").status_code == 200
    assert client.get(f"{DATA_PREFIX}/market.json").status_code == 200
    assert calls == [False]  # resolved once, flag forwarded


def test_injected_sources_bypass_default(sources, monkeypatch):
    def boom(cls, **kwargs):  # pragma: no cover - would fail the test if called
        raise AssertionError("default() must not be called when sources are injected")

    monkeypatch.setattr(ConsoleSources, "default", classmethod(boom))
    client = TestClient(create_app(sources))
    assert client.get(f"{DATA_PREFIX}/strategies.json").status_code == 200


def test_index_lists_every_data_route(sources, client):
    body = client.get("/").json()
    assert body["service"] == API_TITLE
    assert f"{DATA_PREFIX}/strategies.json" in body["data_routes"]
    assert f"{DATA_PREFIX}/{MANIFEST_FILENAME}" in body["data_routes"]
    app = create_app(sources)
    n_data_routes = sum(
        1 for r in app.routes if isinstance(r, APIRoute) and r.path.startswith(DATA_PREFIX)
    )
    assert len(body["data_routes"]) == n_data_routes


# ── CLI entry point ──────────────────────────────────────────────────────────


def test_cli_main_passes_args_to_uvicorn(monkeypatch):
    import uvicorn

    from quant.console.api import __main__ as api_main

    captured: dict = {}

    def fake_run(app, host, port):
        captured.update(app=app, host=host, port=port)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    assert api_main.main(["--port", "9001", "--no-monitor"]) == 0
    assert captured["host"] == api_main.DEFAULT_HOST  # localhost-only default
    assert captured["port"] == 9001
    assert isinstance(captured["app"], FastAPI)
