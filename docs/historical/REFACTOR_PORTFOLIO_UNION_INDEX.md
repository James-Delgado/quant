# Refactor — portfolio backtest on union index, not intersection

## Goal

Stop `run_portfolio_backtest` from throwing away years of data when one symbol
in the panel has a late start. Each symbol should contribute whatever history
it has; the model fit should pool whatever symbols are alive in each training
window; the portfolio P&L should average over whatever symbols are alive in
each test window.

Concretely, on the current 33-symbol Dow 30 + ETF universe, the backtest must
extend from ~2002-06 (the first usable feature row after Tiingo's 2001-06
start + the 252-day MA200 warmup + walk-forward warmup) to 2026-04, instead
of 2010-01 to 2026-04. ~8 extra years of training data.

## Non-goals

- Do not change the model architecture (still XGBoost + RandomizedSearchCV per
  fold, still cross-sectional pooled training).
- Do not change the label definition or purge/embargo discipline.
- Do not change `run_backtest()` (single-symbol path).
- Do not change `evaluate_panel()` semantics (it uses a single shared index
  and that's appropriate for its purpose — per-model comparison on a fixed
  panel).
- No new features in the feature set. Universe expansion only.

## Current behavior — what breaks the long history

`src/quant/backtest/harness.py:224-234`:

```python
common_idx = features_by_symbol[symbols[0]].index
for sym in symbols[1:]:
    common_idx = common_idx.intersection(features_by_symbol[sym].index)
# ...
feat = {s: features_by_symbol[s].loc[common_idx] for s in symbols}
labs = {s: labels_by_symbol[s].loc[common_idx] for s in symbols}
pric = {s: prices_by_symbol[s].loc[common_idx] for s in symbols}
```

Every symbol is sliced to the intersection of all symbols' indices. V (Visa,
IPO 2008-03-19) plus 252-day warmup plus 255-bar walk-forward warmup is the
binding constraint that pushes the first OOS prediction to 2010-01-04.

Downstream, `harness.py:294-305`:

```python
if len(fold_sym_oos_returns) > 1:
    ref_idx = fold_sym_oos_returns[0].index
    for ret_s in fold_sym_oos_returns[1:]:
        if not ret_s.index.equals(ref_idx):
            raise RuntimeError(...)

fold_ret = pd.concat(fold_sym_oos_returns, axis=1).mean(axis=1, skipna=False)
```

The alignment guard explicitly requires every symbol's OOS return series to
have identical indices, and `skipna=False` means any NaN in any column
poisons the portfolio average. Both assume strict alignment.

## Proposed change — high level

1. **Master timeline = union** of all symbols' feature indices, sorted, deduplicated.
2. **Per-fold training pool = sparse stack**: for each symbol, take rows in
   `train_pos` that the symbol has features for; concatenate vertically. A
   symbol with no rows in this fold contributes nothing.
3. **Per-fold prediction = per-symbol slice**: only predict for symbols that
   have at least one feature row in `test_pos`.
4. **Per-bar portfolio return = mean across active symbols**: each symbol's
   return series is sparse-indexed to its actual bars; concat with `axis=1`
   yields a sparse matrix; `mean(axis=1, skipna=True)` averages over symbols
   that were live at that bar.

When all symbols happen to share the same index (the current state, plus all
existing tests and `evaluate_panel`), this reduces to identical behavior.
The refactor is a **strict superset** — same answer for the same input.

## Specific changes to `harness.py`

### 1. Replace intersection with union (lines 224-234)

```python
# Master timeline: union of all symbols' feature indices.
master_idx = features_by_symbol[symbols[0]].index
for sym in symbols[1:]:
    master_idx = master_idx.union(features_by_symbol[sym].index)
master_idx = master_idx.sort_values().unique()
if len(master_idx) == 0:
    raise ValueError("No bars across any symbol")

# Keep references unsliced — per-fold logic indexes positionally into master_idx
# then filters per symbol by membership.
feat = features_by_symbol
labs = labels_by_symbol
pric = prices_by_symbol
```

The `walkforward_splits(len(master_idx), ...)` call uses the union length, so
splits are denominated in **master-calendar bars**. A 200-bar train_window
now means "200 calendar bars where ANY symbol was live."

### 2. Build a per-symbol "alive" mask once (new code, after master_idx)

```python
# Boolean mask per symbol: True where the symbol has a feature row at that master-bar.
alive: dict[str, np.ndarray] = {
    s: master_idx.isin(features_by_symbol[s].index) for s in symbols
}
```

This lets each fold ask "which symbols are alive in `train_pos` / `test_pos`"
in O(1) lookup per (symbol, fold).

### 3. Sparse stacking inside the fold loop (replaces lines 274-277)

```python
X_train_parts: list[np.ndarray] = []
y_train_parts: list[np.ndarray] = []
for s in symbols:
    train_mask = alive[s][train_pos]
    if not train_mask.any():
        continue
    sym_train_idx = master_idx[train_pos][train_mask]
    X_train_parts.append(features_by_symbol[s].loc[sym_train_idx].to_numpy())
    y_train_parts.append(labels_by_symbol[s].loc[sym_train_idx].to_numpy())

if not X_train_parts:
    continue  # no symbol has data this fold; skip

X_train = np.vstack(X_train_parts)
y_train = np.concatenate(y_train_parts)
model.fit(X_train, y_train)
```

The pooled training set is now `Σ_s (rows where symbol s is alive in train_pos)`.
A fold in 2002 might have 5 symbols × 200 bars = 1,000 rows; a fold in 2020
might have 33 × 200 = 6,600 rows. The model sees what it sees; no synthetic
gap-filling.

### 4. Per-symbol prediction with alive filter (replaces lines 282-301)

```python
test_master_idx = master_idx[test_pos]
fold_sym_oos_returns: list[pd.Series] = []

for s in symbols:
    test_mask = alive[s][test_pos]
    if not test_mask.any():
        continue
    sym_test_idx = test_master_idx[test_mask]

    X_test = features_by_symbol[s].loc[sym_test_idx].to_numpy()
    raw_pred = np.asarray(model.predict(X_test), dtype=float)
    signals = pd.Series(np.sign(raw_pred).astype(int), index=sym_test_idx)

    sym_prices = prices_by_symbol[s].loc[sym_test_idx]
    eq, tlog = simulate(sym_prices, signals, **sim_kwargs)
    fold_sym_oos_returns.append(eq.pct_change().dropna())
    if not tlog.empty:
        oos_trade_parts.append(tlog)

if not fold_sym_oos_returns:
    continue  # no active symbols this fold

# Sparse cross-section: each column has its own (subset) index.
# axis=1 outer-aligns; skipna=True averages over symbols active at each bar.
fold_ret = pd.concat(fold_sym_oos_returns, axis=1).mean(axis=1, skipna=True)
fold_metrics.append(compute_metrics(fold_ret))
oos_returns_parts.append(fold_ret)
```

### 5. Replace the alignment guard with an active-count diagnostic (replaces lines 293-301)

The old guard required every symbol's index to equal the reference. Drop it.
In its place, optionally record breadth per fold:

```python
fold_metrics[-1]["n_symbols_active"] = len(fold_sym_oos_returns)
fold_metrics[-1]["n_train_rows"] = len(y_train)
```

These are diagnostic-only and don't affect existing metric keys.

### 6. No change downstream

The concatenation at line 315, equity curve at 319, trade log at 320, and
`compute_metrics` call at 323 all consume `oos_returns_parts` and
`oos_trade_parts` unchanged. The only difference is that the returns
underlying each fold are now a **breadth-weighted equal-weight mean** rather
than a strict cross-section.

## Invariants preserved (the four leakage guarantees)

All six invariants from `src/quant/backtest/CLAUDE.md` survive:

1. **Purge.** Still applied in `walkforward_splits` on the master calendar.
   Per-symbol filtering is positional within `train_pos` / `test_pos`, so a
   symbol's contributions remain inside the purged train window.
2. **Embargo.** Same — operates on master calendar, applies uniformly.
3. **Embargo length.** Unchanged constant.
4. **Test fold length much greater than `label_horizon + embargo`.** Unchanged.
5. **Production-refit path.** Untouched.
6. **Hyperparameter tuning inside each training window.** XGBoost still gets
   pooled training data per fold; `RandomizedSearchCV` runs inside that fit.
   Nothing crosses the boundary.

A subtle point worth restating: because purge/embargo are denominated in
**master-calendar bars**, they may over-purge for a symbol that's only alive
on a sparse subset of those bars. That's the safe direction. The other
direction — under-purging — is what silently inflates Sharpe.

## New invariants to add

1. **Per-symbol indices must be a subset of `master_idx`.** Enforce via a
   one-time check after building `master_idx` — every `features_by_symbol[s].index`
   must be contained in `master_idx`. Should always be true by construction
   (master is the union) but a defensive check catches DataFrame-mutation bugs.
2. **`fold_ret` must be non-empty when `fold_sym_oos_returns` is non-empty.**
   `concat(...).mean(axis=1, skipna=True)` returns NaN only at bars where
   every active symbol's return is NaN — that's the bug case (symbol claimed
   to be alive but simulate produced NaN). Add an explicit `assert not
   fold_ret.isna().all()` and surface a clear error.
3. **At least one symbol must contribute to at least one fold.** If
   `oos_returns_parts` is empty after the loop, raise rather than returning
   silently with empty metrics — the only way to hit this is a config error
   (no symbol has any test data anywhere).

## Tests

### Existing tests that must still pass

- `tests/test_backtest.py` — harness self-tests (random/no-skill ≈ 0 edge;
  intentionally-leaky must be caught). These use single-symbol or
  fully-aligned panels and should produce **bit-identical** results because
  the refactor is a strict superset.
- `tests/test_phase2.py` (or wherever the existing portfolio tests live) —
  same expectation.

### New tests for the union behavior

1. **`test_portfolio_handles_late_starting_symbol`**: build a 2-symbol panel
   where symbol A has 500 bars and symbol B has only the last 300. Assert:
   (a) the backtest runs without error; (b) early folds train on A-only;
   (c) later folds train on both; (d) `n_symbols_active` in fold_metrics
   reflects the transition.
2. **`test_portfolio_identical_to_old_on_aligned_panel`**: build a panel
   where all symbols share an index. The new harness output must equal the
   old behavior's output to floating-point tolerance.
3. **`test_portfolio_skips_fold_with_no_active_symbols`**: edge case — a
   gap window where no symbol has training data. Fold is skipped, run
   continues.
4. **`test_portfolio_breadth_weighting_is_equal_weight`**: in a fold with
   3 active symbols, the per-bar portfolio return must equal the mean of
   the 3 per-symbol returns (no symbol gets extra weight from being alive
   longer in the test window).

## Migration / rollback

- **Single-commit refactor.** Old code path is fully replaced; no feature
  flag. The behavior on aligned panels is identical, so existing notebooks
  and downstream code see no diff.
- **Rollback** is `git revert`. The output `BacktestResult` shape is
  unchanged — no schema migration on saved results.

## Expected effect on Phase 3 result

If the diagnosis is right and 2010-01 was V's binding constraint:

- **Pre-refactor** (33 sym, 2010-2026): Control Sharpe 0.034, Treatment −0.073, MaxDD −54.
- **Post-refactor** (33 sym, ~2002-2026): more training data, more breadth in
  early folds.

I will **not predict the direction** of the Sharpe change. The 2008-09
financial crisis sits squarely in the new training/OOS region and is a
genuinely harder regime than the post-2010 era. The honest expectation is:
metrics will move; whether they move favorably is the empirical question
this refactor exists to answer. If Sharpe gets worse, that's a real result
(the 0.034 was partly a survivorship-bias artifact of starting in 2010).
If Sharpe gets better, the original feature set was being denied data it
could have used.

Either outcome is more informative than the current "we don't even know"
state.

## Open questions for the user before I implement

1. **Drop the alignment-guard test if any exists**, or keep it as a
   regression check on a fully-aligned-panel fixture? Recommendation: keep
   the test, but assert on a controlled aligned fixture instead of on the
   live universe.
2. **`fold_metrics` schema change** — adding `n_symbols_active` and
   `n_train_rows` keys. Anything downstream consume `fold_metrics` and
   break on extra keys? The notebook reads it but only by name, so adding
   keys is safe.
3. **Per-fold breadth chart in the notebook** — worth adding to Section 5
   as a diagnostic so you can see breadth grow over time? Cheap to add.
4. **`evaluate_panel` parity** — leave as-is (still intersection), or
   port the same union logic there? It serves a different purpose
   (per-model comparison on a fixed panel), so I'd recommend leaving it
   alone unless you have a reason.

## Implementation order

1. Branch: stay on `phase-3-sentiment`.
2. Add the four new tests **first**, marked `@pytest.mark.xfail` to verify
   they fail on the current code (TDD discipline per project rules).
3. Apply the harness changes.
4. Flip `xfail` → expected pass; full test suite green.
5. Re-execute `notebooks/04_phase3_sentiment.ipynb` end-to-end. Compare
   Period line, fold count, OOS Sharpe.
6. Update Section 9 conclusion to discuss the new result.
7. Commit. PR description references this doc.

ETA: a focused 90-min session, most of which is waiting for two full
walk-forward runs in the notebook.
