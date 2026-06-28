# Project E — Decision Log & Rationale

> **Purpose:** the *why* behind Project E, captured for any clear-context agent or
> contributor who inherits the PRDs. The PRDs (`E1…E4`) say *what* to build; this
> says *why it's shaped that way* and *what was deliberately rejected*, so decisions
> aren't silently re-litigated or reversed. Authored 2026-06-27 from the design
> session that produced the mockup + PRDs.

---

## What Project E is

A **Human Interface & Observability** layer that makes the platform's results,
models, and data human-visible. New top-level **Project E**, decomposed into four
sub-projects:

- **E1 — Research & Trust Console** (buildable now, over existing artifacts)
- **E2 — Console API** (gated on Project **C1**)
- **E3 — Live Monitoring** (gated on **E2** + C2/C3; **supersedes roadmap §C5**)
- **E4 — Data & Market Status** (gated on **E2** + C1)

Chain: **E1 → E2 → E3/E4**. Rationale for decomposing instead of one project: the
four span independent subsystems with different dependencies; bundling them would
recreate the confounding the methodology exists to avoid, and would block the
immediately-valuable E1 on infrastructure that doesn't exist yet.

## Key decisions (with rationale)

1. **Two-layer architecture: tested Python service layer (`src/quant/console/`) +
   disposable UI.** All trust/data logic lives in the service layer; the UI only
   renders. This is what makes the UI cheap to rewrite and lets E2's API reuse the
   *same* readers — no logic duplication.

2. **UI = React + TypeScript** (Vite SPA, Tailwind + shadcn/ui). Chosen for polish
   and a professional, non-templated look. **Rejected:** Streamlit (fast but low
   polish ceiling, welds logic to the framework) and Dash (a middle option). The
   decoupling above is what makes "React now" safe — only the presentation is ever
   thrown away, never the logic.

3. **Data delivery: static JSON export now → FastAPI later (E2).** E1 exports
   view-models to static JSON the React app fetches; no server. Honors "build the
   API later." The export script and the future API call the *same* service-layer
   readers, so the migration is a data-source swap, not a rewrite.

4. **Design direction: dark "research instrument."** Approved by the user. Elevated
   *past* the generic AI-default ("near-black + one bright accent") — **color appears
   only inside data**, chrome is monochrome cool-graphite, type is the **IBM Plex**
   family (Mono for numerics, Sans for UI, Serif for the reading view), and the
   **signature is a consistent instrument plotting language** (hairline grids, mono
   ticks, stress shading, small-multiples). Tokens live in `mockups/console.css`.

5. **It is a production daily-driver, NOT an interview/demo pitch.** This was the
   hard lesson from a rejected first mockup. Binding UI conventions:
   - **Never *say* "trust"** — convey it through substance (metrics, provenance,
     monitoring). The nav group is "Evidence," not "Trust."
   - **No self-promotion / no bug-fix war stories** in the UI; leakage controls read
     as quiet enforced-status.
   - **No internal file paths** in the UI (`data/ledger.yaml` → "Trial Registry").
   - **GitHub commit links** resolve to `github.com/James-Delgado/quant`.
   - Lineage and similar lists render **one item per line**, not dot-separated.
   - Metrics presented large; visualizations are first-class, not afterthoughts.

6. **Regimes → "Conditions."** Primary attribution axis is **live-computable
   conditions** (vol / trend / rates), reusing the repo's existing detectors. Named
   historical episodes (GFC, COVID, 2022 selloff) are demoted to a **stress-windows**
   view. Rationale: condition/stress attribution is standard quant practice and
   on-brand for Bridgewater; carving the timeline into bespoke named macro eras as
   the *primary* axis was a research-evaluation artifact, not how a desk consumes it.

7. **Daily job-to-be-done (the user's own words), drives the Overview:**
   "which live strategies are up/down and **why**", "data status, market status",
   "overall portfolio performance", with provenance drilled into when it makes sense.
   Because E1 has no live execution yet, live-strategy and market tiles render in a
   **clearly-marked research/placeholder state** (no faked live P&L — that read as
   inauthentic) and light up in E3/E4.

8. **Feature Catalog is a monitoring surface,** not just a registry: coverage, μ/σ,
   a distribution mini, and a **stability status** (stable / drifting / stale) per
   feature, with a nightly drift check. (User request.)

9. **Info tooltips** (hover + click-to-pin) on unfamiliar financial terms, so the UI
   stays uncluttered while explaining itself. (User request.) Plus a full
   Explanations panel.

10. **Strategies = roster → detail** with a one-line description per strategy.
    (User request.) New/live strategies append here.

11. **Issue reporting: UI is the "Report an issue" button + modal ONLY.** There is
    **no user-facing tracker panel** (the user explicitly does not want it visible).
    The tracker is **engineer/agent-visible**: reports become `feedback`-labeled
    **GitHub issues** (E1: pre-filled issue / `gh`; E2: `POST /feedback`), and a
    scripted **`feedback → PRIORITIES.yaml` promotion** turns a report into a backlog
    task with a back-link. Reports auto-capture context (panel + build SHA).

## Conventions for build agents (consolidated)

- Mockup `docs/project-e/mockups/` is the **frozen scope + visual + interaction
  contract**; new asks become `feedback` issues → tasks, not silent additions.
- Reuse the existing `storage/` + `features/` modules; **no new datastore**.
- Methodology rules 15–21 bind: tests land with code, ≥80% coverage, `ruff` clean,
  red CI blocks merge, every sub-project ends with a `*-CLOSE` end-to-end validation
  + one-page closeout report.
- Pre-committed constants (reconciliation/alert thresholds) follow rule 1.

## Still open (decide during build)

- **Charting library:** Plotly default; ECharts acceptable — decide in E1-M3. Service
  layer emits **neutral chart-ready series** so the choice stays reversible.
- **Alert channel (E4):** log / email / push — TBD in E4.
- **PRD location:** these live in `docs/project-e/`; could also be mirrored to
  `.claude/prds/` if the planning pipeline expects them there.

## Reconciliations pending (must happen during task generation)

- **Add Project E (E1–E4)** to `docs/PROJECT_ROADMAP.md` and the `CLAUDE.md` status
  section.
- **Mark roadmap §C5 ("Monitoring + reconciliation") as superseded by E3.**
- Wire `depends_on` across E1→E2→E3/E4 **and** the cross-project gates
  (E2→C1, E3→C2/C3, E4→C1) when writing `PRIORITIES.yaml` tasks.
