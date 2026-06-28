# Research Console — frontend (Project E1)

The disposable presentation layer for the Research & Trust Console. A Vite +
React + TypeScript SPA that **renders** the static export produced by the Python
service layer in `src/quant/console/`. All trust/data logic lives in that
service layer; this app contains layout + a thin data client only
(`docs/project-e/DECISIONS.md` #1).

## Directory decision (E1-M2)

The console frontend lives at repo-root **`frontend/`**. `PRIORITIES.yaml`
carried `frontend/` as a placeholder and delegated the real choice to this, the
first frontend task. Rationale: `frontend/` is the canonical Vite location and
keeps the *disposable* UI clearly separated from the *durable* Python core under
`src/quant/`. The future E2 FastAPI service reuses the same readers; only this
layer is ever thrown away.

## Data flow

```
src/quant/console/export/*.json   (M1 output, gitignored, reproducible)
  → scripts/copy-export.mjs        (predev / prebuild / pretest)
  → frontend/public/data/*.json    (gitignored)
  → src/lib/dataClient.ts          (fetch + types)
  → panels
```

Regenerate the export from repo root before serving:

```bash
PYTHONPATH=src .venv/bin/python -m quant.console export
```

## Commands

```bash
npm install          # install deps
npm run dev          # sync data + dev server
npm run build        # type-check + production build (tsc -b && vite build)
npm run test         # sync data + vitest (jsdom)
npm run typecheck    # tsc --noEmit
```

## Design system

Tokens are ported verbatim from `docs/project-e/mockups/console.css` into
`src/styles/` (IBM Plex Mono/Sans/Serif with system fallbacks; cool-graphite
chrome; **color only inside data**). The mockup at
`docs/project-e/mockups/research-trust-console.html` is the frozen visual +
interaction contract.

## Scope by milestone

- **M2 (this scaffold):** app shell — sidebar nav (Monitor / Evidence /
  Reference), routing, topbar, static data client. Panels are placeholders.
- **M3:** Overview, Strategies, Conditions (charts — library chosen in M3).
- **M4:** Provenance, Feature Catalog, Trial Registry, Data & Market.
- **M5:** Explanations + tooltips + a11y/responsive pass.
- **M6:** Report-issue modal + GitHub submission + promotion script.

shadcn/ui is configured (`components.json`, `@/lib/utils` `cn`) — primitives are
added per-component as panels are built.
