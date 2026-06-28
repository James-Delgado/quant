# PRD — Project E4: Data & Market Status

> **Status:** Draft (scoped, not exhaustive). **Owner:** James Delgado. **Type:** Engineering.
> **Roadmap:** Project E, sub-project E4. Binds [`METHODOLOGY.md`](../METHODOLOGY.md) rules 15–21.
> **Gated on:** E2 (Console API) + Project **C1 (live data / freshness SLA)**.

---

## 1 — Problem

E1's "Data & Market" panel is a **static snapshot**. In production the operator needs
the live version: per-feed **freshness vs SLA**, **gap detection**, **staleness
alerting**, a live **market-environment** view (the conditions the strategies trade in),
and live **feature-drift** monitoring (the dynamic counterpart to E1's catalog stats).
This overlaps Project C1's freshness-monitor intent and consumes its live data.

## 2 — Goals / Non-goals

**Goals**
- **Live feed health** — per-ingestor freshness measured against a pinned SLA, with
  gap detection over the lake.
- **Alerting** — staleness / gap / drift / **regime-change** breaches raise an alert
  (channel TBD: log, email, or push), so a data or market-environment shift is seen
  before it corrupts a prediction. *(Regime-change alerting is folded in from the
  superseded roadmap §C5; E4 owns the alert because it owns the alerting channel and
  the live market-environment computation below — E3 owns the regime-indicator
  display.)*
- **Live market environment** — vol/trend/rates regimes, breadth, curve, computed live.
- **Live feature-drift monitor** — extends the E1 catalog's coverage/μ/σ/drift stats to
  a live, alerting surface.

**Non-goals**
- Building or fixing ingestors — Phase 0 / Project C1 own data acquisition; E4 monitors them.
- The research feature catalog itself (E1) and live strategy P&L (E3).

## 3 — Dependencies

- **E2 (Console API)** — E4 reads live status through the API. Hard prerequisite.
- **Project C1 (live data + freshness SLA)** — supplies same-day data and the SLA
  definitions E4 monitors against.

## 4 — Milestones

| # | Milestone | Deliverable | Acceptance |
|---|---|---|---|
| **M1** | Live feed health + gaps | per-feed freshness vs SLA + lake gap detection | health reflects real freshness; a seeded gap is detected and surfaced |
| **M2** | Alerting | staleness / gap / drift / regime-change breach → alert (pinned thresholds) | a breach raises an alert via the chosen channel; thresholds pre-committed in code; a seeded regime transition raises a regime-change alert |
| **M3** | Live market + feature drift | live vol/trend/rates + breadth + curve; live feature-drift monitor | market env matches the condition machinery; drift monitor flags a seeded distribution shift |
| **E4-CLOSE** | Closeout (rule 21) | end-to-end validation (live status + a fired alert on seeded breach) + one-page report | `depends_on` all E4 tasks; report states delivered + deferred scope |

## 5 — Tech & testing

Reuses the E1 design system + the E2 API; alerting thresholds are **pre-committed
constants** (rule 1). Unit + integration tests ≥80%; `ruff` clean; red CI blocks merge.

## 6 — Risks

| Risk | Mitigation |
|---|---|
| C1 not ready → E4 blocked | hard-gated on C1; lake-only checks could ship earlier if pulled forward, but live SLA needs C1 |
| Alert fatigue | materiality-before-significance (rule 10); pinned, tuned thresholds; severity levels |
| Overlap with C1 freshness monitor | E4 consumes C1's SLA definitions rather than redefining them |

## 7 — Definition of done

Live feed health reflects real freshness vs SLA; gaps detected; breaches alert via the
chosen channel; live market environment + feature-drift monitor render; coverage ≥80%;
`E4-CLOSE` validation + report landed.
