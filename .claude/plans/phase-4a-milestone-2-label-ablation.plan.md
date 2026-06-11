# Plan: Phase 4A — Milestone 2 (Label-Scheme Ablation Matrix)

**Source PRD**: `.claude/prds/phase-4a-feature-and-label-redesign.prd.md`
**Selected Milestone**: Milestone 2 — Label-scheme ablation matrix
**Complexity**: Medium
**Depends on**: Milestone 1 (regime-conditional evaluation harness)

## Summary

Build three label schemes (signed return already exists; add vol-scaled returns and triple-barrier per López de Prado), an ablation orchestrator that runs each scheme through `run_portfolio_backtest` with identical hyperparameters, and a per-regime comparison reporter that ranks schemes by Sharpe with pairwise DM tests. Verdict either replaces signed-return as the default for Phase 4A or documents that no label scheme alone clears the trend-fighting bias in `rate_cycle` (which nb05 identified as the dominant failure regime, Δ Sharpe = −1.44 vs ARIMA).

**Meta-labeling (the PRD's 4th option) is deliberately deferred to a follow-up sub-milestone.** It requires a primary-model dependency and a two-stage training pipeline that don't fit the existing single-model harness. If signed/vol-scaled/triple-barrier identifies a winner, meta-labeling becomes a refinement of that winner in Milestone 2.5. If none of the three work, meta-labeling alone is unlikely to rescue the bias and the next move is Milestone 3 (regime-aware features) — see Risks.

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Label functions | `src/quant/features/labels.py:21-60` | Pure function with upfront validation, returns `LabelResult(series, horizon_bars)` NamedTuple; explicit `ValueError` on every contract violation |
| Result containers | `src/quant/features/labels.py:14-18` | `NamedTuple` for tight (data, metadata) bundles; for richer results use `@dataclass(frozen=True)` per `harness.py:27-35` |
| Module layout | `src/quant/features/labels.py:1-7` | Top docstring states the *invariant* (e.g., horizon-coupling); `from __future__ import annotations` |
| Ablation orchestration | `src/quant/backtest/harness.py:398-435` (`evaluate_panel`) | Take a name→object mapping; `copy.deepcopy(object)` per run; forward identical kwargs to every `run_portfolio_backtest` call |
| Per-regime reporting | `src/quant/backtest/report.py:_REGIME_TABLE_COLUMNS` + `format_regime_report` | `regime_summary_table`-style DataFrame; `format_*_report` returns `str` via `io.StringIO`; `_fmt()` for NaN/None |
| Tests | `tests/test_regimes.py:14-90` + `tests/test_regime_metrics.py:43-100` | Class-grouped pytest, `np.random.default_rng(seed)`, one synthetic helper at top, AAA pattern |
| Notebook structure | `notebooks/05_phase4a_regime_harness.ipynb` | Self-contained walk-through: synthetic demo → real-data slice → per-regime comparison → self-tests |

## Files to Change

| File | Action | Why |
|---|---|---|
| `src/quant/features/label_schemes.py` | CREATE | Two new label generators: `vol_scaled_returns(prices, horizon, vol_window)` and `triple_barrier_labels(prices, max_horizon, pt_sigma, sl_sigma, vol_window)`. Re-export `generate_labels` from `labels.py` under the new name `signed_returns` for consistency with the scheme registry |
| `src/quant/features/labels.py` | UPDATE | Add a one-line alias `signed_returns = generate_labels` and a `LABEL_SCHEMES` registry mapping scheme-name → callable, so the ablation runner can import a single dict |
| `src/quant/backtest/ablation.py` | CREATE | `run_label_ablation(label_schemes, model, panel, **kw)` returns `dict[scheme_name, BacktestResult]`. Same pattern as `evaluate_panel` but iterates over label schemes (rebuilding `labels_by_symbol` per scheme) instead of over models |
| `src/quant/backtest/report.py` | UPDATE | Add `format_ablation_report(results, regime_labels)` and `ablation_summary_table(results, regime_labels)` — one row per scheme × regime, cells = Sharpe / Sortino / MaxDD / n_bars. Add pairwise DM-test table when forecast errors are available |
| `tests/test_label_schemes.py` | CREATE | Per-scheme: validation errors, point-in-time invariant (label at bar t uses only data ≤ t+horizon), known-input correctness, NaN handling at series tail |
| `tests/test_ablation.py` | CREATE | Ablation runner: identical kwargs forwarded, deepcopy isolates state, returns dict keyed by scheme name; smoke test on synthetic panel; the ranking reporter produces expected shape |
| `notebooks/06_phase4a_label_ablation.ipynb` | CREATE | Walk-through: label-scheme demos on synthetic returns → small real-data ablation (5 syms × 5 yrs) → per-regime ranking → focused look at `rate_cycle` (the failure regime from nb05) |
| `docs/concepts/label-schemes.md` | CREATE | Definitions, rationale for each scheme, pre-committed parameter values (vol_window, pt_sigma, sl_sigma), point-in-time rule, update protocol mirroring `regime-evaluation.md` |
| `CLAUDE.md` | UPDATE | Mark Milestone 2 in-progress; cross-link the new doc + plan |

## Tasks

### Task 1: Implement `vol_scaled_returns(prices, horizon, vol_window=21)`

- **Action**: Compute `forward_return / rolling_realized_vol` where `rolling_realized_vol = returns.rolling(vol_window).std()` evaluated **strictly at bar t** (using returns ≤ t). The label denominator must be point-in-time — no look-ahead into the future window the label measures. Return `LabelResult(series=scaled, horizon_bars=horizon)`. Validation mirrors `generate_labels`: empty/zero/NaN/non-numeric/non-monotonic all raise.
- **Mirror**: `labels.py:21-60` for validation + return shape.
- **Rationale**: nb05 showed GBM fails hardest in low-vol/trending regimes (rate_cycle). A signed-return label gives the model the same training signal for a 0.5% move in low-vol as for a 5% move in high-vol — so the GBM ends up trained primarily on high-vol crisis bars and mis-applies that learning in calm trends. Vol-scaling standardises the training signal across regimes.
- **Validate**: `.venv/bin/pytest tests/test_label_schemes.py::TestVolScaledReturns -v` — known synthetic series produces the algebraically expected scaled values; NaN tail equals `horizon + vol_window - 1`; zero-vol windows raise rather than producing inf.

### Task 2: Implement `triple_barrier_labels(prices, config=LDP_DEFAULT)` with modular config

- **Action**:
  1. Define a frozen `TripleBarrierConfig` dataclass in `label_schemes.py`:
     ```python
     @dataclass(frozen=True)
     class TripleBarrierConfig:
         pt_sigma: float = 2.0
         sl_sigma: float = 1.0
         vol_window: int = 21
         max_horizon: int = 5
     LDP_DEFAULT = TripleBarrierConfig()
     ```
  2. Implement `triple_barrier_labels(prices, config=LDP_DEFAULT) -> LabelResult`. For each bar t:
     - `σ̂[t] = realised vol over returns[t-vol_window+1 .. t]` (point-in-time, no look-ahead)
     - `pt_barrier = prices[t] × (1 + config.pt_sigma × σ̂[t])`
     - `sl_barrier = prices[t] × (1 − config.sl_sigma × σ̂[t])`
     - Walk forward up to `config.max_horizon` bars; first-hit determines `+1` / `−1` / `0`.
  3. Return `LabelResult(series, horizon_bars=config.max_horizon)` — worst-case time-out becomes the purge horizon (conservative over-purge when actual fill is earlier).

  **Modularity rule**: the function never reads global state. All parameters flow through `config`. Notebooks override by passing a custom `TripleBarrierConfig(...)`; the canonical defaults live in `LDP_DEFAULT` and are documented in `docs/concepts/label-schemes.md` with citations. Code-side comments do **not** restate the rationale — the doc is the source of truth.

- **Mirror**: `labels.py:21-60` for validation prelude. Use `@dataclass(frozen=True)` per the `BacktestResult` pattern at `harness.py:27-35`.

- **López de Prado parameter rationale** (cited in `docs/concepts/label-schemes.md`, not in code):
  - **PT=2σ, SL=1σ (asymmetric).** AFML §3.5. Equity has positive drift, so a symmetric ±1σ barrier is biased against the natural carry. The 2:1 PT:SL encodes "take a trade if I expect ≥ 2σ upside and can survive 1σ adverse motion" — exactly the discipline that's missing in the signed-return GBM that mean-reverts in `rate_cycle`.
  - **vol_window=21.** One trading month. Canonical short-horizon vol estimator (AFML §3.5; Bouchaud & Potters, *Theory of Financial Risk*). Captures recent regime context without dragging in distant history.
  - **max_horizon=5.** One trading week. The AFML §3.5 sweet spot on daily bars — long enough to capture meaningful directional information, short enough to avoid the increasing noise floor at longer horizons.

- **Pre-commitment**: `LDP_DEFAULT` is pinned **before any model run**. Same discipline as the Milestone 1 VIX thresholds and the T1–T6 gates. Override only via a PR that explains the rationale and re-runs the full ablation.

- **Validate**: `.venv/bin/pytest tests/test_label_schemes.py::TestTripleBarrierLabels -v` — synthetic ramps hit PT only; synthetic crashes hit SL only; flat series produce all-0 labels; max_horizon=0 raises; passing a custom `TripleBarrierConfig(pt_sigma=3.0)` produces different barrier heights (test for modularity).

### Task 3: Build `run_label_ablation()` orchestrator

- **Action**: Implement `run_label_ablation(label_schemes, model, features_by_symbol, prices_by_symbol, **kw) -> dict[str, BacktestResult]`. For each scheme in `label_schemes`:
  1. Rebuild `labels_by_symbol` by applying the scheme's label generator to each symbol's price series.
  2. Forward all other kwargs (train_window, test_window, step, embargo, sim_kwargs) verbatim — identical hyperparameters across schemes (the `evaluate_panel` discipline).
  3. `copy.deepcopy(model)` per scheme so internal state can't bleed.
  4. Call `run_portfolio_backtest`; capture the result.
  5. Set `label_horizon` from `LabelResult.horizon_bars` (not from a caller-supplied value) so purge stays correct per scheme.
- **Mirror**: `harness.py:398-435` (`evaluate_panel`) end to end — same kwargs-discipline, same deepcopy, same return-dict shape.
- **Validate**: `.venv/bin/pytest tests/test_ablation.py -v` — synthetic panel + three schemes returns three results; kwargs verifiably identical (assert against a shared kw-dict); model deepcopy means seed=N produces identical first-fold predictions across two consecutive runs of the same scheme.

### Task 4: Balanced multi-regime ablation reporting

- **Action**: Add to `report.py`:
  - `ablation_summary_table(results, regime_labels) -> pd.DataFrame` — rows = scheme, columns = `{aggregate, qe_bull, covid, rate_cycle}` (era axis), cells = OOS Sharpe. The aggregate is the full-period OOS Sharpe; the per-regime columns are sliced via `compute_regime_metrics`.
  - `ablation_composite_ranking(results, regime_labels) -> pd.DataFrame` — **balanced multi-regime ranking** by Borda count:
    1. For each regime column (including aggregate), rank schemes 1 → N (1 = highest Sharpe).
    2. Composite score = mean rank across all regime columns. Lower is better.
    3. Output columns: `composite_rank`, `mean_rank_across_regimes`, plus the per-regime ranks for transparency.
    4. No regime gets special weighting — `rate_cycle` counts the same as `qe_bull`. The PRD's success metric ("GBM > ARIMA in ≥ 2 of 3 recent regimes") is multi-regime breadth, so the ranking should reflect that.
  - `format_ablation_report(results, regime_labels) -> str` — three sections in the 52-col text format:
    1. Per-regime Sharpe table (from `ablation_summary_table`)
    2. Composite ranking (from `ablation_composite_ranking`)
    3. Pairwise DM p-values per regime (from `ablation_dm_matrix`)
  - `ablation_dm_matrix(results, regime_labels) -> pd.DataFrame` — pairwise DM p-values per regime where forecast errors are available; only schemes whose underlying `BacktestResult` has populated `oos_forecast_errors` participate (triple-barrier's classification residuals still produce a valid DM-test input).
- **Why Borda over weighted scoring**: rank-based aggregation is robust to outlier Sharpe values (a single crisis-regime Sharpe of +5 shouldn't dominate the ranking). It also avoids choosing a weighting that itself becomes a p-hacking knob.
- **Mirror**: `report.py:format_regime_report` end to end; reuse `_fmt()` for NaN; same column widths and `.3f`/`.2%` specs.
- **Validate**: `.venv/bin/pytest tests/test_ablation.py::TestAblationReport -v` — known synthetic results produce the algebraically expected Borda ranks; ties handled deterministically; missing-regime-data schemes excluded with a `warnings.warn` rather than crashing the ranking.

### Task 5: Write `notebooks/06_phase4a_label_ablation.ipynb`

- **Action**: Self-contained walk-through that mirrors nb05's structure:
  1. Setup + imports
  2. Synthetic-data label-scheme demos (each scheme on a small handcrafted price series → confirm expected behaviour)
  3. Real-data ablation on a 5-symbol × ~8-year slice (same as nb05 §6 — fast iteration)
  4. Per-regime breakdown using `ablation_summary_table` — every scheme's Sharpe in every regime, all visible at once
  5. **Balanced multi-regime composite ranking** via `ablation_composite_ranking` — Borda ranks across `{aggregate, qe_bull, covid, rate_cycle}`, with the per-regime ranks shown alongside the composite so the verdict's rationale is auditable
  6. Pairwise DM tests per regime
  7. **Diagnostic appendix** — a short section that calls out per-regime patterns *after* the balanced ranking has produced its verdict. E.g., "scheme X wins the composite; here is how it does in `rate_cycle` specifically, since nb05 flagged that regime as the dominant failure attribution." This honours the balanced ranking as the primary verdict while preserving the regime-attribution insight from nb05.
  8. What's next — either "scheme X promoted; M3 builds on it" or "no scheme advances the composite ranking past the signed-return baseline; M3 features become the next bet"
- **Mirror**: nb05's section structure and the markdown-cell style.
- **Validate**: Execute end-to-end via `jupyter nbconvert --execute --inplace` with a ~10-min timeout; no execution errors; all key cells produce non-empty output.

### Task 6: Concept doc + CLAUDE.md update

- **Action**: `docs/concepts/label-schemes.md` — definitions of each scheme, pre-committed parameter values, point-in-time rule, the López de Prado citation, update protocol ("do not retune to make a scheme pass"). Cross-link from `regime-evaluation.md` and `evaluation-standards.md`. Update `CLAUDE.md`: mark Milestone 2 in-progress; brief delivery paragraph naming the three schemes and the verdict pattern.
- **Mirror**: `docs/concepts/regime-evaluation.md` tone and structure end to end.
- **Validate**: visual review only; `grep "label-schemes" docs/concepts/` returns the cross-links.

## Validation

```bash
# Full test suite — must stay green.
.venv/bin/pytest tests/ -v

# Coverage on new modules.
.venv/bin/pytest tests/test_label_schemes.py tests/test_ablation.py \
    --cov=src/quant/features/label_schemes \
    --cov=src/quant/backtest/ablation \
    --cov-report=term-missing

# Lint + format new files.
.venv/bin/ruff check src/quant/features/label_schemes.py \
    src/quant/backtest/ablation.py \
    tests/test_label_schemes.py \
    tests/test_ablation.py

# Notebook smoke (matches the convention from CLAUDE.md):
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=900 notebooks/06_phase4a_label_ablation.ipynb
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Triple-barrier's variable effective horizon violates the purge invariant | Medium | Use `max_horizon` as the conservative `label_horizon` for purge — over-purges when actual fill is earlier, but preserves the leakage control. Document the over-purge cost in the concept doc. |
| Vol-scaling denominator → 0 in low-vol stretches → label = ±∞ | Medium | Raise `ValueError` if any rolling-vol bar is exactly zero; warn if any is < 1e-6 (clipping risk). Tests cover both. |
| Triple-barrier parameters (pt_sigma, sl_sigma, max_horizon) become an implicit hyperparameter search | High | **Pin them in `label-schemes.md` before any model run** (`pt_sigma=2.0`, `sl_sigma=1.0`, `max_horizon=5`, `vol_window=21`). Same anti-p-hacking discipline as VIX thresholds and the T1–T6 gates. Any future change requires a PR that explains the rationale. |
| Ablation runtime — 3 schemes × 33 symbols × 116 folds × n_iter=50 = ~12,000 GBM fits ≈ 100 hours | High | Default the notebook to a 5–10 symbol slice (nb05 convention); the full-panel ablation belongs to Milestone 6. Optionally support `n_iter=10` for the ablation runner with an explicit "preview mode" flag — same approach as nb05's `RUN_GBM_PREVIEW`. |
| Triple-barrier's variable horizon makes per-bar forecast errors hard to define cleanly | Medium | Forecast error per bar = `(realised triple-barrier outcome) − (model prediction)` — same shape as the signed-return path. The DM test on these errors is well-defined for the regression formulation. Document that for triple-barrier the DM test is comparing *classification residuals*, which is a weaker claim than for continuous labels. |
| Meta-labeling complexity — defer or include? | Medium | **Defer to M2.5 sub-milestone** (or M3, depending on outcome of M2). Meta-labeling has a primary-model dependency that doesn't fit the existing single-model harness; including it forces a wrapper that complicates the ablation matrix. Cleaner to ship M2 with three schemes, then decide M2.5 scope based on M2 results. |
| The winning scheme might win on aggregate but lose in `rate_cycle` | Medium | The ablation reporter ranks schemes by `rate_cycle` Sharpe explicitly (not just aggregate). The PRD's success metric is regime-conditional; this milestone honours it. A scheme that lifts aggregate but worsens `rate_cycle` is *not* a winner under Phase 4A's framing. |
| Notebook 06 grows complex and duplicates code from nb05 | Low | Refactor shared helpers (real-data loader, panel builder) into nb06 inline; do not create a `notebook_utils.py` for one extra notebook. If a third notebook needs the same helpers, extract then. |

## Acceptance

- [ ] `vol_scaled_returns` and `triple_barrier_labels` exist, are tested, satisfy point-in-time, and produce `LabelResult` instances
- [ ] `run_label_ablation` returns a `dict[scheme_name, BacktestResult]` populated for all three schemes on a synthetic panel
- [ ] Hyperparameters demonstrably identical across schemes (kwargs-discipline test)
- [ ] `format_ablation_report` produces a per-regime Sharpe table + a balanced multi-regime composite Borda ranking (no regime gets special weighting)
- [ ] `TripleBarrierConfig` is module-level, frozen, default-instanced as `LDP_DEFAULT`; parameter rationale lives in `docs/concepts/label-schemes.md` with López de Prado citations, not in code comments
- [ ] `notebooks/06_phase4a_label_ablation.ipynb` executes end-to-end with no errors and produces a verdict (winning scheme OR "no scheme fixes rate_cycle")
- [ ] All existing 310 tests still pass; new tests bring the count to ~330+
- [ ] `docs/concepts/label-schemes.md` exists with pre-committed parameter values
- [ ] `CLAUDE.md` lists Milestone 2 status
- [ ] Triple-barrier purge handling documented; harness self-tests still pass
- [ ] Patterns mirrored, not reinvented (per the table above)

---
*Status: DRAFT — awaiting user confirmation before implementation.*
