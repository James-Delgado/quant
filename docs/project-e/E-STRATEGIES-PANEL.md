# E-STRATEGIES-PANEL — Strategy Portfolio panel (companion doc)

> Companion to the **code** deliverable. The real deliverable of this task is the
> tested service-layer reader + the React panel; this doc records *what* the panel
> shows, *where* the pieces live, and the *boundary* it must not cross. It is not a
> spec written ahead of the code — it documents what shipped.

## What it is

The **Strategy Portfolio** panel renders the C6 *deployment* registry in the
Research & Trust Console: every strategy that is **in use** (enabled — live in the
daily run, sharing capital equally) and every strategy that is **idle**
(configured but not yet deployed). It answers "what is configured to run, and how
is capital split across it?" — the deployment-side counterpart to the research
**Strategies** panel.

## Why a separate panel (not the existing Strategies page)

The existing `frontend/src/pages/Strategies.tsx` is the **research-arm** roster:
Phase-4A arms (ARIMA control, GBM variants) with equity curves, drawdown, and
gate verdicts, each benchmarked against the ARIMA control (DECISIONS #10). The C6
registry is a **different roster** — deployable strategies with deploy status,
equal-weight allocation, and provenance, and *no* equity curves. Folding the two
together would conflate "what we researched" with "what we deployed". They are
kept as distinct Monitor panels: **Strategies** (research) and **Portfolio**
(deployment).

## The E1 / E3 boundary (honest states — DECISIONS #5/#7)

This is the **static** deployment portfolio. It shows configuration, not realized
results. **Live per-strategy P&L is E3 (live monitoring)** and is deliberately
absent — the panel makes no claim it cannot back. The footer note states this
explicitly rather than rendering an empty or faked P&L tile.

## Per-strategy fields

display_name · description · model · prediction target · universe (as tags) ·
status (in use / idle) · allocation % (equal-weight `1/N` across enabled
strategies; `—` for idle) · provenance summary (placeholder vs. gate-verified).

## Where it lives

| Layer | File |
|---|---|
| Registry + serializable view-model (C6-M1) | `src/quant/execution/strategy_registry.py` (`strategy_view_models`) + `strategy_registry.yaml` |
| Reader (reuses the C6 view-model — no recompute) | `src/quant/console/readers.py` (`load_portfolio`) |
| Frozen DTOs | `src/quant/console/viewmodels.py` (`PortfolioView`, `PortfolioStrategy`) |
| Schema (drift-checked export contract) | `src/quant/console/schemas.py` (`portfolio.json`) |
| Static export | `src/quant/console/export.py` → `export/portfolio.json` |
| Injectable source path | `src/quant/console/sources.py` (`ConsoleSources.registry_path`) |
| React panel | `frontend/src/pages/Portfolio.tsx` (route `portfolio`, nav "Portfolio") |
| TS contract mirror | `frontend/src/types/viewmodels.ts` (`PortfolioView`) |

## Data contract (`portfolio.json`)

```jsonc
{
  "strategies": [
    {
      "id": "arima_placeholder",
      "display_name": "ARIMA(1,0,0) Placeholder",
      "description": "...",
      "model_ref": "arima_baseline",
      "target_ref": "next_bar_return",
      "universe": ["SPY", "QQQ", "IWM"],
      "cadence": "daily",
      "broker": "alpaca_paper",
      "status": "enabled",            // "enabled" (in use) | "idle"
      "allocation_pct": 100.0,         // equal-weight 1/N; 0 while idle
      "provenance": "placeholder",
      "provenance_summary": "Placeholder (infrastructure — no edge claim)"
    }
  ],
  "n_enabled": 1,
  "n_idle": 0
}
```

## Tests

- Service layer: `tests/test_console.py` (`test_load_portfolio_*`, export-count +
  schema-validation cases) — ≥80% coverage on `src/quant/console`.
- Frontend: `frontend/src/test/Portfolio.test.tsx` + the `portfolio.json` contract
  case in `contract.test.ts`.
