#!/usr/bin/env bash
#
# E4-CLOSE — Project E4 closeout: reproducible end-to-end gate.
#
# Certifies that Data & Market Status *composes*: a fresh export from the real
# lake carries live per-ingestor SLA verdicts + lake gap reports (E4-M1) and
# the live market environment (E4-M3); the alerts CLI reports the honest
# current state; and a SEEDED SLA breach fires a real alert through the pinned
# channel — `python -m quant.console alerts` exiting non-zero with the breach
# on stderr, the exact cron-mail contract documented in
# docs/concepts/freshness-monitor.md. This is the seam the per-milestone tests
# never cross: they inject sources / fixtures; this gate runs the real CLI
# over the real lake. UI/service-project analogue of a closeout notebook
# (AGENT_OPERATION "Project closeout"), mirroring scripts/e1_closeout_check.sh
# and scripts/e2_closeout_check.sh; evidence is captured in
# docs/project-e/E4_CLOSEOUT.md.
#
# Exits non-zero on the first failed stage so CI / a human can gate on it.
#
# Usage:
#   scripts/e4_closeout_check.sh          # full run (lake-backed feature monitor)
#   FAST=1 scripts/e4_closeout_check.sh   # --no-monitor everywhere (skips the
#                                         # ~1-2 min drift-panel build; drift
#                                         # degrades to an explicit note)
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

# Seeded-breach horizon (pinned; METHODOLOGY §1): evaluating the alerts CLI
# as-of now+30 days guarantees every C1 SLA is breached (the loosest SLA is
# measured in days), independent of how fresh the lake happens to be — a
# deterministic seeded breach that still crosses the real CLI → readers →
# SLA-monitor → LogChannel → exit-code seam.
SEED_DAYS=30

step() { printf '\n=== %s ===\n' "$1"; }

MODE_ARGS=()
[ "${FAST:-0}" = "1" ] && MODE_ARGS+=(--no-monitor)

step "1/7 Fresh export from real artifacts (fan-out gated via --check)"
# `${arr[@]+...}` guards bash 3.2's set -u treating an empty array as unbound.
"$PYTHON" -m quant.console export --check ${MODE_ARGS[@]+"${MODE_ARGS[@]}"}

step "2/7 E4-M1 live status: SLA verdicts + lake gap reports in data_status.json"
"$PYTHON" - <<'PY'
import json, pathlib, sys

E = pathlib.Path("src/quant/console/export")
ds = json.loads((E / "data_status.json").read_text())
errors: list[str] = []

# Per-ingestor SLA verdicts (the C1-M3 machinery, consumed verbatim). An empty
# verdict set is only acceptable when honestly explained (METHODOLOGY §9).
sla = ds.get("sla", [])
if not sla:
    if not any("SLA" in n for n in ds.get("notes", [])):
        errors.append("sla empty with no explanatory degrade note")
else:
    allowed = {"fresh", "stale", "missing"}
    bad = [s for s in sla if s.get("state") not in allowed]
    if bad:
        errors.append(f"unknown SLA states: {bad}")

# Lake gap reports: at least one dataset actually checked (n_gaps not None —
# a verified count over a real observed window), and every checked report
# carries its window.
gaps = ds.get("gaps", [])
checked = [g for g in gaps if g.get("n_gaps") is not None]
if not checked:
    errors.append(f"no gap-checked dataset (gaps={gaps})")
for g in checked:
    if not (g.get("window_start") and g.get("window_end")):
        errors.append(f"checked gap report missing its window: {g}")

if errors:
    print("E4-M1 VALIDATION FAILED:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print(
    f"OK: {len(sla)} SLA verdicts "
    f"({ {s['feed']: s['state'] for s in sla} }); "
    f"{len(checked)}/{len(gaps)} datasets gap-checked, "
    f"gap counts={ {g['feed']: g['n_gaps'] for g in checked} }"
)
PY

step "3/7 E4-M3 live market environment + alerts.json shape"
"$PYTHON" - <<'PY'
import json, pathlib, sys

E = pathlib.Path("src/quant/console/export")
mkt = json.loads((E / "market.json").read_text())
errors: list[str] = []

# Every regime label is either a valid enum value or None WITH an honest
# degrade note (METHODOLOGY §9) — the same contract market_snapshot pins.
enums = {
    "vol_regime": {"low_vol", "mid_vol", "high_vol"},
    "trend_regime": {"uptrend", "downtrend"},
    "rates_regime": {"rates_falling", "rates_steady", "rates_rising"},
}
notes = mkt.get("notes", [])
for field, allowed in enums.items():
    value = mkt.get(field)
    if value is None:
        if not any(field.split("_")[0] in n.lower() for n in notes):
            errors.append(f"{field} is None with no degrade note (notes={notes})")
    elif value not in allowed:
        errors.append(f"{field}={value!r} not in {sorted(allowed)}")

# The E4-M3 curve + breadth fields must exist in the schema (value may be an
# honest None-with-note on a lake missing DGS2/universe data).
for field in ("two_year", "spread_2s10s", "breadth_above_ma200", "breadth_n_symbols"):
    if field not in mkt:
        errors.append(f"market.json missing E4-M3 field {field}")

# alerts.json: the exported E4-M2 surface is well-formed.
al = json.loads((E / "alerts.json").read_text())
if "asof" not in al or "alerts" not in al or "notes" not in al:
    errors.append(f"alerts.json missing keys (got {sorted(al)})")
kinds = {"staleness", "gap", "drift", "regime_change"}
severities = {"warning", "critical"}
for a in al.get("alerts", []):
    if a.get("kind") not in kinds or a.get("severity") not in severities:
        errors.append(f"malformed alert item: {a}")

if errors:
    print("E4-M3 VALIDATION FAILED:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
regimes = {f: mkt.get(f) for f in enums}
print(f"OK: regimes={regimes}, spread_2s10s={mkt.get('spread_2s10s')}, "
      f"breadth={mkt.get('breadth_above_ma200')} "
      f"(n={mkt.get('breadth_n_symbols')})")
print(f"OK: alerts.json well-formed — {len(al['alerts'])} alert(s), "
      f"{len(al['notes'])} note(s) at asof={al['asof']}")
PY

step "4/7 Honest current-state alert run (either verdict accepted; §9 evidence)"
set +e
"$PYTHON" -m quant.console alerts ${MODE_ARGS[@]+"${MODE_ARGS[@]}"}
CURRENT_EXIT=$?
set -e
if [ "$CURRENT_EXIT" -ne 0 ] && [ "$CURRENT_EXIT" -ne 1 ]; then
  echo "FAIL: alerts CLI crashed (exit $CURRENT_EXIT); expected 0 (clear) or 1 (alerting)"
  exit 1
fi
echo "OK: current-state exit=$CURRENT_EXIT ($([ "$CURRENT_EXIT" -eq 0 ] && echo 'no alerts' || echo 'alerts firing — honest state over this lake'))"

step "5/7 SEEDED BREACH fires through the real channel (now+${SEED_DAYS}d ⇒ exit 1 + stderr)"
SEED_NOW="$("$PYTHON" -c "
import datetime as dt
print((dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=${SEED_DAYS})).strftime('%Y-%m-%dT%H:%M:%S+00:00'))
")"
STDERR_FILE="$(mktemp -t e4_closeout_alerts.XXXXXX)"
set +e
"$PYTHON" -m quant.console alerts --now "$SEED_NOW" ${MODE_ARGS[@]+"${MODE_ARGS[@]}"} \
  2>"$STDERR_FILE"
SEED_EXIT=$?
set -e
if [ "$SEED_EXIT" -ne 1 ]; then
  echo "FAIL: seeded breach (--now $SEED_NOW) exited $SEED_EXIT; expected 1"
  cat "$STDERR_FILE"
  exit 1
fi
grep -q "SLA breach" "$STDERR_FILE" || {
  echo "FAIL: no staleness alert on stderr for the seeded breach:"
  cat "$STDERR_FILE"
  exit 1
}
grep -q "ALERT(S)" "$STDERR_FILE" || {
  echo "FAIL: stderr missing the alert summary line:"
  cat "$STDERR_FILE"
  exit 1
}
echo "OK: seeded breach at --now $SEED_NOW → exit 1, breach delivered on stderr:"
sed 's/^/  | /' "$STDERR_FILE"
rm -f "$STDERR_FILE"

step "6/7 Cron wiring documented (E4-ALERTS-CRON-DOC drift check)"
DOC="docs/concepts/freshness-monitor.md"
grep -q "python -m quant.console alerts" "$DOC" || {
  echo "FAIL: $DOC does not document the console alerts invocation"
  exit 1
}
grep -E "^[0-9*]+ [0-9*]+ \* \* .*quant\.console alerts" "$DOC" >/dev/null || {
  echo "FAIL: $DOC has no crontab line for the console alerts channel"
  exit 1
}
echo "OK: $DOC documents the console-alerts invocation + crontab entry"

step "7/7 Frontend build + tests; console service-layer suites"
( cd frontend && npm run build && npm run test )
"$PYTHON" -m pytest tests/test_console.py tests/test_console_api.py -q

printf '\nE4-CLOSE check: PASS — live SLA/gap/market status from the real lake; a seeded SLA breach fired through the real cron channel (exit 1 + stderr); cron wiring documented.\n'
