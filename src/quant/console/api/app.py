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
validates. ``POST /feedback`` (E2-M2) delegates to the E1-M6 service layer
(``quant.console.feedback``) — the frozen ``FeedbackReport`` dataclass is the
single payload contract shared with the frontend modal, and the GitHub
credential stays server-side (the ``gh`` CLI's auth; never in the client).

E2-M3 adds ``GET /health`` and ``POST /recompute``:

* ``/health`` reports per-feed freshness judged against the **pinned C1 SLA
  table** — it consumes the ``SOURCE_SLAS`` / ``evaluate_feed`` machinery from
  ``scripts/monitor_freshness.py`` (C1-M3), not the Data panel's simpler
  age-threshold view, so the API and the cron monitor cannot disagree about
  what "fresh" means (one SLA source of truth, METHODOLOGY §6).
* ``/recompute`` is the on-demand refresh: it drops the app's memoized default
  sources (so the next request re-resolves the lake / feature-monitor state)
  and can optionally re-write the static export tree server-side — no manual
  ``console export`` step.

Auth (E2-M3): one simple local bearer token (``CONSOLE_API_TOKEN`` env var, or
the ``auth_token`` factory argument) shared by every write/mutate route.
``/recompute`` is fail-closed — with no token configured it refuses (403)
rather than running unauthenticated. ``POST /feedback`` predates the token and
stays open when none is configured (acceptable under the localhost-only default
bind, PRD §2 non-goals) but requires the SAME token as soon as one is set, so
all mutating routes share one auth story (E2-M3 discovery note).

CORS (E2-M4): the vite dev server (and a built ``dist/`` served from any other
local origin) runs on a different origin than this API, so api-mode fetches
need CORS. Origins come from ``CONSOLE_API_CORS_ORIGINS`` (comma-separated) or
the ``cors_origins`` factory argument, defaulting to the vite dev origins;
configuring an empty value disables cross-origin access entirely. Single-origin
static mode never needed CORS, and still doesn't.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import secrets
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from quant.console import feedback, readers, schemas
from quant.console.export import (
    MANIFEST_FILENAME,
    TOP_LEVEL_READERS,
    build_manifest,
    sanitize,
    write_export,
)
from quant.console.sources import ConsoleSources

_LOG = logging.getLogger(__name__)

API_TITLE = "quant console API"
API_VERSION = "0.1.0"

# URL prefix for the export-mirror routes. Matches the frontend data client's
# static layout (``public/data/``) so static and api mode share paths.
DATA_PREFIX = "/data"

# The write/operational endpoints live OUTSIDE /data — they are not part of the
# export-mirror contract the route-set drift test pins (METHODOLOGY §6).
FEEDBACK_PATH = "/feedback"
HEALTH_PATH = "/health"
RECOMPUTE_PATH = "/recompute"

# Environment variable holding the simple local bearer token (PRD §2: single
# user, "a simple local token is sufficient"). Pinned name per METHODOLOGY §1;
# documented in docs/ENV.md. Read at request time so tests (and an operator
# exporting the var before launch) control it without rebuilding the app.
TOKEN_ENV_VAR = "CONSOLE_API_TOKEN"

# Environment variable holding the comma-separated CORS allow-list (E2-M4).
# Unset → the vite dev-server origins below, so `npm run dev` against a local
# API works out of the box; set-but-empty → CORS disabled. Pinned name per
# METHODOLOGY §1; documented in docs/ENV.md. Unlike the token this is read at
# app-creation time — middleware cannot be added per request.
CORS_ENV_VAR = "CONSOLE_API_CORS_ORIGINS"

# Vite's default dev origin, both spellings — browsers treat localhost and
# 127.0.0.1 as distinct origins.
DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")

# ``gh issue create`` prints the new issue's URL; the trailing number is what
# the promotion path needs.
_ISSUE_NUMBER_RE = re.compile(r"/issues/(\d+)/?$")


def parse_issue_number(issue_url: str) -> int | None:
    """Extract the issue number from a GitHub issue URL, or ``None``."""
    match = _ISSUE_NUMBER_RE.search(issue_url.strip())
    return int(match.group(1)) if match else None


def resolve_cors_origins(cors_origins: list[str] | None = None) -> list[str]:
    """The CORS allow-list: the factory argument, else the env var, else defaults.

    The env var is comma-separated; entries are stripped and empties dropped,
    so ``CONSOLE_API_CORS_ORIGINS=""`` (or an explicit ``[]``) disables CORS
    outright rather than silently falling back to the defaults (METHODOLOGY §9
    — an operator who turned cross-origin access off gets it off).
    """
    if cors_origins is not None:
        return list(cors_origins)
    raw = os.environ.get(CORS_ENV_VAR)
    if raw is None:
        return list(DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _iso_utc(value: dt.datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SSZ`` (UTC, second precision) — the manifest's format."""
    utc = value.astimezone(dt.timezone.utc) if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_monitor_module() -> Any:
    """Load ``scripts/monitor_freshness.py`` as a module (scripts is not a package).

    The same importlib-by-file-path pattern ``scripts/trade_daily.py`` and the
    monitor's own tests use. The path is resolved relative to the installed
    ``quant`` package (src layout → repo root), so it works wherever the
    editable install runs from. A previously loaded instance (e.g. by the test
    suite or ``trade_daily``) is reused via ``sys.modules`` rather than
    re-executed. Raises ``RuntimeError`` when the script cannot be found/loaded
    — the caller turns that into an honest 503, never fabricated freshness
    (METHODOLOGY §9).

    Layering note: a package importing a repo script is inverted; promoting the
    pure SLA core into ``src/quant`` is filed as follow-up E2-M3-SLA-CORE-SRC.
    """
    cached = sys.modules.get("monitor_freshness")
    if cached is not None:
        return cached
    import importlib.util

    import quant

    path = Path(quant.__file__).resolve().parents[2] / "scripts" / "monitor_freshness.py"
    spec = importlib.util.spec_from_file_location("monitor_freshness", path)
    if spec is None or spec.loader is None or not path.exists():
        raise RuntimeError(f"freshness monitor script not found at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["monitor_freshness"] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop("monitor_freshness", None)
        raise
    return module


def _serialize_feed(status: Any) -> dict[str, Any]:
    """One ``/health`` feed entry from a ``monitor_freshness.FeedStatus``.

    Duck-typed (the FeedStatus class lives in the script module): reads
    ``name`` / ``state`` / ``latest`` / ``required_date`` / ``detail``. Dates
    serialize as ISO calendar dates — the granularity the SLA verdict is made
    at (``evaluate_feed`` compares observation *dates*).
    """
    state = status.state
    latest = status.latest
    required = status.required_date
    return {
        "name": status.name,
        "state": getattr(state, "value", state),
        "latest": latest.date().isoformat() if latest is not None else None,
        "required_date": required.isoformat() if required is not None else None,
        "detail": status.detail,
    }


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
    submit_report: Callable[[feedback.FeedbackReport], str] | None = None,
    promote_issue: Callable[[int], feedback.PromotedTask] | None = None,
    feed_statuses_fn: Callable[[dt.datetime], list[Any]] | None = None,
    write_export_fn: Callable[..., list[Path]] | None = None,
    auth_token: str | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Build the console API app.

    ``sources`` is injectable for tests. When ``None``, the production
    ``ConsoleSources.default(feature_monitor=...)`` is resolved lazily on the
    FIRST request — never at app-creation time, so importing/constructing the
    app requires no settings or credentials — and memoized for the app's
    lifetime, so the feature monitor's per-instance panel memo is reused across
    requests instead of rebuilding the panel per call. ``feature_monitor``
    mirrors the CLI's ``--no-monitor`` gate (ignored when ``sources`` is given).

    ``submit_report`` / ``promote_issue`` are the ``POST /feedback`` write
    seams, injectable so endpoint tests run with no network and no ``gh``.
    Their defaults resolve lazily (at request time) to the E1-M6 service-layer
    functions: :func:`quant.console.feedback.submit_issue_via_gh` and
    :func:`quant.console.feedback.promote` (the latter stamped with
    ``sources.now()``'s date, the app's injectable clock).

    E2-M3 seams: ``feed_statuses_fn(now)`` supplies the per-feed SLA verdicts
    for ``GET /health`` (default: lazily load ``scripts/monitor_freshness.py``
    and run its ``monitor`` over the real lake); ``write_export_fn`` performs
    the optional static re-export for ``POST /recompute`` (default:
    :func:`quant.console.export.write_export` into the default export dir);
    ``auth_token`` pins the bearer token explicitly (default: read the
    ``CONSOLE_API_TOKEN`` env var at request time).

    ``cors_origins`` (E2-M4) pins the CORS allow-list explicitly (default:
    :func:`resolve_cors_origins` — the ``CONSOLE_API_CORS_ORIGINS`` env var,
    else the vite dev origins). An empty list disables CORS.
    """
    app = FastAPI(title=API_TITLE, version=API_VERSION)
    allowed_origins = resolve_cors_origins(cors_origins)
    if allowed_origins:
        # Exact-origin allow-list, no cookies (auth is a bearer header). The
        # Authorization header must be allowed for the api-mode /feedback POST.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )
    state: dict[str, ConsoleSources | None] = {"sources": sources}
    # When sources are injected, /recompute has nothing to reset — the caller
    # owns their lifecycle. Only the lazily resolved default is droppable.
    sources_injected = sources is not None
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

    def _submit(report: feedback.FeedbackReport) -> str:
        submitter = submit_report or feedback.submit_issue_via_gh
        return submitter(report)

    def _promote(issue_number: int) -> feedback.PromotedTask:
        if promote_issue is not None:
            return promote_issue(issue_number)
        today = get_sources().now().date().isoformat()
        return feedback.promote(issue_number, today=today)

    # ── Auth (E2-M3): one simple local bearer token for every mutate route ──

    def _configured_token() -> str | None:
        """The active token: the factory argument, else the env var, else None."""
        if auth_token:
            return auth_token
        return os.environ.get(TOKEN_ENV_VAR) or None

    def _require_token(authorization: str | None, *, fail_closed: bool) -> None:
        """Enforce the bearer token on a mutating route.

        With a token configured, the route requires ``Authorization: Bearer
        <token>`` (constant-time compare) — 401 otherwise. With NO token
        configured: ``fail_closed=True`` (``/recompute``, pinned
        "authenticated" by PRD §4 M3) refuses with 403 rather than running
        unauthenticated (§9 — no silent auth downgrade); ``fail_closed=False``
        (``/feedback``) keeps the pre-token localhost-open behaviour.
        """
        token = _configured_token()
        if token is None:
            if fail_closed:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "recompute is disabled: no auth token configured "
                        f"(set {TOKEN_ENV_VAR} or pass auth_token to create_app)"
                    ),
                )
            return
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not supplied.strip():
            raise HTTPException(status_code=401, detail="missing bearer token")
        # Byte-wise compare: ``compare_digest`` raises TypeError on non-ASCII
        # str input, which would surface as a 500 instead of a clean 401.
        if not secrets.compare_digest(supplied.strip().encode(), token.encode()):
            raise HTTPException(status_code=401, detail="invalid token")

    # ── GET /health (E2-M3) ──────────────────────────────────────────────────

    @app.get(HEALTH_PATH, name="health")
    def health() -> dict[str, Any]:
        """Per-feed freshness vs the pinned C1 SLA table (PRD §4 M3).

        Delegates the verdict to the C1-M3 machinery (``evaluate_feed`` over
        ``SOURCE_SLAS``) so the API can never disagree with the cron monitor
        about what "fresh" means. The clock is ``sources.now()`` (injectable).
        An unavailable monitor is an honest 503 — never a fabricated "fresh"
        (METHODOLOGY §9).
        """
        now = get_sources().now()
        try:
            if feed_statuses_fn is not None:
                statuses = feed_statuses_fn(now)
            else:
                statuses = _load_monitor_module().monitor(now=now)
        except Exception as exc:
            _LOG.error("health check failed: freshness monitor unavailable: %s", exc)
            raise HTTPException(
                status_code=503, detail=f"freshness monitor unavailable: {exc}"
            )
        feeds = [_serialize_feed(s) for s in statuses]
        # Zero evaluated feeds is an alert, not a vacuous "fresh" (§9): the
        # pinned SLA table always yields 7 feeds, so an empty result means the
        # monitor is not actually monitoring anything.
        overall = "fresh" if feeds and all(f["state"] == "fresh" for f in feeds) else "alert"
        return {"status": overall, "checked_at": _iso_utc(now), "feeds": feeds}

    # ── POST /recompute (E2-M3) ──────────────────────────────────────────────

    @app.post(RECOMPUTE_PATH, name="recompute")
    def recompute(
        payload: dict[str, Any] | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Authenticated on-demand re-export/recompute (PRD §4 M3).

        Always drops the app's memoized *default* sources, so the next request
        re-resolves ``ConsoleSources.default()`` — a fresh feature-monitor memo
        over the current lake state (the disk cache decides whether the panel
        actually rebuilds). Injected sources are caller-owned and are not reset
        (``sources_reset: false``).

        Optional body ``{"write_static": true}`` additionally re-writes the
        static export tree server-side (the E1 ``console export`` step, no
        manual CLI run) into the default export dir — the output path is never
        client-supplied. A schema-validation failure during the export is a 500
        (the same fail-fast contract as ``write_export``).
        """
        _require_token(authorization, fail_closed=True)
        body_in = payload or {}
        unknown = sorted(set(body_in) - {"write_static"})
        if unknown:
            raise HTTPException(
                status_code=422, detail=f"unknown field(s): {', '.join(unknown)}"
            )
        write_static = body_in.get("write_static", False)
        if not isinstance(write_static, bool):
            raise HTTPException(status_code=422, detail="write_static must be a boolean")

        sources_reset = False
        if not sources_injected:
            with resolve_lock:
                state["sources"] = None
            sources_reset = True

        body: dict[str, Any] = {"sources_reset": sources_reset, "static_files_written": None}
        if write_static:
            writer = write_export_fn or write_export
            try:
                written = writer(sources=get_sources())
            except ValueError as exc:
                _LOG.error("re-export failed schema validation: %s", exc)
                raise HTTPException(status_code=500, detail=f"re-export failed: {exc}")
            body["static_files_written"] = len(written)
        return body

    @app.post(FEEDBACK_PATH, status_code=201, name="feedback")
    def post_feedback(
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """File the report as a ``feedback``-labeled GitHub issue (PRD §4 M2).

        The body is the E1-M6 capture payload — exactly the
        :class:`~quant.console.feedback.FeedbackReport` fields (one schema,
        shared with the frontend modal; METHODOLOGY §6) — plus an optional
        ``promote: bool`` that additionally runs the feedback → PRIORITIES
        promotion on the issue just created.

        Error contract: a malformed payload is 422 (nothing filed); a GitHub
        submission failure is 502 (nothing promoted). A promotion failure
        AFTER the issue landed is NOT a 5xx — the issue exists and hiding it
        behind an error would misreport the write — the 201 body carries
        ``promoted: false`` plus an explicit ``promotion_error`` instead
        (fail-loudly-not-silently, METHODOLOGY §9).

        Auth (E2-M3): when a token is configured, this route requires it — the
        SAME token as ``/recompute``, one auth story for every mutate route.
        With no token configured it stays open (localhost-only default bind).
        """
        _require_token(authorization, fail_closed=False)
        promote_flag = payload.get("promote", False)
        if not isinstance(promote_flag, bool):
            raise HTTPException(status_code=422, detail="promote must be a boolean")
        fields = {k: v for k, v in payload.items() if k != "promote"}
        # Boundary validation the dataclass cannot do at runtime: every
        # FeedbackReport field is a string (dataclass annotations are not
        # enforced), and a non-string would otherwise surface as a 500 deep
        # inside issue-body construction instead of a 422 here.
        non_str = sorted(k for k, v in fields.items() if not isinstance(v, str))
        if non_str:
            raise HTTPException(
                status_code=422,
                detail=f"invalid feedback payload: non-string field(s): {', '.join(non_str)}",
            )
        try:
            report = feedback.FeedbackReport(**fields)
        except (TypeError, ValueError) as exc:
            # TypeError: missing/unknown field; ValueError: pinned-enum or
            # empty-title violation.
            raise HTTPException(status_code=422, detail=f"invalid feedback payload: {exc}")
        try:
            issue_url = _submit(report)
        except RuntimeError as exc:
            _LOG.error("feedback issue creation failed: %s", exc)
            raise HTTPException(status_code=502, detail=f"GitHub issue creation failed: {exc}")
        issue_number = parse_issue_number(issue_url)
        body: dict[str, Any] = {
            "issue_url": issue_url,
            "issue_number": issue_number,
            "promoted": False,
        }
        if promote_flag:
            if issue_number is None:
                body["promotion_error"] = (
                    f"could not parse an issue number from {issue_url!r}; "
                    "promote manually via `console feedback promote <n>`"
                )
                _LOG.error("feedback promotion skipped: %s", body["promotion_error"])
            else:
                try:
                    task = _promote(issue_number)
                    body["promoted"] = True
                    body["task_id"] = task.id
                except (ValueError, RuntimeError) as exc:
                    # ValueError: duplicate task / label guard; RuntimeError:
                    # the promotion path's own gh read failed.
                    _LOG.error("feedback promotion failed for issue #%s: %s", issue_number, exc)
                    body["promotion_error"] = str(exc)
        return body

    @app.get("/")
    def index() -> dict[str, Any]:
        paths = sorted(
            route.path
            for route in app.routes
            if isinstance(route, APIRoute) and route.path.startswith(DATA_PREFIX)
        )
        return {"service": API_TITLE, "version": API_VERSION, "data_routes": paths}

    return app
