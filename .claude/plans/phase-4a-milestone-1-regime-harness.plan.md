# Plan: Phase 4A — Milestone 1 (Rolling-Window + Regime-Conditional Evaluation Harness)

**Source PRD**: `.claude/prds/phase-4a-feature-and-label-redesign.prd.md`
**Selected Milestone**: Milestone 1 — Rolling-window + regime-conditional evaluation harness
**Complexity**: Medium

## Summary

Extend the existing purged walk-forward harness so that every OOS bar carries a point-in-time regime label and every evaluation output (Sharpe, DM test, gate decision) can be sliced and reported per-regime. The current harness aggregates OOS metrics across all 23 years uniformly; this change adds a regime axis without touching purge/embargo logic. Plumbs forecast errors through `BacktestResult` so per-regime DM tests can run downstream.

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Module layout | `src/quant/backtest/statistics.py:1-18` | Module docstring with references; `from __future__ import annotations`; one public dataclass + free functions |
| Result containers | `src/quant/backtest/harness.py:27-35` + `statistics.py:27-42` | `@dataclass(frozen=True)` with `field(default_factory=...)` for optional series; `__str__` for human-readable summary |
| Error style | `src/quant/backtest/walkforward.py:40-45` + `harness.py:70-74` | `warnings.warn(..., stacklevel=2)` for soft issues; `ValueError` with multi-line context-rich message for hard violations; `RuntimeError` for "should-not-happen" invariant breaks |
| Configuration | `src/quant/backtest/walkforward.py:20-39` | Explicit keyword args with sensible defaults at module call site, not in a Settings object — the harness is library-style |
| Tests | `tests/test_statistics.py:14-77` + `tests/test_portfolio_harness.py:1-90` | Class-grouped pytest (`class TestX:`); deterministic seeds via `np.random.default_rng(seed)`; one synthetic-data helper per test module; stub models with `.fit()` / `.predict()` |
| Reports | `src/quant/backtest/report.py:35-76` | `format_report()` returns string via `io.StringIO`; `print_report()` wraps it; `summary_table()` returns DataFrame; `_fmt()` helper centralises NaN/None handling |
| Duck-typing protocols | `src/quant/backtest/harness.py:10-12` (model contract) | Document the contract in the module docstring; use `Protocol` from `typing` only when type-checking matters at boundaries |

## Files to Change

| File | Action | Why |
|---|---|---|
| `src/quant/backtest/regimes.py` | CREATE | New module: `RegimeDetector` Protocol + two concrete detectors on orthogonal axes (`VIXThresholdDetector` for volatility, `DateRangeDetector` for macro eras) + `tag_regimes(dates, detector)` helper |
| `src/quant/backtest/regime_metrics.py` | CREATE | Per-regime aggregation: `compute_regime_metrics(...)`, per-regime DM test wrapper, `phase4a_gate_report(...)` |
| `src/quant/backtest/harness.py` | UPDATE | Add `oos_returns: pd.Series` and `oos_forecast_errors: pd.Series` fields to `BacktestResult` (defaults so existing constructors keep working); populate them in `run_backtest()` and `run_portfolio_backtest()` |
| `src/quant/backtest/report.py` | UPDATE | Add `format_regime_report(result, regime_labels) -> str` and `regime_summary_table(...)` that mirror existing `format_report` / `summary_table` |
| `src/quant/backtest/__init__.py` | UPDATE | Re-export `tag_regimes`, `compute_regime_metrics`, `phase4a_gate_report` so notebooks import from `quant.backtest` (mirrors how `diebold_mariano` is exposed today) |
| `tests/test_regimes.py` | CREATE | Unit tests for `tag_regimes`: VIX-threshold correctness, point-in-time invariant (no future leakage), boundary handling, missing-VIX-bar handling |
| `tests/test_regime_metrics.py` | CREATE | Unit tests for `compute_regime_metrics` and `phase4a_gate_report`: random model → ~0 Sharpe in every regime; perfect-foresight → high Sharpe in every regime; gate report correctly identifies pass/fail counts |
| `tests/test_portfolio_harness.py` | UPDATE | Add tests asserting `BacktestResult.oos_returns` and `oos_forecast_errors` are non-empty after `run_portfolio_backtest()` runs |
| `docs/concepts/regime-evaluation.md` | CREATE | Brief concept doc: regime definitions, VIX thresholds with citations, point-in-time rule, how per-regime gates differ from aggregate gates. Cross-link from PRD Milestone 1 row. |

## Tasks

### Task 1: Extend `BacktestResult` to retain per-bar OOS series

- **Action**: Add two fields to the `BacktestResult` dataclass: `oos_returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))` and `oos_forecast_errors: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))`. Populate `oos_returns` from the already-computed `oos_returns` local in both `run_backtest()` and `run_portfolio_backtest()`. Populate `oos_forecast_errors` by capturing `(label - raw_prediction)` per fold (continuous-forecast models only) and concatenating.
- **Mirror**: `harness.py:27-35` for dataclass field declaration; the existing `oos_equity_parts`/`oos_returns_parts` accumulation pattern at `harness.py:108-156` for the per-fold capture.
- **Validate**: `.venv/bin/pytest tests/test_backtest.py tests/test_portfolio_harness.py -v` — existing 169 tests stay green; new assertions confirm the two series are populated and length-matched.

### Task 2: Build `regimes.py` with `RegimeDetector` protocol and two concrete detectors

- **Action**: Define a `RegimeDetector` `Protocol` with one method `label(dates: pd.DatetimeIndex) -> pd.Series[str]` returning a categorical regime label per date. Ship **two** concrete implementations on orthogonal axes:
  - `VIXThresholdDetector(vix_series, low=15, high=25)` — volatility axis. Maps `vix < low` → `"low_vol"`, `vix > high` → `"high_vol"`, otherwise → `"mid_vol"`.
  - `DateRangeDetector(ranges)` — era axis. Defaults to the three PRD-specified eras: `qe_bull` (2010-01-01 → 2019-12-31), `covid` (2020-01-01 → 2021-12-31), `rate_cycle` (2022-01-01 → present), plus `pre_qe` (anything earlier) as a catch-all so the detector never returns `NaN`. Customisable via the constructor.

  The two axes are intentionally orthogonal — the PRD's success metric is defined on the era axis (`qe_bull`/`covid`/`rate_cycle`), while VIX provides a complementary risk-regime view. A future composite detector can combine them but is out of scope for Milestone 1.

  Provide a top-level `tag_regimes(dates, detector) -> pd.Series` convenience function. **Hard invariant**: both detectors must be point-in-time — `VIXThresholdDetector` indexes `vix_series.loc[date]` (raises on missing); `DateRangeDetector` consults only its date-range table, which is independent of any series being labeled.
- **Mirror**: `statistics.py` module layout for the docstring + dataclass + free-function shape; the protocol-by-duck-typing convention from `harness.py:10-12`.
- **Validate**: `.venv/bin/pytest tests/test_regimes.py -v` — VIX thresholds map correctly; passing a future-dated VIX series and asking for an earlier date returns only the as-of value; missing VIX dates raise `ValueError`; `DateRangeDetector` correctly maps boundary dates (e.g., 2019-12-31 → `qe_bull`, 2020-01-01 → `covid`); both detectors return a `pd.Series[str]` whose length and index exactly match the input.

### Task 3: Build `regime_metrics.py` — per-regime aggregation

- **Action**: Implement `compute_regime_metrics(returns: pd.Series, regime_labels: pd.Series) -> dict[str, dict[str, float]]` that groups `returns` by `regime_labels` and calls `compute_metrics` per group. Implement `regime_dm_test(errors_a, errors_b, regime_labels) -> dict[str, DMResult | None]` that slices both error series by regime and runs `diebold_mariano` per regime (regimes with `n < 4` observations return `None` rather than crashing). Implement `phase4a_gate_report(gbm_result, arima_result, regime_labels, *, regimes_required=("qe_bull", "covid", "rate_cycle"), min_pass=2, dm_alpha=0.05) -> dict` returning `{per_regime: {...}, gate_passed: bool, pass_count: int, dm_p_values: {...}}`.
- **Mirror**: `compute_metrics` from `metrics.py:17-101` for the grouped call; `diebold_mariano` from `statistics.py:45-141` for the DM API shape; `DMResult` dataclass at `statistics.py:27-42` for the per-regime container.
- **Validate**: `.venv/bin/pytest tests/test_regime_metrics.py -v` — random model from `test_portfolio_harness.py` yields `|sharpe| < 0.3` in every regime; perfect-foresight model yields `sharpe > 2.0` in every regime; gate report correctly returns `gate_passed=True` when ≥ 2 regimes have GBM > ARIMA and DM p < 0.05 in ≥ 1.

### Task 4: Plumb regime-aware reporting through `report.py`

- **Action**: Add `regime_summary_table(result, regime_labels) -> pd.DataFrame` (rows = regimes, columns = sharpe / sortino / max_dd / n_bars). Add `format_regime_report(result, regime_labels) -> str` matching the existing 52-column format from `format_report`. Reuse `_fmt()` for NaN handling.
- **Mirror**: `report.py:19-76` end to end — keep formatting specs identical (`.3f` for Sharpe, `.2%` for drawdown, `—` for NaN).
- **Validate**: `.venv/bin/pytest tests/test_backtest.py -v -k "report"` plus a smoke test in `test_regime_metrics.py` that calls `format_regime_report()` on a populated `BacktestResult` and asserts a header row + one row per regime in the output.

### Task 5: Write `docs/concepts/regime-evaluation.md`

- **Action**: Document the three default macro regimes the PRD references (e.g., 2010–2019 QE bull, 2020–2021 COVID, 2022–2026 rate cycle), the VIX threshold defaults and why they were chosen, the point-in-time rule, and a worked example showing per-regime Sharpe / DM output. Cross-link from `docs/concepts/evaluation-standards.md` and link this doc back from PRD Milestone 1.
- **Mirror**: Tone and structure of `docs/concepts/evaluation-standards.md:1-30` — "why thresholds exist before implementation," referenced citations, explicit "do not tune to make a model pass."
- **Validate**: Doc renders cleanly; no broken intra-doc links (`grep -r 'regime-evaluation' docs/`).

### Task 6: Update `CLAUDE.md` Phase 4A status block

- **Action**: Add a Phase 4A row to the project-status table in `CLAUDE.md`; cross-link the PRD path; note Milestone 1 as in-progress with the plan path. **Do not** describe implementation status until the work is actually done — only the framing.
- **Mirror**: The existing `CLAUDE.md` phase rows (Phase 0 → Phase 3).
- **Validate**: Visual review only; `grep "Phase 4A" CLAUDE.md` returns the new entry.

## Validation

```bash
# Full test suite (must stay green; new tests bring the count up):
.venv/bin/pytest tests/ -v

# Coverage spot-check on the new modules:
.venv/bin/pytest tests/test_regimes.py tests/test_regime_metrics.py \
    --cov=src/quant/backtest/regimes \
    --cov=src/quant/backtest/regime_metrics \
    --cov-report=term-missing

# Lint + format:
.venv/bin/ruff check src/quant/backtest/ tests/test_regimes.py tests/test_regime_metrics.py
.venv/bin/ruff format --check src/quant/backtest/ tests/

# Harness self-test (the only test that catches a silently-broken harness):
.venv/bin/pytest tests/test_portfolio_harness.py -v

# Smoke test from a notebook context (manual; copy into a scratch cell):
#   from quant.backtest import tag_regimes, compute_regime_metrics, phase4a_gate_report
#   ... build features/labels/prices ...
#   result = run_portfolio_backtest(...)
#   labels = tag_regimes(result.oos_returns.index, VIXThresholdDetector(vix_series))
#   per_regime = compute_regime_metrics(result.oos_returns, labels)
#   print(per_regime)
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Adding fields to frozen `BacktestResult` breaks existing positional constructors | Medium | Use `field(default_factory=...)` so new fields are optional; grep for `BacktestResult(` to find any positional callers and convert to keyword args in the same commit |
| Point-in-time violation in `VIXThresholdDetector` (using future VIX to label past dates) | High if not tested | Mandatory test: build a VIX series whose values *change* over time, ask the detector to label an early date, assert it uses only the early value. Reject any implementation that reads `vix_series` outside `.loc[date]` |
| Forecast-error definition differs by model (Ridge returns continuous, ARIMA returns continuous, GBM returns signal) | Medium | Define forecast error as `actual_return - raw_prediction` for continuous-forecast models. For signal models, leave `oos_forecast_errors` empty — DM tests require continuous forecasts and shouldn't run on `{-1, 0, +1}` signals. Document this contract in the dataclass docstring |
| Regime boundaries chosen post-hoc (p-hacking risk) | Medium | Pin VIX thresholds (`low=15`, `high=25`) in code with a citation to the long-run VIX distribution; require updates to the threshold to come with a Git commit explaining the new justification, mirroring the "do not tune thresholds to make a model pass" rule from `evaluation-standards.md` |
| Per-regime sample sizes too small for DM test (n < 4) | High in short regimes | Return `None` for affected regimes instead of crashing; gate report counts those as "insufficient evidence" rather than "fail." Log via `warnings.warn` so the researcher sees it |
| Notebook 04 fails because `BacktestResult` shape changed | Low | New fields are additive with defaults; existing attribute access (`result.oos_metrics`, etc.) stays identical. Run nb04 head-only smoke after Task 1 to confirm |
| Scope creep into Milestone 2/3 work (regime-aware *features*, not just *labels*) | Medium | This plan is the *evaluation harness* only. Adding `VIX_regime` as a feature column belongs to Milestone 3 — keep it out of `regimes.py`. The detector is consumed by metrics, not by `features/engineering.py` |

## Acceptance

- [ ] `BacktestResult` exposes `oos_returns` and `oos_forecast_errors` series; all 263 existing tests still pass
- [ ] `tag_regimes(dates, VIXThresholdDetector(vix))` returns a `pd.Series[str]` aligned with `dates`, with no future leakage
- [ ] `compute_regime_metrics(returns, labels)` returns one full metric dict per regime
- [ ] `phase4a_gate_report(gbm, arima, labels)` returns `{gate_passed, pass_count, per_regime, dm_p_values}` and the gate semantics exactly match the PRD success metric ("GBM > ARIMA in ≥ 2 of 3 recent regimes, DM p < 0.05 in ≥ 1")
- [ ] `format_regime_report(result, labels)` prints a per-regime summary using the same formatting conventions as `format_report`
- [ ] `docs/concepts/regime-evaluation.md` exists and is cross-linked from `evaluation-standards.md` and the PRD
- [ ] `CLAUDE.md` lists Phase 4A with milestone 1 in-progress
- [ ] Harness self-tests still pass — random strategy → ~0 Sharpe in every regime; perfect-foresight → high Sharpe in every regime
- [ ] Patterns mirrored, not reinvented (per the table above)

---
*Status: DRAFT — awaiting user confirmation before implementation.*
