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
keeps the _disposable_ UI clearly separated from the _durable_ Python core under
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
npm run lint         # eslint (flat config)
npm run lint:fix     # eslint --fix
npm run format       # prettier --write .
npm run format:check # prettier --check . (CI-style, no writes)
```

## Linting & formatting (E1-M2-FE-LINT)

ESLint (flat config, `eslint.config.js`) + Prettier (`.prettierrc.json`) are the
JS-side analog of the repo's `ruff` gate (METHODOLOGY §19). ESLint covers
`src/**` and the JS tooling files with typescript-eslint + react-hooks +
jsx-a11y + react-refresh; Prettier owns formatting (ESLint defers to it via
`eslint-config-prettier`). The frontend gate before committing is:

```bash
npm run lint && npm run format:check && npm run build && npm run test
```

Notes:

- The root TypeScript config files (`vite.config.ts`, `tailwind.config.ts`) are
  out of ESLint scope — they are already type-checked by `tsc -b` via
  `tsconfig.node.json`.
- The instrument CSS in `src/styles/` (ported verbatim from the mockup) is in
  `.prettierignore` so the "ported verbatim" provenance and diff stay intact; it
  is reviewed against the frozen mockup, not Prettier.
- There is no GitHub Actions workflow in this repo yet, so the gate above is run
  locally. A CI workflow that runs both the Python suite and this frontend gate
  is tracked as a follow-up (`E1-CI-WORKFLOW`).

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
