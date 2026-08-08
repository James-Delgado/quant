# E2 Closeout — Console API

> **Status: COMPLETE.** Project E2 (the Console API) is delivered. The
> end-to-end closeout validation passes: a real uvicorn process serves every
> console view-model live at export parity, the auth/health/recompute
> operational surface works against the real lake, and the React console runs
> in `api` mode against the live service with zero console errors. Validated
> 2026-08-08 on `main` (post-E2-M4, build SHA `0abea25`). Companion:
> [`E2-console-api.prd.md`](E2-console-api.prd.md), [`DECISIONS.md`](DECISIONS.md),
> [`E1_CLOSEOUT.md`](E1_CLOSEOUT.md). Closes `E2-CLOSE`
> (METHODOLOGY §21 / AGENT_OPERATION "Project closeout").

## 1 — What E2 delivered

A **FastAPI** service (`src/quant/console/api/`) wrapping the same
`src/quant/console/` readers the E1 static export uses — no new business
logic — plus the frontend's static↔api data-source swap.

| Milestone | Delivered |
|---|---|
| **M1** Read endpoints | `create_app` factory mirroring the export tree one-for-one under `/data/` (same paths as `public/data/`); every endpoint calls the same reader, `sanitize` step, and generated schema as `write_export` — parity by construction, byte-level tested both directions |
| **M2** Feedback | `POST /feedback` files the `feedback`-labeled GitHub issue server-side (E1-M6 `FeedbackReport` is the single payload contract) with optional `promote` → PRIORITIES task; 422/502/201-with-`promotion_error` error contract |
| **M3** Health + recompute | `GET /health` judges per-feed freshness against the pinned C1 SLA table (`monitor_freshness` machinery — one SLA source of truth); authenticated `POST /recompute` drops memoized sources + optional server-side static re-export; one `CONSOLE_API_TOKEN` bearer story for all mutate routes (`/recompute` fail-closed 403) |
| **M4** Data-source swap | `VITE_DATA_SOURCE` static (default) / api (unknown values throw — no silent fallback), `VITE_API_BASE`, optional `VITE_CONSOLE_API_TOKEN`; CORS allow-list on the app (`CONSOLE_API_CORS_ORIGINS`, vite dev origins default, empty disables); api-mode modal swaps to one-click `POST /feedback`, degrading to the pre-filled `issues/new` tab on 401 |

## 2 — Validation method (the closeout "notebook")

Per the E1-CLOSE precedent, a service+UI project's closeout artifact is a
**scripted end-to-end gate**, not a Jupyter notebook. The reproducible gate is
[`scripts/e2_closeout_check.sh`](../../scripts/e2_closeout_check.sh):

```
scripts/e2_closeout_check.sh          # full lake-backed run
FAST=1 scripts/e2_closeout_check.sh   # --no-monitor both sides (skips ~90s panel)
```

Eight stages, exiting non-zero on the first failure: (1) fresh `console
export --check` from real artifacts; (2) boot the **real API** (`python -m
quant.console.api`, uvicorn on a scratch port, throwaway
`CONSOLE_API_TOKEN`); (3) **live parity sweep** — fetch every export artifact
from the running server over HTTP and compare payloads exactly (the manifest
structurally: `generated_at` is request-time by design), plus route-set
drift in the live direction; (4) `GET /health` returns real per-feed SLA
verdicts; (5) auth story live — `/recompute` 401 with no/wrong token, then
200 with the token **and** `write_static: true` re-writing the export tree
server-side; (6) frontend build + full test suite (static mode); (7)
**api-mode build** (`VITE_DATA_SOURCE=api`); (8) console API + console
unit/contract suites. This is exactly the seam the per-milestone tests never
crossed: they use FastAPI's in-process `TestClient`; the gate uses a real
server process, real HTTP, and the real lake. The live api-mode browser
render is the human-reviewable evidence (§3).

## 3 — Evidence (this run, 2026-08-08 ~00:28 UTC)

- **Fresh export:** 16 files written (`--check` fan-out gate: 4 strategies, 4 detail + 4 provenance files).
- **Live parity:** all **16 artifacts fetched from the running uvicorn at parity** (8 top-level, 8 fan-out); all 10 advertised `/data` routes backed by artifacts. No integration defect.
- **Health:** `status=alert`, all 7 feeds `stale` — the **honest** verdict over a lake that hasn't been re-ingested recently (the daily ingest cron is not running on this machine); the endpoint reflects real state rather than fabricating freshness (METHODOLOGY §9). Feed set matches the pinned C1 SLA table.
- **Auth + recompute:** 401 (no token) / 401 (wrong token) / 200 (valid); sources reset; **16 static files re-written server-side** via `write_static: true`.
- **Frontend:** static build green (~71 kB gz JS); **130 tests pass** (22 files); api-mode build green with the API base baked in.
- **Service layer:** **205 tests pass** (`tests/test_console_api.py` + `tests/test_console.py`).
- **Live api-mode render** (`VITE_DATA_SOURCE=api npm run dev` against `python -m quant.console.api`, the documented operator workflow): all **9 hash routes rendered honest content (1062–2022 chars each), 0 console errors on every route**; the network log shows 45 fetches to the API origin (`/data/*.json` incl. `_manifest.json` and per-strategy fan-out) — the console is served live by the API, not by copied static files.

## 4 — Definition of done (PRD §7)

- [x] API serves every E1 view-model at schema parity (byte-level in tests; live over HTTP in the gate).
- [x] React runs against it behind a flag; static mode still works (both builds + render evidence above).
- [x] `/feedback` creates context-carrying issues + promotion (E2-M2 tests + E2-M4 live curl smoke; not re-exercised against real GitHub here — see §6 deviations).
- [x] `/health` reflects real freshness (live `alert` verdict over the actually-stale lake).
- [x] Coverage ≥80% (api/app.py at 100% line coverage as of E2-M2; suite-wide console coverage certified in E1).
- [x] `E2-CLOSE` validation + this report landed.

## 5 — Honesty posture

The API cannot disagree with the artifacts or the cron monitor: every read
endpoint serializes through the same reader + schema as the export
(a payload that would fail `write_export` 500s instead of serving malformed),
and `/health` consumes the same pinned SLA table as `scripts/monitor_freshness.py`.
Failure modes are honest — 503 when the monitor is unavailable, `alert` on
zero evaluated feeds, 403 fail-closed recompute with no token, 201-with-
`promotion_error` rather than a 5xx that would hide a created issue.

## 6 — Deviations (declared, METHODOLOGY §9)

- **`POST /feedback` was not exercised against real GitHub in this run** — it
  would file a junk issue in the tracker per gate execution. Bounded impact:
  the endpoint's contract is covered by the API test suite (injected write
  seams) and was smoke-tested live (real `gh` submission) during E2-M2/E2-M4;
  the gate exercises the auth boundary of the mutate surface via `/recompute`.
- **The gate's browser render is not inside the script** — stages 1–8 are
  reproducible headless; the nine-route render (§3) follows the E1 precedent
  of scripted-gate + captured browser evidence.

## 7 — Deferred / out of scope

All pre-tracked in `PRIORITIES.yaml`; none changes whether the console is
served live by the API at parity:

- `E2-M4-PROMOTE-UI` (140) — expose the promote flag in the api-mode modal.
- `E2-SPA-STATIC-MOUNT` (141) — serve `frontend/dist` from the FastAPI app (single origin, zero CORS).
- `MISC-SLA-CORE-SRC` (139) — promote the SLA core out of `scripts/monitor_freshness.py` into `src/quant` (fixes the API→script layering inversion).
- `E1-M6-REST-SUBMIT` (120) — gh-free REST submission for the server path.
- Live-monitoring panels themselves (E3) and live SLA/alerting surfaces (E4) — the sub-projects this closeout unblocks.

## 8 — What's next

`E2-CLOSE` unblocks **E4-M1** (deps `E2-CLOSE` + `C1-M3` now both done →
`ready`). **E3-M1** stays blocked on the C3 sizing milestones (`C3-M1..M3`).
