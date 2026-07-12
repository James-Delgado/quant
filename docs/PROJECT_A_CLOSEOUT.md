# Project A — Research Substrate & Methodology: Closeout Report

> **Status: CLOSED / MAINTAIN.** Project A's research substrate — feature
> catalog, priorities drift test, append-only trial-count ledger, runner→ledger
> integration, and the DSR-aware gate — is built, unit-tested in isolation, and
> now **validated as an assembled whole** end-to-end across its module
> boundaries. The composition reproduces the known Phase 4A NO-GO from the
> shipped machinery.
>
> **Closeout task**: `A-CLOSE` (`docs/PRIORITIES.yaml`).
> **Validation notebook**: [`notebooks/12_project_a_closeout.ipynb`](../notebooks/12_project_a_closeout.ipynb) (checkpoint-only; `nbconvert --execute` green).
> **Methodology**: [`docs/METHODOLOGY.md`](METHODOLOGY.md) §21 (project closeout).
> The project-scale analog of §20's post-task review and of
> [`PHASE_4A_REPORT.md`](PHASE_4A_REPORT.md) / [`B1_REPORT.md`](B1_REPORT.md).

## 1 · What Project A delivered

Project A is the research **substrate** — the harness, catalogs, ablation
orchestrators, regime machinery, runner pattern, and the methodology contract
(rules 1–21) distilled from [`PHASE_4A_RETROSPECTIVE.md`](PHASE_4A_RETROSPECTIVE.md).
The post-Phase-4A methodology-upgrade tasks this closeout gates on:

| Task | Delivered | Artifact |
|---|---|---|
| `A-LEDGER` | Append-only trial-count ledger + writer + CI drift test (METHODOLOGY §12) | `src/quant/ledger.py`, `data/ledger.yaml`, `tests/test_ledger.py` |
| `A-PRIORITIES-TEST` (+`-TS`) | Backlog integrity drift test: status enum, dependency resolution, single-`in_progress`, timestamp presence/ordering (§6) | `tests/test_priorities.py` |
| `A-LEDGER-RUNNERS` | `record_run` maps a runner's `metadata.json` → `LedgerEntry`, append-only + idempotent by `config_hash` (§12) | `src/quant/ledger.py::record_run`, `scripts/run_phase4a_arms.py` |
| `A-DSR-GATE` (+`-LEDGER-SHARPE`, `-WIRE-EMPIRICAL-STD`, `-DOC-SYNC`, `-METH-SCHEMA-SYNC`) | Two-stage DSR-aware gate: regime Sharpe/DM gate **and** deflated Sharpe with `N` read from the ledger (§13) | `src/quant/backtest/regime_metrics.py::dsr_aware_gate_report`, `src/quant/backtest/statistics.py`, `tests/test_regime_metrics.py` |

The feature catalog (`features/catalog.{py,yaml}`, Phase 4A M4) is the earlier
half of the same substrate and is exercised here as the reference §6 drift
contract.

## 2 · What the notebook validated (the integration seams)

Per-task unit tests certify each piece alone; they do **not** certify the pieces
*compose*. `notebooks/12_project_a_closeout.ipynb` exercises the real, shipped
surface across every module boundary and asserts each seam green (the notebook's
final cell fails `nbconvert --execute` loudly on any regression):

| Seam | What is exercised | Verdict |
|---|---|---|
| `catalog_contract` | `load_catalog()` → 27 records; `validate_catalog_coverage` green forward, and an injected unregistered column raises (both directions, §6) | PASS |
| `priorities_drift` | `tests/test_priorities.py::validate_priorities` on the committed file (132 tasks); exactly one `in_progress` | PASS |
| `ledger_read` | `load_ledger()` → 16 entries; `cumulative_trial_count()` = **75**; `observed_sharpe_std()` = `None` (no sharpe-bearing entries → pinned-scalar fallback) | PASS |
| `runner_ledger` | `record_run()` on a **temp** ledger: `N` 75→78, re-run same `config_hash` is a no-op; real audited `data/ledger.yaml` untouched (§9 honest declaration) | PASS |
| `dsr_gate` | Real `arima`+`signed` Phase 4A checkpoints → `dsr_aware_gate_report`, which reads `N=75` from the ledger and deflates | PASS |

**The DSR-aware gate verdict (assembled from checkpoints).** On the 5394-bar
aligned OOS panel (2004-06-21 → 2026-03-31), GBM+`signed_returns` vs the ARIMA
control:

- **Stage 1 (regime Sharpe/DM gate)**: `pass_count = 0` — GBM loses to ARIMA on
  Sharpe in every required regime (qe_bull −0.03 vs +1.06; covid −1.28 vs +0.40;
  rate_cycle −0.44 vs +0.41). `stage1_passed = False`.
- **Stage 2 (deflated Sharpe, `N=75`, `sharpe_std=0.35` pinned fallback)**:
  `sr_observed = −0.34`, `sr_benchmark(E[max]) = +0.85`, `dsr = 0.00`,
  `dsr_passed = False`.
- **Combined `gate_passed = False`** — the assembled substrate reproduces the
  Phase 4A NO-GO ([`PHASE_4A_REPORT.md`](PHASE_4A_REPORT.md)) end-to-end, which
  is exactly the negative-control behaviour a trustworthy gate should exhibit on
  this data.

## 3 · Deferred scope / declared limitations

- **Empirical Sharpe dispersion is never exercised on real data.** No ledger
  entry carries a `sharpe` (all 16 are pre-`sharpe`-field or infrastructure
  entries), so `observed_sharpe_std()` returns `None` and the DSR stage always
  falls back to the pinned `DEFAULT_SHARPE_STD = 0.35`. The empirical-dispersion
  path (`A-DSR-WIRE-EMPIRICAL-STD`) is unit-tested but has no live data to bite
  on. Follow-up **`A-LEDGER-SHARPE-BACKFILL`** (appended to `PRIORITIES.yaml`)
  backfills `sharpe` into the historical Phase 4A / B1 entries so future gates
  deflate against the empirical spread rather than the scalar.
- **`record_run` demonstrated on a temp copy, not the audited file** — by design
  (§9): the closeout must not append a synthetic trial to the append-only
  CI-audited ledger. The seam is proven; no real trial was written.
- **Checkpoint-only DSR demonstration.** The gate runs on the existing Phase 4A
  `arima`/`signed` checkpoints (METHODOLOGY §7), not a fresh fit — this is the
  intended pattern for a verdict notebook and keeps closeout reproducible in
  seconds.

## 4 · Definition of done

All nine `A-CLOSE` dependencies are `done`; the validation notebook executes
green with all five seams passing and zero error cells; `ruff` clean; the
priorities and ledger drift tests pass. Project A is **closed** and moves to
**maintain** (the 467-test suite + drift tests are the maintenance surface).
Per the AGENT_OPERATION Step 7 corollary, any task appended to Project A after
this closeout must be added to a re-opened `A-CLOSE` dependency set.

*Built at `f8c057d`. Closeout notebook: `notebooks/12_project_a_closeout.ipynb`.*
