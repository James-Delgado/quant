#!/usr/bin/env bash
#
# E1-CLOSE — Project E1 closeout: reproducible end-to-end build+render gate.
#
# Certifies that the Research & Trust Console *composes*: a fresh export from the
# real artifacts feeds all nine panels, the SPA builds, and the rendered panels
# (component render tests + the contract drift test reading the REAL export) pass.
# This is the UI-project analogue of a closeout notebook (AGENT_OPERATION
# "Project closeout"); the live browser render of all nine routes is captured as
# evidence in docs/project-e/E1_CLOSEOUT.md.
#
# Exits non-zero on the first failed stage so CI / a human can gate on it.
#
# Usage:
#   scripts/e1_closeout_check.sh            # full export (lake-backed feature monitor)
#   FAST=1 scripts/e1_closeout_check.sh     # schema-only export (skips the ~90s monitor)
#
# Requirements: the project venv python and Node/npm on PATH. The venv is
# auto-resolved from the main repo root (works from a git worktree); override
# with PYTHON=/path/to/python.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Resolve the venv python from the main repo root (worktrees share the main .git).
GIT_COMMON="$(git rev-parse --git-common-dir)"
MAIN_REPO="$(cd "$(dirname "$GIT_COMMON")" && pwd)"
PYTHON="${PYTHON:-$MAIN_REPO/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="python"

export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"

step() { printf '\n=== %s ===\n' "$1"; }

step "1/5 Fresh export from real artifacts"
EXPORT_ARGS=()
[ "${FAST:-0}" = "1" ] && EXPORT_ARGS+=(--no-monitor)
# `${arr[@]+...}` guards against bash 3.2 treating an empty array as unbound (set -u).
"$PYTHON" -m quant.console export ${EXPORT_ARGS[@]+"${EXPORT_ARGS[@]}"}

step "2/5 Validate export artifacts (all panels + trend axis + manifest)"
"$PYTHON" - <<'PY'
import json, pathlib, sys
E = pathlib.Path("src/quant/console/export")
errors = []

# Every panel's backing artifact must exist (Overview/Strategies/Portfolio/
# Conditions/Data&Market read the top-level files; Provenance + Strategy detail
# fan out per arm; Explanations is static-in-app).
required = [
    "catalog.json", "conditions.json", "data_status.json", "ledger.json",
    "market.json", "portfolio.json", "strategies.json", "_manifest.json",
]
for name in required:
    if not (E / name).exists():
        errors.append(f"missing artifact: {name}")

# Fan-out: at least one provenance + one strategy-detail file.
for sub in ("provenance", "strategy"):
    if not list((E / sub).glob("*.json")):
        errors.append(f"empty fan-out dir: {sub}/")

# E1-CONDITIONS-TREND-COPY closeout check: the fresh, lake-backed export must
# carry the `trend` axis (uptrend/downtrend vs the 200-bar MA) so the Conditions
# lead copy (vol/trend/rates) matches the rendered bars. A stale checkpoint lacks
# it; its absence after a fresh export is an integration defect (keep E1-CLOSE open).
cond = json.loads((E / "conditions.json").read_text())
axes = [a["name"] for a in cond.get("axes", [])]
if "trend" not in axes:
    errors.append(f"conditions.json missing the `trend` axis (got {axes})")
else:
    trend = next(a for a in cond["axes"] if a["name"] == "trend")
    if set(trend.get("conditions", [])) < {"uptrend", "downtrend"}:
        errors.append(f"trend axis lacks uptrend/downtrend: {trend.get('conditions')}")

# Manifest carries the freshness datum the Topbar renders.
mani = json.loads((E / "_manifest.json").read_text())
if "generated_at" not in mani or "sources" not in mani:
    errors.append("manifest missing generated_at/sources")

if errors:
    print("ARTIFACT VALIDATION FAILED:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print(f"OK: all panel artifacts present; conditions axes = {axes}")
print(f"OK: manifest generated_at = {mani['generated_at']}, "
      f"{len(mani['sources'])} sources")
PY

step "3/5 Build the SPA (syncs export -> public/data, tsc + vite)"
( cd frontend && npm run build )

step "4/5 Render: frontend test suite (per-panel render + real-export contract)"
( cd frontend && npm run test )

step "5/5 Service layer: console unit + contract tests"
"$PYTHON" -m pytest tests/test_console.py -q

printf '\nE1-CLOSE check: PASS — console builds and renders all nine panels from a fresh real export.\n'
