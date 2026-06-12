# Plan: Phase 4A — Milestone 3 (Cross-Sectional + Regime-Aware Features + Per-Feature Ablation)

**Source PRD**: `.claude/prds/phase-4a-feature-and-label-redesign.prd.md`
**Selected Milestone**: Milestone 3 — Cross-sectional + regime-aware feature set + per-feature ablation
**Complexity**: Large
**Depends on**: Milestone 5 (corrected FRED joins — macro-derived regime features must be built on leak-free columns), Milestone 1 (regime metrics), Milestone 2 (ablation-orchestrator pattern; label default)
**Execution order**: second of the remaining milestones (after M5, before M4/M6).

## Summary

Add the two feature families the current model lacks — cross-sectional ranks
(where does this symbol sit *relative to the universe today*) and explicit
regime indicators (what kind of market is this) — then measure each candidate's
per-regime edge with an add-one-feature ablation against the 17-feature
baseline. The PRD success metric is: **≥ 3 features show ≥ 0.1 Sharpe lift net
of costs in ≥ 1 regime**, and SHAP rankings agree with OOS ablation lifts on
which features dominate. Label scheme is `signed_returns` throughout — M2's
verdict kept it as default (no scheme fixed `rate_cycle`), and changing labels
and features simultaneously would confound attribution.

**Candidate features are pre-committed in this plan (anti-p-hacking).** Seven
candidates, no additions mid-flight; anything discovered during the work goes
into a "future candidates" list in the glossary, not into this ablation.

## Pre-committed candidate features

| # | Feature | Family | Definition (point-in-time at bar *t*) |
|---|---|---|---|
| 1 | `xs_rank_ret_21d` | cross-sectional | Percentile rank of `ret_21d` across all universe symbols with data at *t* (0–1) |
| 2 | `xs_rank_ret_252d` | cross-sectional | Percentile rank of `ret_252d` across the universe at *t* |
| 3 | `xs_rank_vol_21d` | cross-sectional | Percentile rank of `vol_21d` across the universe at *t* |
| 4 | `vix_regime` | regime | Ordinal {0, 1, 2} from `VIXCLS` using the pinned M1 thresholds (low < 15, mid, high > 25) |
| 5 | `curve_inverted` | regime | Binary: `yield_curve < 0` |
| 6 | `vol_regime_ratio` | regime | `vol_21d / vol_63d` — vol expansion (> 1) vs contraction (< 1); makes the glossary-documented implicit ratio explicit |
| 7 | `trend_regime` | regime | Binary: `ma200_ratio > 1` — the glossary's "primary regime filter," given to the model as an explicit conditioning bit |

Cross-sectional ranks use **only same-date values** of features that are
themselves point-in-time — no temporal leakage is possible by construction; the
risk is *survivorship/membership* (rank over whoever has data at *t* under the
union-of-indices panel), handled below. HMM-derived regimes stay **out** per
the PRD risk table (adopt only if hand-coded regimes provably fail).

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Feature construction | `src/quant/features/engineering.py:48-72` (`_compute_price_features`) | Dict-of-Series → DataFrame; derived features computed post-merge (`yield_curve` at `engineering.py:144-147`) |
| Panel-wide post-pass | `src/quant/features/engineering.py:194-251` (`build_features`) | `{symbol: DataFrame}` contract; validation prelude raising `ValueError` on missing symbols |
| Ablation orchestration | `src/quant/backtest/ablation.py:32-103` (`run_label_ablation`) | Name→variant mapping; `copy.deepcopy(model)` per run; identical kwargs forwarded verbatim; `ValueError` on inconsistent inputs |
| Pinned regime thresholds | `src/quant/backtest/regimes.py` (`VIXThresholdDetector(low=15, high=25)`) | Import the pinned constants — do not duplicate the numbers in `engineering.py` |
| Reporters | `src/quant/backtest/report.py` (`ablation_summary_table`, `ablation_composite_ranking`, `format_ablation_report`) | DataFrame table + Borda ranking + `format_*_report` via `io.StringIO`; `_fmt()` for NaN |
| Tests | `tests/test_ablation.py`, `tests/test_features.py` | Class-grouped pytest, synthetic panels, deterministic seeds, kwargs-discipline assertions |
| Notebook | `notebooks/06_phase4a_label_ablation.ipynb` | Synthetic demo → slice ablation → per-regime tables → composite ranking → verdict |

## Files to Change

| File | Action | Why |
|---|---|---|
| `src/quant/features/cross_sectional.py` | CREATE | `add_cross_sectional_features(features_by_symbol, columns, min_symbols=5) -> dict[str, pd.DataFrame]` — panel-wide post-pass computing same-date percentile ranks; NaN where fewer than `min_symbols` symbols have data |
| `src/quant/features/engineering.py` | UPDATE | Add the four regime-indicator columns (`vix_regime`, `curve_inverted`, `vol_regime_ratio`, `trend_regime`) computed post-FRED-merge; import VIX thresholds from `regimes.py`; bump docstring feature inventory |
| `src/quant/backtest/ablation.py` | UPDATE | Add `run_feature_ablation(feature_sets: dict[str, list[str]], model, features_by_symbol, labels_by_symbol, prices_by_symbol, ...) -> dict[str, BacktestResult]` — generic named-column-subset ablation mirroring `run_label_ablation`'s discipline. Plus helpers `make_add_one_sets(baseline_cols, candidates)` and `make_leave_one_out_sets(cols)` |
| `src/quant/backtest/report.py` | UPDATE | `feature_ablation_table(results, baseline_name, regime_labels)` — per-regime Sharpe **delta vs baseline** per feature set; `feature_ablation_gate(results, baseline_name, regime_labels, min_lift=0.1, min_features=3, noise_guard=True)` returning the PRD-metric verdict dict with the noise guard; `format_feature_ablation_report(...)` |
| `src/quant/backtest/statistics.py` | UPDATE | Add `bootstrap_sharpe_delta_ci(...)` — paired stationary block bootstrap on the Sharpe delta (noise guard input for the feature gate; reuses T1's 21-day-block convention) |
| `tests/test_statistics.py` | UPDATE | Bootstrap CI tests: identical series → CI centered at 0; injected known edge → CI excludes 0; pairing matters (breaking the pairing widens the CI); deterministic under a seeded rng |
| `tests/test_cross_sectional.py` | CREATE | Rank correctness on hand-computable panels; `min_symbols` NaN rule; union-of-indices alignment (symbols entering/leaving); same-date-only invariant |
| `tests/test_features.py` | UPDATE | Regime-indicator columns: threshold boundary cases; `vix_regime` uses imported (not re-typed) thresholds; NaN propagation from missing FRED |
| `tests/test_ablation.py` | UPDATE | `run_feature_ablation`: column subsets actually differ per run; kwargs identical; deepcopy isolation; helpers produce expected set structures; gate function verdict on synthetic results |
| `notebooks/08_phase4a_feature_ablation.ipynb` | CREATE | Walk-through: feature demos → add-one ablation on the slice → per-regime lift table → PRD-metric gate → SHAP-vs-ablation agreement → verdict |
| `docs/concepts/feature-glossary.md` | UPDATE | One entry per new feature (definition, rationale, lookback, point-in-time rule), marked *(Phase 4A)*; "future candidates" parking-lot section |
| `CLAUDE.md` | UPDATE | Milestone 3 status + delivery summary |

## Tasks

### Task 1: `cross_sectional.py` — panel-wide rank features

- **Action**: Implement `add_cross_sectional_features(features_by_symbol,
  columns=("ret_21d", "ret_252d", "vol_21d"), min_symbols=5)`. Algorithm:
  1. For each source column, assemble a wide frame (index = union of all
     symbols' dates, columns = symbols) from the per-symbol feature frames.
  2. `wide.rank(axis=1, pct=True)` — percentile rank across symbols at each
     date, NaN-aware (symbols without data at *t* are excluded from that
     date's rank pool).
  3. Where the count of non-NaN symbols at *t* is `< min_symbols`, set the
     whole row to NaN — a rank over 2 symbols is noise, and the slice
     notebooks run 5-symbol panels.
  4. Join each rank column back to each symbol's frame as
     `xs_rank_<source_col>`; return a **new** dict (no mutation of inputs).
  Validation prelude mirrors `build_features`: empty dict raises; requested
  source columns missing from any frame raises with the offending symbols
  named.
- **Mirror**: `build_features` contract shape; `_compute_price_features` naming.
- **Validate**: `.venv/bin/pytest tests/test_cross_sectional.py -v` — 3-symbol hand-computable panel produces exact expected ranks; a symbol with no data at *t* is excluded from that date's pool; `min_symbols` rule produces NaN rows; inputs verifiably unmutated.

### Task 2: Regime-indicator columns in `engineering.py`

- **Action**: After the FRED merge in `build_features` (post
  `_attach_fred_features`), add:
  - `vix_regime`: 0 where `VIXCLS < low`, 2 where `VIXCLS > high`, else 1 —
    **importing** `low`/`high` defaults from `quant.backtest.regimes`
    (single source of truth for the pinned 15/25 thresholds). NaN VIXCLS → NaN.
  - `curve_inverted`: `(yield_curve < 0)` as float 0/1; NaN-propagating.
  - `vol_regime_ratio`: `vol_21d / vol_63d`; guard `vol_63d == 0` → NaN (not inf).
  - `trend_regime`: `(ma200_ratio > 1)` as float 0/1; NaN-propagating.
  These are *features the model sees*; the M1 detectors remain the *evaluation*
  axis — same numbers, different consumers, deliberately linked by import.
- **Mirror**: derived-column pattern of `yield_curve` at `engineering.py:144-147`.
- **Validate**: `.venv/bin/pytest tests/test_features.py -v -k "regime"` — boundary values (VIX exactly 15/25) classified per the detector's convention (assert equality with `VIXThresholdDetector` output on the same series); NaN propagation correct; column count = 17 + 4 (+3 sentiment when `sentiment_df` passed).

### Task 3: `run_feature_ablation` orchestrator + set helpers

- **Action**: Implement in `backtest/ablation.py`:
  ```python
  def run_feature_ablation(
      feature_sets: dict[str, list[str]],   # {"baseline": [...17 cols], "+xs_rank_ret_21d": [...18 cols], ...}
      model, features_by_symbol, labels_by_symbol, prices_by_symbol,
      train_window=504, test_window=63, step=63, embargo=3, label_horizon=..., **sim_kwargs,
  ) -> dict[str, BacktestResult]
  ```
  Per set: slice every symbol's frame to that set's columns (raise `ValueError`
  naming missing columns), `copy.deepcopy(model)`, forward all other kwargs
  verbatim to `run_portfolio_backtest`. Unlike `run_label_ablation`, labels are
  fixed and caller-supplied — only the feature columns vary. Helpers:
  - `make_add_one_sets(baseline_cols, candidates)` → `{"baseline": base, "+c": base + [c], ...}`
  - `make_leave_one_out_sets(cols)` → `{"all": cols, "-c": cols minus c, ...}`
  Add-one is the primary design (PRD: "run with / without feature, hold rest
  constant"); leave-one-out on the final combined set is a notebook follow-up,
  not a separate orchestrator.
- **Mirror**: `run_label_ablation` (`ablation.py:32-103`) end to end — same deepcopy/kwargs discipline, same return shape, same error style.
- **Validate**: `.venv/bin/pytest tests/test_ablation.py -v -k "feature"` — each run's model received the expected column count (capture via stub model recording `X.shape`); kwargs identical across runs; missing-column raises; helpers produce exactly N+1 sets for N candidates.

### Task 4: Per-feature ablation reporters + PRD-metric gate

- **Action**: In `report.py`:
  - `feature_ablation_table(results, baseline_name, regime_labels)` — rows =
    feature sets, columns = `{aggregate, qe_bull, covid, rate_cycle}`, cells =
    **Sharpe delta vs baseline** (baseline row shows absolute Sharpe). Built on
    `compute_regime_metrics`.
  - `feature_ablation_gate(results, baseline_name, regime_labels, *,
    min_lift=0.1, min_features=3, noise_guard=True)` → `{gate_passed,
    qualifying_features: {name: {regime, lift, ci_low, sign_consistent}},
    ...}` implementing the PRD metric verbatim ("≥ 3 features show ≥ 0.1
    Sharpe lift net of costs in ≥ 1 regime") **plus a noise guard**: the SE
    of an annualized Sharpe over an ~8-year slice is ≈ 1/√8 ≈ 0.35, so a
    raw 0.1 lift is deep inside noise and raw lifts alone select noise
    (winner's curse). A feature qualifies only if, in addition to the lift,
    **either** (a) the paired block-bootstrap 90% CI on the Sharpe delta
    excludes 0 in the qualifying regime, **or** (b) the delta is positive in
    ≥ 2 regime columns (cross-regime sign-consistency). Net-of-costs is
    inherent — `oos_returns` are already post-cost.
  - `bootstrap_sharpe_delta_ci(returns_variant, returns_baseline, *,
    block_len=21, n_boot=1000, ci=0.90, seed=...)` in
    `backtest/statistics.py` — **paired** stationary block bootstrap: align
    both series on their common index, resample 21-trading-day date blocks
    (the T1 block-bootstrap convention from `evaluation-standards.md`),
    keep the pairing intact within each resampled block so the high
    baseline/variant correlation is preserved (paired deltas give a far
    tighter CI than two independent per-arm CIs), compute
    `sharpe(variant) − sharpe(baseline)` per resample, and return the
    percentile interval.
  - `format_feature_ablation_report(...)` — delta table + gate verdict +
    qualifying-feature list, in the established 52-col text format.
- **Mirror**: `ablation_summary_table` / `format_ablation_report` from M2; `phase4a_gate_report`'s verdict-dict shape from `regime_metrics.py:135-227`.
- **Validate**: `.venv/bin/pytest tests/test_ablation.py -v -k "report or gate"` — synthetic results with known Sharpe orderings produce the algebraically expected deltas; gate fires at exactly 3 qualifying features and not at 2; the noise guard rejects a 0.1 lift whose CI straddles 0 with no cross-regime sign-consistency; missing regimes warn rather than crash. Plus `.venv/bin/pytest tests/test_statistics.py -v -k "bootstrap"`.

### Task 5: `notebooks/08_phase4a_feature_ablation.ipynb`

- **Action**: Sections:
  1. Setup; build the slice panel (5 symbols × 8 years, nb06 convention) with
     **corrected FRED joins** (M5 default) + the 7 new candidates.
  2. Feature sanity demos — each new feature on a known stretch (e.g.,
     `curve_inverted` flips during 2022–23; `xs_rank_ret_21d` distribution
     ~uniform across symbols).
  3. Add-one ablation: 8 runs (baseline + 7) under GBM preview (`n_iter=10`),
     `signed_returns` labels.
  4. `feature_ablation_table` + `feature_ablation_gate` — the PRD-metric
     verdict on the slice.
  5. **SHAP-vs-ablation agreement**: train the IS GBM on the combined
     (17 + survivors) set (nb03 pattern), compute SHAP importance ranks, and
     report Spearman ρ between SHAP rank and per-feature OOS ablation lift
     rank. Reported, not gated — the PRD asks that they "agree"; quantify it.
  6. Leave-one-out spot-check on the combined set (cheap robustness pass).
  7. Verdict: which candidates join the canonical feature set for M4
     registration and the M6 full-panel run; which are documented as noise.
- **Mirror**: nb06 structure and markdown style.
- **Validate**: Executes end-to-end via `nbconvert --execute` within timeout; gate verdict cell renders; verdict names an explicit surviving-feature list.

### Task 6: Glossary + CLAUDE.md

- **Action**: Add the 7 entries to `docs/concepts/feature-glossary.md` (marked
  *(Phase 4A)*, with the same definition/rationale format as existing entries
  and the point-in-time rule for cross-sectional ranks). Add a "future
  candidates" parking-lot subsection. Update `CLAUDE.md` Milestone 3 status,
  the codebase map (`cross_sectional.py`), and the feature count.
- **Mirror**: Existing glossary entry format (`feature-glossary.md:61-80` Phase 2.5 entries).
- **Validate**: Visual review; `grep "xs_rank" docs/concepts/feature-glossary.md`.

## Validation

```bash
# Full suite — must stay green:
.venv/bin/pytest tests/ -v

# Coverage on new/changed modules:
.venv/bin/pytest tests/test_cross_sectional.py tests/test_ablation.py tests/test_features.py \
    --cov=src/quant/features/cross_sectional \
    --cov=src/quant/backtest/ablation \
    --cov-report=term-missing

# Lint:
.venv/bin/ruff check src/quant/features/ src/quant/backtest/ tests/

# Notebook (preview-mode GBM; 8 slice runs):
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=3600 notebooks/08_phase4a_feature_ablation.ipynb
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Cross-sectional ranks leak universe membership (a symbol's presence at date *t* reflects later listing decisions) | Medium | Rank only over symbols with actual price data at *t* (union-of-indices already enforces per-symbol history honesty); document the residual survivorship caveat — the Dow 30 universe was chosen in Phase 2.5, which is a known, already-documented bias |
| 8 ablation runs × GBM preview still slow on the slice | Medium | `n_iter=10` preview mode (nb06 convention); full-panel ablation of *survivors only* belongs to M6's compute budget |
| Slice-level lifts don't replicate at full panel | Medium | Treat nb08's gate verdict as provisional; M6 re-evaluates the survivors at full panel. Document this two-stage discipline in the notebook verdict |
| Threshold duplication drift (`vix_regime` 15/25 vs detector 15/25) | Medium | Import the constants from `regimes.py`; test asserts feature output equals detector output on the same series |
| Candidate list grows mid-milestone ("just one more feature") | High | Pre-committed list of 7 in this plan; additions go to the glossary parking lot and a future ablation round |
| Adding columns breaks `MomentumBaseline`'s positional column lookup (`MOM_COL = 5`, defined inline in nb02's baselines cell; models receive numpy arrays at predict time, so name-based lookup isn't available inside the model) | Medium | Decided (2026-06-12): new columns are *appended* after the existing 17, AND a regression test in `tests/test_features.py` pins `list(features.columns).index("mom_21d") == 5`; future notebooks resolve the index dynamically at setup (`MomentumBaseline(mom_col=feature_cols.index("mom_21d"))` — make `MOM_COL` a constructor arg whenever nb02 is next re-executed) |
| Per-regime Sharpe deltas on thin regimes (covid ≈ 500 bars on the slice) are noisy | High | The noise guard (paired bootstrap CI or cross-regime sign-consistency) is part of the gate by default; report `n_bars` alongside every delta (existing reporter convention); the M6 full panel is the confirmatory test |
| Confounding with label changes | Low | Pinned: `signed_returns` only. Vol-scaled interaction is covered by M6's scheme × (final features) matrix |

## Acceptance

- [ ] `add_cross_sectional_features` produces correct, point-in-time, non-mutating rank columns with the `min_symbols` rule
- [ ] Four regime-indicator columns added; thresholds imported from `regimes.py`, verified equal to detector output
- [ ] `run_feature_ablation` + `make_add_one_sets` / `make_leave_one_out_sets` exist with `run_label_ablation`'s isolation discipline, tested
- [ ] `feature_ablation_gate` implements the PRD metric verbatim (≥ 3 features, ≥ 0.1 lift, ≥ 1 regime), with the noise guard (paired bootstrap CI / cross-regime sign-consistency) active by default, tested at the boundary
- [ ] `bootstrap_sharpe_delta_ci` exists in `statistics.py`, paired and seeded, tested
- [ ] Regression test pins `mom_21d` at column index 5 (nb02 `MomentumBaseline.MOM_COL` contract)
- [ ] nb08 executes end-to-end and names an explicit surviving-feature list (possibly empty — a negative finding is a valid outcome)
- [ ] SHAP-vs-ablation Spearman agreement reported
- [ ] Glossary updated with all 7 candidates; CLAUDE.md updated
- [ ] All existing tests pass; new tests cover the new modules
- [ ] Patterns mirrored, not reinvented (per the table above)

---
*Status: DRAFT — awaiting user confirmation before implementation.*
