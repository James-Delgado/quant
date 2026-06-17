# Phase 1 — Purged Walk-Forward Backtester

> **Spec document.** See `PHASE_0_INFRASTRUCTURE.md` for full project context.
> This describes what Phase 1 builds, why, the design, and the exit gate that
> must be cleared before Phase 2 begins.

> **Status: ✅ COMPLETE** — all exit-gate criteria met. Key commits:
> `a456b84` (initial implementation), `6e735bf` (review fixes).
> Implemented modules: `walkforward.py`, `simulator.py`, `metrics.py`,
> `harness.py`, `report.py`. Test suite: 87 passed / 4 skipped (live API).
> See `notebooks/01_system_tour.ipynb` for an executed end-to-end demo.

---

## Objective

Build the project's **evaluation engine** — the "scientific instrument" — and
build it *before* any predictive model exists. Its job is to produce honest,
leak-free, cost-aware estimates of strategy performance. If this component is
wrong, every downstream modeling decision is measured with a broken ruler and
the whole project is invalid. This is the highest-priority, highest-rigor
phase.

---

## Entry gate (prerequisites)

- Phase 0 data lake operational and **verified against live APIs**.
- `equity_bars_daily`, `equity_eod_tiingo`, and `macro_fred` datasets populated
  with multi-year history via the backfill script.
- DuckDB catalog queries confirmed working over the processed lake.

---

## Scope — what to build

Three components:

1. A **model-evaluation layer** — purged walk-forward cross-validation.
2. A **trade-simulation layer** — turns predictions into realistic P&L.
3. A **reporting layer** plus a **harness self-test suite**.

Out of scope for Phase 1: any actual predictive model. The harness is exercised
in Phase 1 only with trivial reference strategies (see *Harness self-validation*).

---

## Design detail

### Model-evaluation layer

- **Walk-forward testing.** Slide a train/test window through history; every
  prediction uses only prior data. Use a **rolling** (fixed-length) window —
  regimes shift and stale data misleads. The walk-forward step size *is* the
  production retraining cadence; make it configurable.
- **Purging.** Trading labels depend on a forward window (e.g. "return over
  next N days"). Remove from the training set any sample whose label window
  overlaps the test set.
- **Embargo.** After purging, drop a small buffer of samples around the
  train/test boundary to defeat serial-correlation leakage. Buffer size
  configurable (a few days for daily data).
- **CPCV (Combinatorial Purged Cross-Validation).** The advanced mode: form
  many purged train/test combinations to yield a *distribution* of backtest
  outcomes. **Design the harness so CPCV can be switched on later**; it need
  not be the default in Phase 1, but the interfaces must not preclude it.

### Trade-simulation layer

A correct evaluation scheme still produces fantasy returns if fills are naive.
Model all of:

- **Transaction costs** — commissions and fees, per-broker configurable.
- **Slippage and spread** — buy at the ask, sell at the bid; never the mid.
- **Realistic fills** — no execution at the exact signal-bar close. Default to
  next-bar open. At size, model partial fills and **market impact**.
- **Latency** — a configurable signal-to-execution delay.
- **Liquidity cap** — do not allow trading more than a set fraction of bar
  volume.

All cost/slippage parameters must be **explicit, documented, and defensible** —
not buried magic numbers.

### Data hygiene (mandatory)

- **Survivorship bias.** The backtest universe must be **point-in-time
  correct** and include delisted/bankrupt names. A universe of only-survivors
  systematically overstates returns.
- **Point-in-time fundamentals.** Use values as known on the date, not later
  restatements. The `ingested_at` stamp from the Phase 0 lake is the basis for
  this.

### Metrics and reporting

Judge the strategy, not the model. The report must include:

- Risk-adjusted: **Sharpe, Sortino, Calmar**, maximum drawdown.
- Trading: hit rate, profit factor, **turnover** (costs scale with it).
- The **in-sample vs out-of-sample** comparison — a large gap signals
  overfitting.
- **Deflated Sharpe Ratio** — corrects for the number of strategy
  configurations tried (multiple-testing).
- The equity curve, and — once CPCV is enabled — the distribution of outcomes.

### Harness self-validation (critical)

Before the harness is trusted, it must pass these self-tests:

1. **Random/no-skill strategy** → produces approximately zero edge, slightly
   negative after costs. Confirms costs are applied and there is no accidental
   edge baked in.
2. **Perfect-foresight strategy** → produces large positive returns. Confirms
   the P&L accounting is correct.
3. **Intentionally leaky strategy** → run a strategy that uses future
   information, with purging/embargo *off* (looks too good) and *on* (inflation
   removed). Confirms the leakage controls actually work.

---

## Suggested module structure

Extends the Phase 0 repo:

```
src/quant/backtest/
├── walkforward.py   purged walk-forward + CPCV split generation
├── simulator.py     fills, costs, slippage, latency, liquidity cap
├── metrics.py       Sharpe/Sortino/Calmar/DSR, drawdown, turnover
├── report.py        equity curve + metrics report generation
└── harness.py       orchestrates: model + date range + lake -> report
tests/
└── test_backtest.py the three self-validation tests above
```

---

## Deliverables

- The `backtest` package implementing all three layers.
- A report generator (equity curve + metrics; HTML or notebook output).
- The harness self-test suite, passing.
- A short written document of the cost/slippage assumptions and their sources.

---

## Exit gate (success criteria)

Phase 2 may begin only when:

- All three harness self-tests pass.
- The harness can take an arbitrary predictive model plus a date range and
  produce a complete, leak-free performance report.
- Cost and slippage assumptions are documented and defensible.

---

## Risks and pitfalls

- **Look-ahead leakage** — the dominant failure mode. Purging, embargo, and
  point-in-time data discipline exist to prevent it.
- **Optimistic fills** — assuming execution at favorable prices inflates
  results.
- **Survivorship bias** — silently excludes losers.
- **Multiple testing / p-hacking** — trying many configs and reporting the
  best. The Deflated Sharpe Ratio and CPCV are the defenses.
- **Overfitting the cost model** — tuning cost assumptions until a strategy
  looks good. Costs are inputs, not parameters to optimize.

---

## Tooling

`mlfinlab` (purged/combinatorial CV, Deflated Sharpe, volume/dollar bars),
event-driven frameworks such as Backtrader or vectorbt for fill simulation,
`statsmodels` for the ARIMA reference baseline, pandas/polars and DuckDB for
data access. LEAN remains the later execution-grade cross-check (Phase 4).

---

## What comes next

Phase 2 builds the first real predictive model (gradient-boosted trees) and
evaluates it **through this harness** against an ARIMA baseline.
