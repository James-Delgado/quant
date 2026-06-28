# PRD — Project E1: Research & Trust Console

> **Status:** Draft for execution. **Owner:** James Delgado. **Type:** Engineering (UI + service layer), not a research experiment — acceptance is by criteria + definition-of-done, not a Sharpe gate.
>
> **Hand-off package:** this PRD + the consensus mockup at
> [`docs/project-e/mockups/research-trust-console.html`](mockups/research-trust-console.html)
> (+ `console.css`). The mockup is the **visual + interaction contract**; this
> PRD is the build contract. A clear-context agent turns the milestones below
> into `PRIORITIES.yaml` tasks.
>
> **Roadmap:** Project E (Human Interface & Observability), sub-project E1. See
> [`PROJECT_ROADMAP.md`](../PROJECT_ROADMAP.md). E1 **supersedes the roadmap C5
> "Monitoring + reconciliation" stub.** Binds to [`METHODOLOGY.md`](../METHODOLOGY.md)
> engineering rules 15–21.

---

## 1 — Problem

Every result the platform produces lives in machine formats — parquet checkpoints
(`data/phase4a/*`, `data/b1/*`), `data/ledger.yaml`, `features/catalog.yaml`, the
DuckDB/parquet lake, and markdown reports. None of it is human-visible at a glance.
There is no instrument the operator can open to answer the daily questions, and no
credible surface to show a knowledgeable quant that the data, data preparation, and
models can be trusted.

## 2 — Goals / Non-goals

**Goals**
- A **daily-driver analytical console** that answers, in order: overall portfolio
  performance; which strategies are up/down and **why**; data + market status;
  and result provenance on demand.
- Credibility conveyed through **substance** (real metrics, visualizations,
  provenance, monitoring) — never through self-description.
- A clean **two-layer architecture** (tested Python service layer + static React
  frontend) that the later API (E2) and live-monitoring panels (E3/E4) extend
  without rework.
- An in-UI **"Report an issue" button** whose reports feed an
  engineer/agent-visible **tracking system** that can be promoted into backlog
  tasks.

**Non-goals (explicit, deferred)**
- Live execution data, live P&L, intraday quotes → **E3/E4** (gated on Project C).
- The FastAPI service → **E2**.
- A **user-facing** issues/feedback panel — the tracker is code/agent-visible only.
- Multi-user, authentication, hosting/SSO.

## 3 — Users & jobs-to-be-done

Single user (the operator/researcher). On opening the console each morning:
1. **Portfolio performance** — how is the deployable candidate / aggregate doing vs benchmark.
2. **Strategies up/down and why** — per-strategy performance with a plain-language driver.
3. **Data & market status** — are feeds fresh and complete; what regime are we in.
4. **Provenance on demand** — drill into any result's inputs, controls, and commit.

(Live-strategy P&L and intraday market data join JTBD #1–#3 when E3/E4 land; E1
renders those tiles in a clearly-marked research/empty state.)

## 4 — Architecture

**Two layers, decoupled so the UI is cheap to replace and the logic never is.**

### 4.1 Service layer — `src/quant/console/` (Python, the durable core)
Pure functions that read existing artifacts and return **typed view-models**
(frozen dataclasses), plus an **export step** that serializes them to JSON.
- Readers: `load_strategies()`, `load_strategy(id)`, `load_conditions()`,
  `load_provenance(run)`, `load_catalog()`, `load_ledger()`, `data_status()`,
  `market_snapshot()`.
- Sources: per-arm checkpoints (`metadata.json` + parquet), `data/ledger.yaml`,
  `features/catalog.yaml`, the lake via `storage/catalog.py` + `storage/lake.py`.
- Depends only on existing `storage/` + `features/` modules + pandas/duckdb.
- **No business logic lives anywhere else.** Frontend renders; it does not compute.

### 4.2 Export → static JSON (the E1 data delivery)
An idempotent `console export` script writes view-models to
`src/quant/console/export/*.json`. The React app fetches these static files. **No
running server in E1.** When E2 adds FastAPI, the same readers back live endpoints;
the React data source swaps from static files to API with no logic rewrite.

### 4.3 Frontend (React, the disposable layer)
Vite + React + TypeScript SPA. Pages contain layout + calls into a thin data
client only. Design system per the mockup (`console.css` tokens, IBM Plex family,
color-only-in-data). Charting via a library (default **Plotly**; ECharts acceptable
— decide in M3) fed **neutral chart-ready series** from the service layer.

### 4.4 Data flow
```
artifacts (parquet · ledger.yaml · catalog.yaml · lake)
   → src/quant/console/ readers → typed view-models
   → console export → export/*.json
   → React (Vite/TS) static fetch → panels
```

## 5 — Panels (scope = the 8 locked in the mockup)

Each panel reads the named source and must reach mockup parity.

| Panel | Reads | Must show |
|---|---|---|
| **Overview** | strategies + ledger + data_status + market | portfolio hero chart vs benchmark (stress-shaded); strategies table with sparkline + plain-language driver; conditions-now + data-status; honest research-mode banner |
| **Strategies** | per-strategy view-models | roster (description + headline metric/status) → detail: figures, cumulative/drawdown/rolling-Sharpe/return-dist charts, condition link, "why" |
| **Conditions** | regime attribution + stress windows | Sharpe-by-condition (vol/trend/rates, live-computable primary axis); strategy×condition heatmap; named-episode stress table |
| **Provenance** | checkpoint `metadata.json` + git | run config; **GitHub commit link** (`github.com/James-Delgado/quant`); leakage controls as quiet enforced-status; self-tests; data lineage one-per-line |
| **Feature Catalog** | `catalog.yaml` + lake monitoring | health summary (registered/stable/drifting/stale/coverage); per-feature coverage, distribution mini, μ/σ, **stability (drift/staleness) status**, OOS status |
| **Trial Registry** | `data/ledger.yaml` | trials count, deflation "luck bar", best-vs-bar; per-run table (run, **project**, comparisons, verdict, commit link) |
| **Data & Market** | lake + FRED | per-feed freshness/gaps; market snapshot (VIX, 10Y, breadth, 2s10s) |
| **Explanations** | static content | serif reading cards + inline `ⓘ` tooltips (hover + click-pin) reused across panels |

**Conventions (from review):** no "trust" language; no internal file paths in UI;
named regimes are **conditions** (live-computable) with episodes as a separate
stress view; metrics presented large; charts in one consistent instrument style.

## 6 — Issue reporting & tracking system

**UI surface = the "Report an issue" button + modal ONLY.** No tracker panel.

- **Capture:** modal collects `{title, type(bug|idea|data), severity(low|med|high),
  description}` and **auto-captures context** `{panel, build_sha, timestamp,
  app_version}`.
- **Submit (E1, no backend):** opens/creates a **GitHub issue** in
  `James-Delgado/quant` labeled `feedback`, body pre-filled with the payload +
  context. (Implementation: pre-filled `issues/new` URL, or `gh`/REST if a token
  is available locally.)
- **Tracking (engineer/agent-visible, not in UI):** the set of `feedback`-labeled
  GitHub issues **is** the tracker. A documented + scripted **promotion path**
  (`console feedback promote <issue>`) appends a task to `PRIORITIES.yaml` with a
  back-link to the issue, so a bug found while using the console flows into the
  work queue. Agents can read `feedback` issues and run the promotion.
- **E2 evolution:** the button POSTs to `/feedback`; server creates the issue and
  can auto-open the PRIORITIES task. Same payload schema.

## 7 — Milestones

| # | Milestone | Deliverable | Acceptance |
|---|---|---|---|
| **M1** | Service layer + export | `src/quant/console/` readers + frozen view-model dataclasses + `console export` → `export/*.json` | unit tests ≥80% on readers; export runs idempotently from real artifacts; JSON validates against documented schemas |
| **M2** | Frontend scaffold | Vite + React + TS + Tailwind/shadcn; design tokens from `console.css`; layout shell, sidebar nav, routing, static data client | app builds; shell + nav match mockup at 320/768/1024/1440; keyboard nav + reduced-motion + visible focus |
| **M3** | Monitor panels | Overview, Strategies (roster→detail), Conditions — with charts via the chosen lib | render from `export/*.json`; mockup parity; charts use the instrument style; strategy roster swaps detail |
| **M4** | Evidence panels | Provenance, Feature Catalog (with monitoring stats: coverage, μ/σ, drift, staleness), Trial Registry, Data & Market | commit links resolve to the repo; catalog surfaces drift/stale status; registry shows deflation bar; no file paths in UI |
| **M5** | Explanations + polish | serif explanation cards; reusable `ⓘ` tooltips (hover + click-pin); a11y + responsive pass | tooltips work on hover + touch; contrast + keyboard audited; no overflow at the four breakpoints |
| **M6** | Issue reporting + tracker | Report button + modal; GitHub-issue submission with context; `feedback`→`PRIORITIES` promotion script + docs | a submitted report creates a labeled GitHub issue carrying context; promotion creates a PRIORITIES task linking the issue; **no tracker panel in UI** |
| **E1-CLOSE** | Project closeout (rule 21) | end-to-end validation that the console builds and renders **all panels from freshly exported real artifacts**; one-page closeout report | `depends_on` all E1 tasks; validation passes; report states delivered scope + deferred items |

## 8 — Data contract (export JSON, sketch)

Documented and drift-checked. Sketches (build finalizes):
- `strategies.json` — `[{id, name, mode, sharpe, return, sparkline[], driver, status}]`
- `strategy/<id>.json` — `{name, description, figures[], equity[], drawdown[], rolling_sharpe[], return_hist[], why}`
- `conditions.json` — `{by_condition[], heatmap[][], stress_windows[]}`
- `provenance/<run>.json` — `{commit, config, leakage_controls[], self_tests[], lineage[]}`
- `catalog.json` — `[{feature, group, coverage, dist[], mean, std, stability, oos_status}]`
- `ledger.json` — `{n_trials, luck_bar, best, runs[]}`
- `data_status.json`, `market.json` — feed freshness + market snapshot.

## 9 — Tech stack (pinned)

Vite + React + TypeScript SPA · Tailwind + shadcn/ui · charting lib (Plotly default,
ECharts acceptable — M3) · TanStack Table for dense tables · static build, no SSR.
Python service layer in `src/quant/console/`. **No new datastore** — DuckDB/parquet/
YAML you already produce. IBM Plex (Mono/Sans/Serif) with system fallbacks.

## 10 — Testing & methodology (binds rules 15–21)

- **Service layer:** unit tests land with code; ≥80% line coverage
  (`pytest --cov`). Export schemas under a drift check.
- **Frontend:** component/smoke tests (Vitest + Testing Library); visual checks at
  320/768/1024/1440; a11y (keyboard, reduced-motion, contrast).
- **Rule 17/21:** `E1-CLOSE` is the end-to-end validation gate; a one-page closeout
  report is the deliverable. `ruff` + lint clean; red CI blocks merge.

## 11 — Dependencies & sequencing

- **E1 depends only on existing artifacts** + the new service layer → buildable now.
- **E2 (Console API)** depends on E1's service layer + **Project C1 (live data)**.
- **E3 (Live Monitoring)** and **E4 (Data & Market Status)** depend on **E2**.
- Build order within E1: M1 → M2 → (M3 ∥ M4) → M5 → M6 → E1-CLOSE.

## 12 — Risks & mitigations

| Risk | Mitigation |
|---|---|
| Charting-lib choice churns the UI | service layer emits neutral series; lib swap is presentation-only |
| Static JSON goes stale vs artifacts | export is one command + a freshness stamp shown in the UI; E2 replaces with live API |
| Scope creep (more panels/features) | mockup is the frozen scope contract; new asks become `feedback` issues → PRIORITIES, not silent additions |
| Faking live data reads as inauthentic | live tiles render explicit research-mode/empty states until E3/E4 |
| IBM Plex offline | system-font fallbacks in tokens |

## 13 — Definition of done (E1)

All eight panels render from freshly exported real artifacts at mockup parity;
commit links resolve to `James-Delgado/quant`; the Report button creates a
context-carrying `feedback` GitHub issue and the promotion path creates a
`PRIORITIES.yaml` task; service-layer coverage ≥80%; responsive + a11y floors met;
`E1-CLOSE` validation + closeout report landed.

---

*Companion PRDs (to be written next, lighter): **E2 — Console API** (depends on C1),
**E3 — Live Monitoring** (depends on E2; supersedes C5), **E4 — Data & Market
Status** (depends on E2). A clear-context agent translates each PRD's milestones
into `PRIORITIES.yaml` tasks.*
