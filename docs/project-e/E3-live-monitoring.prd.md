# PRD — Project E3: Live Monitoring

> **Status:** Draft (scoped, not exhaustive). **Owner:** James Delgado. **Type:** Engineering.
> **Roadmap:** Project E, sub-project E3. **Supersedes roadmap §C5 ("Monitoring + reconciliation").**
> Binds [`METHODOLOGY.md`](../METHODOLOGY.md) rules 15–21.
> **Gated on:** E2 (Console API) + Project **C2/C3** (paper execution + sizing).

---

## 1 — Problem

E1 renders the operator's "which strategies are up/down and why" and "portfolio
performance" tiles in a **research/placeholder** state because no live execution exists
yet. Once the execution layer (Project C: C2 paper trading, C3 sizing) is online, the
operator needs those tiles **live** — real P&L, positions, exposure — plus a live
regime indicator and a **paper-vs-backtest reconciliation** so divergence is caught early.

## 2 — Goals / Non-goals

**Goals**
- Light up the E1 Overview's live tiles: **live portfolio P&L, positions, exposure**,
  per-strategy live performance, sourced from C2/C3 state via the E2 API.
- A **live regime/condition indicator** (reusing the condition machinery) updated daily/intraday.
- A **reconciliation panel**: live paper-trading P&L vs the Phase-1 backtest for the same
  period; deltas beyond a pre-committed threshold are flagged.
- A **live model-output monitor** — a per-strategy signal/prediction distribution
  histogram — to catch model degeneration early (outputs collapsing to a constant,
  drifting, or saturating against a cap). *(Folded in from the superseded roadmap
  §C5 dashboard; C5's model-output histogram lands here.)*

**Non-goals**
- The execution layer itself — Project C (C2/C3) builds paper trading + sizing; E3 only
  reads and displays its state via E2.
- Feed staleness / data alerting — that is **E4**.
- New view-model logic outside the shared `src/quant/console/` layer.

## 3 — Dependencies

- **E2 (Console API)** — E3 reads live state through the API. Hard prerequisite.
- **Project C2 (paper execution) + C3 (sizing)** — the source of live P&L/positions.
- Inherits the reconciliation intent from roadmap §C5, which this project **supersedes**.

## 4 — Milestones

| # | Milestone | Deliverable | Acceptance |
|---|---|---|---|
| **M1** | Live portfolio + positions | Overview live tiles (P&L, positions, exposure, per-strategy live) + a per-strategy model-output (signal) distribution histogram, from C2/C3 via E2 | research placeholders replaced by live values; reconciles to C2 state; the model-output histogram renders from live signals and flags a seeded degenerate (constant) output |
| **M2** | Live regime indicator | live vol/trend/rates condition indicator | matches the condition machinery on the same as-of date |
| **M3** | Reconciliation | paper-vs-backtest P&L panel; pre-committed delta threshold flags divergence | a > threshold delta is flagged with drill-in; threshold pinned in code before launch |
| **E3-CLOSE** | Closeout (rule 21) | end-to-end validation (live tiles + reconciliation against a real paper run) + one-page report | `depends_on` all E3 tasks; report states delivered + deferred scope |

## 5 — Tech & testing

Reuses the E1 design system + the E2 API. Live tiles are additive panels, not a
rearchitecture. Reconciliation threshold is a **pre-committed constant** (methodology
rule 1). Unit + integration tests ≥80%; visual + a11y floors as E1; `ruff` clean.

## 6 — Risks

| Risk | Mitigation |
|---|---|
| C2/C3 not ready → E3 blocked | hard-gated on C2/C3; E1/E2 deliver value meanwhile |
| Reconciliation false alarms | threshold pre-committed + materiality-before-significance (rule 10) |
| Intraday refresh complexity | start daily-cadence; intraday is a later, separate milestone |

## 7 — Definition of done

Live P&L/positions/exposure replace the E1 placeholders; live regime indicator matches
the condition machinery; reconciliation flags > threshold deltas; coverage ≥80%;
`E3-CLOSE` validation + report landed. Roadmap §C5 marked superseded.
