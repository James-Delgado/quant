# PRD — Project E2: Console API

> **Status:** Draft (scoped, not exhaustive). **Owner:** James Delgado. **Type:** Engineering.
> **Roadmap:** Project E, sub-project E2. Binds [`METHODOLOGY.md`](../METHODOLOGY.md) rules 15–21.
> **Gated on:** Project **C1 (live data)** + the E1 `src/quant/console/` service layer.

---

## 1 — Problem

E1 serves the console from **static JSON** exported on demand. That is correct while
the underlying artifacts only change when an experiment is re-run. Two things need a
server: (a) **fresh / on-demand data** (live data from C1, re-export without a manual
step), and (b) **write actions** (the "Report an issue" submission, future controls).
E2 is also the foundation the live-monitoring panels (E3) and live data/market status
(E4) read from.

## 2 — Goals / Non-goals

**Goals**
- A **FastAPI** service that wraps the **same `src/quant/console/` readers** E1 uses —
  no new business logic, no duplicated computation.
- Endpoints that mirror the E1 export view-models (same schemas), so the React app
  swaps its data source from static files to the API behind a flag with zero logic change.
- A real **`/feedback`** endpoint: creates the `feedback` GitHub issue server-side and
  can trigger the `feedback → PRIORITIES.yaml` promotion.
- A **freshness/health** endpoint and an on-demand **re-export/recompute** trigger.

**Non-goals**
- New view-model logic (lives in the shared service layer).
- The live-monitoring panels themselves (E3) and live SLA/alerting (E4).
- Multi-user auth / SSO (single-user; a simple local token is sufficient).

## 3 — Dependencies

- **Project C1 (live data / same-day point-in-time reader)** — without it, E2 serves
  only what static export already covers; the live value of an API depends on C1. **E2
  does not start until C1 lands.**
- **E1 service layer** — E2 imports the same readers; E1 must be complete.
- Downstream: **E3 and E4 depend on E2.**

## 4 — Milestones

| # | Milestone | Deliverable | Acceptance |
|---|---|---|---|
| **M1** | API scaffold + read endpoints | FastAPI app serving the E1 view-models from the shared readers | each endpoint returns the **same schema** as the matching `export/*.json`; shared schema tests pass against both |
| **M2** | Feedback endpoint | `POST /feedback` creates a labeled GitHub issue with context; optional promotion trigger | a posted report appears as a `feedback` issue with full context; promotion creates a PRIORITIES task |
| **M3** | Freshness + recompute | `/health` (per-feed freshness vs SLA) + an authenticated on-demand re-export/recompute | health reflects real lake/feed state; recompute refreshes view-models without a manual export |
| **M4** | Frontend data-source swap | React data client reads the API behind a `static ↔ api` flag | console runs identically against the API; no view/logic rewrite; static mode still works |
| **E2-CLOSE** | Closeout (rule 21) | end-to-end validation (console served live by the API) + one-page report | `depends_on` all E2 tasks; report states delivered + deferred scope |

## 5 — Tech & testing

FastAPI + uvicorn; reuses `src/quant/console/`; Pydantic schemas shared with the
export contract (one source of truth, drift-tested both directions). Unit + endpoint
tests ≥80% coverage; `ruff` clean; red CI blocks merge.

## 6 — Risks

| Risk | Mitigation |
|---|---|
| C1 slips → E2 blocked | E2 is explicitly gated on C1; static E1 keeps delivering value meanwhile |
| Schema drift export↔API | both serialize the **same** view-models; one schema, tested both ways |
| Feedback endpoint secrets (GitHub token) | server-side env/secret manager; never in the client |

## 7 — Definition of done

API serves every E1 view-model at schema parity; React runs against it behind a flag;
`/feedback` creates context-carrying issues + promotion; `/health` reflects real
freshness; coverage ≥80%; `E2-CLOSE` validation + report landed.
