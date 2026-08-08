#!/usr/bin/env bash
#
# E2-CLOSE — Project E2 closeout: reproducible end-to-end gate.
#
# Certifies that the Console API *composes*: a real uvicorn process serves the
# console view-models live over HTTP at export-schema parity, the mutate routes
# enforce the one-token auth story, /health reflects the real lake, /recompute
# refreshes the static tree server-side, and the React app builds in BOTH data
# modes (static + api). This is the seam the per-milestone tests (in-process
# TestClient, static fixtures) never cross. UI/service-project analogue of a
# closeout notebook (AGENT_OPERATION "Project closeout"), mirroring
# scripts/e1_closeout_check.sh; the live api-mode browser render is captured as
# evidence in docs/project-e/E2_CLOSEOUT.md.
#
# Exits non-zero on the first failed stage so CI / a human can gate on it.
#
# Usage:
#   scripts/e2_closeout_check.sh            # full export + lake-backed feature monitor
#   FAST=1 scripts/e2_closeout_check.sh     # --no-monitor both sides (skips ~90s panel)
#   PORT=8765 scripts/e2_closeout_check.sh  # override the scratch port
#
# Requirements: the project venv python and Node/npm on PATH. The venv is
# auto-resolved from the main repo root (works from a git worktree); override
# with PYTHON=/path/to/python.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

GIT_COMMON="$(git rev-parse --git-common-dir)"
MAIN_REPO="$(cd "$(dirname "$GIT_COMMON")" && pwd)"
PYTHON="${PYTHON:-$MAIN_REPO/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="python"

export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"

PORT="${PORT:-8971}"
API_BASE="http://127.0.0.1:${PORT}"
# A throwaway token for THIS run: proves the live auth story (401 without /
# with-wrong, 200 with) without touching any operator secret.
CLOSEOUT_TOKEN="$("$PYTHON" -c 'import secrets; print(secrets.token_hex(16))')"
SERVER_LOG="$(mktemp -t e2_closeout_api.XXXXXX)"
SERVER_PID=""

cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
on_fail() {
  printf '\n--- api server log (%s) ---\n' "$SERVER_LOG"
  tail -50 "$SERVER_LOG" || true
}
trap cleanup EXIT
trap on_fail ERR

step() { printf '\n=== %s ===\n' "$1"; }

MODE_ARGS=()
[ "${FAST:-0}" = "1" ] && MODE_ARGS+=(--no-monitor)

step "1/8 Fresh export from real artifacts (fan-out gated via --check)"
# `${arr[@]+...}` guards bash 3.2's set -u treating an empty array as unbound.
"$PYTHON" -m quant.console export --check ${MODE_ARGS[@]+"${MODE_ARGS[@]}"}

step "2/8 Boot the real API (uvicorn, port ${PORT})"
CONSOLE_API_TOKEN="$CLOSEOUT_TOKEN" \
  "$PYTHON" -m quant.console.api --port "$PORT" ${MODE_ARGS[@]+"${MODE_ARGS[@]}"} \
  >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 60); do
  if curl -sf -o /dev/null "$API_BASE/"; then break; fi
  kill -0 "$SERVER_PID" 2>/dev/null || { echo "API process died on startup"; exit 1; }
  sleep 0.5
done
curl -sf -o /dev/null "$API_BASE/" || { echo "API never became ready"; exit 1; }
# Guard against a port collision: the responder must be OUR service, not
# whatever else happens to be listening on the scratch port.
curl -sf "$API_BASE/" | grep -q '"service":"quant console API"' \
  || { echo "port ${PORT} is serving something else — set PORT to a free port"; exit 1; }
echo "OK: API up (pid $SERVER_PID)"

step "3/8 Live parity sweep: every export artifact == its live API payload"
API_BASE="$API_BASE" "$PYTHON" - <<'PY'
import json, os, pathlib, sys, urllib.request

api = os.environ["API_BASE"]
export_dir = pathlib.Path("src/quant/console/export")
errors: list[str] = []

def fetch(path: str):
    # Generous timeout: the FIRST request lazily resolves the API's default
    # sources (feature-monitor build unless --no-monitor / disk-cached).
    with urllib.request.urlopen(f"{api}/data/{path}", timeout=600) as resp:
        return json.load(resp)

files = sorted(
    p.relative_to(export_dir).as_posix() for p in export_dir.rglob("*.json")
)
if not files:
    sys.exit("no export artifacts found — did step 1 run?")

for rel in files:
    static = json.loads((export_dir / rel).read_text())
    try:
        live = fetch(rel)
    except Exception as exc:  # noqa: BLE001 — any fetch failure fails the gate
        errors.append(f"{rel}: fetch failed: {exc}")
        continue
    if rel == "_manifest.json":
        # generated_at is request-time by design (the live counterpart of the
        # export stamp); parity is structural — same schema keys, same source
        # set with identical labels.
        if set(live) != set(static):
            errors.append(f"{rel}: key sets differ: {sorted(live)} vs {sorted(static)}")
        elif [s.get("label") for s in live["sources"]] != [
            s.get("label") for s in static["sources"]
        ]:
            errors.append(f"{rel}: source labels differ")
    elif live != static:
        errors.append(f"{rel}: live payload != static export")

# Route-set drift, live direction (METHODOLOGY §6): every /data route the
# server advertises must correspond to an export artifact or fan-out dir.
with urllib.request.urlopen(f"{api}/", timeout=60) as resp:
    routes = json.load(resp)["data_routes"]
for route in routes:
    rel = route.removeprefix("/data/")
    if "{" in rel:  # parameterized fan-out route -> its export dir must exist
        sub = rel.split("/", 1)[0]
        if not list((export_dir / sub).glob("*.json")):
            errors.append(f"route {route}: no matching fan-out files under {sub}/")
    elif not (export_dir / rel).exists():
        errors.append(f"route {route}: no matching export artifact")

if errors:
    print("LIVE PARITY FAILED:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
fan_out = len([f for f in files if "/" in f])
print(
    f"OK: {len(files)} artifacts fetched live at parity "
    f"({fan_out} fan-out, {len(files) - fan_out} top-level); "
    f"{len(routes)} advertised routes all backed"
)
PY

step "4/8 GET /health reflects the real lake (C1 SLA verdicts)"
API_BASE="$API_BASE" "$PYTHON" - <<'PY'
import json, os, urllib.request

with urllib.request.urlopen(f"{os.environ['API_BASE']}/health", timeout=120) as resp:
    body = json.load(resp)
assert body.get("status") in {"fresh", "alert"}, body
feeds = body.get("feeds", [])
assert feeds, "zero feeds evaluated — monitor not monitoring"
allowed = {"fresh", "waiting", "stale", "error"}
bad = [f for f in feeds if f.get("state") not in allowed]
assert not bad, f"unknown feed states: {bad}"
print(
    f"OK: status={body['status']} checked_at={body['checked_at']} "
    f"feeds={ {f['name']: f['state'] for f in feeds} }"
)
PY

step "5/8 Auth story + authenticated /recompute (re-export without manual CLI)"
API_BASE="$API_BASE" CLOSEOUT_TOKEN="$CLOSEOUT_TOKEN" "$PYTHON" - <<'PY'
import json, os, urllib.error, urllib.request

api, token = os.environ["API_BASE"], os.environ["CLOSEOUT_TOKEN"]

def post(path, headers=None, body=None):
    req = urllib.request.Request(
        f"{api}{path}",
        method="POST",
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")

status, _ = post("/recompute")
assert status == 401, f"no token: expected 401, got {status}"
status, _ = post("/recompute", headers={"Authorization": "Bearer wrong"})
assert status == 401, f"wrong token: expected 401, got {status}"
status, body = post(
    "/recompute",
    headers={"Authorization": f"Bearer {token}"},
    body={"write_static": True},
)
assert status == 200, f"valid token: expected 200, got {status}: {body}"
assert body["sources_reset"] is True, body
assert (body["static_files_written"] or 0) > 0, body
print(
    f"OK: 401/401/200; sources reset, "
    f"{body['static_files_written']} static files re-written server-side"
)
PY

step "6/8 Frontend build + full test suite (static mode default)"
( cd frontend && npm run build && npm run test )

step "7/8 Frontend api-mode build (the E2-M4 flag path compiles + bakes the API base)"
( cd frontend && VITE_DATA_SOURCE=api VITE_API_BASE="$API_BASE" npm run build )

step "8/8 Service layer: console API + console unit/contract tests"
"$PYTHON" -m pytest tests/test_console_api.py tests/test_console.py -q

printf '\nE2-CLOSE check: PASS — console view-models served live by the API at parity; auth, health, recompute, and both frontend data modes verified.\n'
