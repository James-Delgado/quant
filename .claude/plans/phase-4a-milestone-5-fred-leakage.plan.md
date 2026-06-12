# Plan: Phase 4A — Milestone 5 (FRED ASOF-Join Leakage Investigation)

**Source PRD**: `.claude/prds/phase-4a-feature-and-label-redesign.prd.md`
**Selected Milestone**: Milestone 5 — FRED ASOF-join leakage investigation
**Complexity**: Medium
**Depends on**: Milestone 1 (regime metrics, for per-regime impact slicing)
**Execution order**: **FIRST** of the remaining milestones (before M3) — a confirmed
leak would contaminate M3's per-feature ablation baselines, where macro features
are the SHAP-dominant family. Re-sequencing decision confirmed by user 2026-06-12.

## Summary

Determine whether the macro features' in-sample SHAP dominance (DFF, yield_curve,
DGS10, VIXCLS) is real signal or a look-ahead artifact of the FRED join. Planning
audit already found one concrete discrepancy: the `engineering.py` module
docstring (lines 6–8) claims the join attaches "the most recent FRED observation
whose **ingested_at** <= bar_date", but `_attach_fred_features` actually merges on
the **observation date** (`timestamp`). DFF for day *t* is published by the NY Fed
the *next* business day, so the model may see macro values before they were
knowable at decision time (close of bar *t*). The milestone (a) pins down actual
publication timing per series, (b) implements a per-series publication-lag shift,
(c) measures the performance impact on a fast slice, and (d) issues a verdict on
whether Phase 2.5 / Phase 3 results require re-statement.

**Pre-committed decision rule (anti-p-hacking):** the lagged join becomes the
default on *correctness* grounds, regardless of whether it helps or hurts
performance. The A/B experiment exists to quantify the re-statement, not to
decide whether to adopt the fix. Same discipline as the pinned VIX thresholds
and `TripleBarrierConfig` defaults.

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Join implementation | `src/quant/features/engineering.py:75-149` (`_attach_fred_features`) | pandas `merge_asof(direction="backward")` on a tz-naive date key; `warnings.warn` on NaN coverage gaps |
| Module constants | `src/quant/features/engineering.py:37` (`_FRED_SERIES`) | Module-level tuple of approved series; pinned, documented in the docstring |
| Pre-committed parameters | `src/quant/features/label_schemes.py` (`LDP_DEFAULT`) + `regimes.py` VIX thresholds | Frozen defaults pinned in code *before* any model run; rationale lives in `docs/concepts/`, not code comments |
| Concept docs | `docs/concepts/regime-evaluation.md`, `docs/concepts/label-schemes.md` | "Why before implementation" framing, citations, explicit update protocol ("do not retune to make a model pass") |
| Tests | `tests/test_features.py` | Synthetic FRED frames + synthetic OHLCV; assert exact join values per bar; class-grouped pytest with AAA |
| Notebook structure | `notebooks/05_phase4a_regime_harness.ipynb` / `06_...` | Synthetic demo → real-data slice → per-regime comparison → verdict section |
| Per-regime impact | `src/quant/backtest/regime_metrics.py:49-73` (`compute_regime_metrics`) | Slice the A/B Sharpe delta by `DateRangeDetector` era labels |

## Files to Change

| File | Action | Why |
|---|---|---|
| `src/quant/features/engineering.py` | UPDATE | (1) Fix the false `ingested_at` docstring claim. (2) Add `FRED_PUBLICATION_LAGS: dict[str, int]` (business days) module constant. (3) Apply per-series forward shift of FRED observation dates inside `_attach_fred_features` *before* the ffill + asof merge. (4) Thread `fred_publication_lags: Mapping[str, int] | None` through `build_features` — `None` reproduces legacy (unlagged) behavior for the A/B experiment; the pinned dict is the new default |
| `tests/test_features.py` | UPDATE | New test class `TestFredPublicationLags`: synthetic FRED series with distinguishable per-day values; assert bar *t* receives obs *t−lag*; assert `None` reproduces legacy join bit-for-bit; assert shift composes correctly with weekend ffill and tz conversion |
| `notebooks/07_phase4a_fred_leakage.ipynb` | CREATE | The investigation notebook: empirical lag verification → macro-only leakage probe → full-feature A/B on a slice → per-regime impact → verdict |
| `docs/concepts/fred-publication-lag.md` | CREATE | Publication-timing table per series with citations, the pinned lag values, the point-in-time decision-time convention (knowable at close *t*), the latest-vintage storage limitation, update protocol |
| `docs/concepts/feature-glossary.md` | UPDATE | Macro feature entries (DGS10, DFF, VIXCLS, yield_curve) get a one-line publication-lag note + cross-link to the new doc |
| `CLAUDE.md` | UPDATE | Milestone 5 status + verdict summary; re-statement note on Phase 2.5/3 numbers if the impact is material |

## Tasks

### Task 1: Audit + empirical publication-lag verification

- **Action**: Document the actual join path end-to-end (ingest → `_load_fred_wide`
  pivot/ffill → `_attach_fred_features` asof merge) in
  `docs/concepts/fred-publication-lag.md`. Then *empirically verify* publication
  timing for the three series using ALFRED vintage metadata (`fredapi`'s
  `get_series_first_release` / `realtime_start` fields): for a sample of ~20
  recent observation dates per series, record `realtime_start − observation_date`
  in business days. Pin the lag table from the evidence. Expected (to be
  confirmed, not assumed):
  - `DFF` — EFFR published next business day ~9am ET → **lag = 1**
  - `DGS10` — Treasury yield-curve data published after the close; FRED vintage often next morning → **lag = 1** (conservative)
  - `VIXCLS` — Cboe close published ~4:15pm ET same day; FRED vintage may be next day → **lag = 1** (conservative)

  Also document the **decision-time convention**: features at bar *t* must be
  knowable at the close of bar *t* (when the signal is formed), even though
  fills happen at the next open. A value published at 4:15pm ET on day *t* is
  *not* knowable at the 4:00pm close — lag 1 is correct under this convention.

  Also document the **latest-vintage storage limitation**: `to_processed()` in
  `ingest/fred_macro.py:57-61` keeps the latest-ingested value per
  (series, date), so revised values overwrite originals. Acceptable for
  DGS10/DFF/VIXCLS (negligible revision), and the reason CPI/UNRATE stay
  excluded — state this explicitly so it isn't rediscovered later.
- **Mirror**: `docs/concepts/regime-evaluation.md` structure (evidence → pinned values → update protocol).
- **Validate**: Doc exists with the empirical lag table; `grep -r 'fred-publication-lag' docs/` shows cross-links from `feature-glossary.md`.

### Task 2: Implement per-series publication-lag shift in `engineering.py`

- **Action**:
  1. Add module constant pinned from Task 1 evidence:
     ```python
     FRED_PUBLICATION_LAGS: dict[str, int] = {"DGS10": 1, "DFF": 1, "VIXCLS": 1}
     ```
  2. In `_attach_fred_features`, accept `publication_lags: Mapping[str, int] | None`.
     When provided, shift each series column's observation index forward by
     `lag` business days (`pd.offsets.BDay(lag)`) **before** the asof merge.
     Critically, the shift must compose correctly with the weekend `ffill`:
     `_load_fred_wide` ffills the wide frame, so apply the shift per-series on
     the *un-ffilled* values (or shift each column's index and re-ffill) —
     choose the implementation that keeps "bar t sees obs-date ≤ t − lag"
     provable in a test, and document the choice in the function docstring.
  3. Thread `fred_publication_lags=FRED_PUBLICATION_LAGS` as the new default
     through `build_features`; `None` switches to legacy unlagged behavior
     (needed for the A/B arm and for reproducing historical results).
  4. Fix the module docstring: remove the false `ingested_at` claim; describe
     the actual semantics (observation-date join + publication-lag shift).
- **Mirror**: `_attach_fred_features` existing structure; `LDP_DEFAULT` pre-commitment pattern for the pinned dict.
- **Validate**: `.venv/bin/pytest tests/test_features.py -v -k "publication"` — synthetic FRED frame where each day's value encodes its date; with lag=1, the bar on Tuesday receives Monday's value; with `None`, Tuesday receives Tuesday's value; Friday-bar/weekend-ffill case correct; all existing feature tests stay green.

### Task 3: Macro-only leakage probe (nb07 §2)

- **Action**: In the notebook, train the GBM (preview mode, `n_iter=10`) on
  **macro columns only** (`DGS10, DFF, VIXCLS, yield_curve`) over the nb05/nb06
  5-symbol × 8-year slice, in two arms: lagged vs unlagged joins. Compare IS
  hit-rate, OOS Sharpe, and per-regime metrics. **Interpretation rule
  (pre-committed)**: if the unlagged macro-only model shows IS skill that
  materially degrades under the 1-day lag, the IS macro dominance from nb03 is
  at least partly mechanical look-ahead; if both arms are statistically
  indistinguishable, the join timing is not the explanation for the
  IS-dominant/OOS-absent signature and the investigation reports "no leak found
  via publication timing."
- **Mirror**: nb06's slice-loading + preview-mode GBM cells.
- **Validate**: Notebook section executes; both arms produce populated `BacktestResult`s; the comparison table renders.

### Task 4: Full-feature A/B on the slice (nb07 §3–4)

- **Action**: Run the full 17-feature matrix through `run_portfolio_backtest`
  with the GBM (preview `n_iter=10`), lagged vs unlagged, same slice. Report:
  1. OOS Sharpe delta (aggregate + per-regime via `DateRangeDetector` +
     `compute_regime_metrics`)
  2. SHAP top-5 stability between arms (re-using nb03's SHAP cell pattern)
  3. Prediction-level diff: fraction of OOS bars where `sign(pred)` changes
     between arms — the most direct measure of mechanical sensitivity
- **Mirror**: nb05 §6 real-data slice structure; `regime_summary_table` for the per-regime view.
- **Validate**: Notebook executes end-to-end under the timeout (preview mode keeps it fast); all three comparison outputs are non-empty.

### Task 5: Verdict + re-statement (nb07 §5, docs, CLAUDE.md)

- **Action**: Write the verdict section in nb07 and mirror it in
  `fred-publication-lag.md` and `CLAUDE.md`:
  - **Leak confirmed + material** — materiality judged against **pinned
    thresholds (pre-committed here, not chosen after seeing results)**:
    sign-flip fraction > 5% of OOS bars, OR |ΔSharpe| > 0.1 in aggregate or
    in any era regime: lagged join stays default; add an explicit re-statement note
    to the Phase 2.5 / Phase 3 paragraphs in `CLAUDE.md` ("numbers reflect
    unlagged joins; corrected full-panel numbers land in M6"). Do **not**
    re-run nb02/nb04 here — M6's full-panel runs use the corrected joins and
    supersede them.
  - **Leak confirmed + immaterial**: lagged join stays default (correctness);
    note that prior results stand within noise.
  - **No mechanical sensitivity**: lagged join *still* stays default; the
    IS-dominance puzzle is re-attributed (feature instability or label
    misspecification) and handed to M3 as a finding.
- **Mirror**: nb06 §8 "what's next" verdict-section style.
- **Validate**: `grep "Milestone 5" CLAUDE.md` shows the verdict; PRD row updated.

## Validation

```bash
# Full test suite — must stay green:
.venv/bin/pytest tests/ -v

# Targeted:
.venv/bin/pytest tests/test_features.py -v

# Lint:
.venv/bin/ruff check src/quant/features/engineering.py tests/test_features.py

# Notebook (preview-mode GBM keeps this inside the timeout):
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=1800 notebooks/07_phase4a_fred_leakage.ipynb
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| ALFRED vintage metadata unavailable / API quota | Low | `fredapi` exposes release data on the standard key already in `.env`. Fallback: cite the publishers' release schedules (NY Fed EFFR, Treasury H.15, Cboe) and pin lag=1 conservatively for all three |
| Changing the default join silently changes every downstream notebook's numbers | High | That is the point — but make it auditable: the `None` escape hatch reproduces legacy behavior bit-for-bit (tested), and `CLAUDE.md` records which executed notebooks predate the fix. nb02–nb06 are *not* re-executed in this milestone |
| Lag shift interacts wrongly with the weekend ffill (value shifts 1 day, then ffill smears it back) | Medium | Test the exact Friday/Monday cases with synthetic data; assert the invariant "bar t sees obs-date ≤ t − lag" directly on the merged output, not on intermediate frames |
| Slice-level A/B is underpowered to detect a small real effect | Medium | The decision (adopt lag) does not depend on the A/B outcome — only the re-statement language does. The full-panel measurement happens in M6 regardless |
| Investigation scope-creeps into re-running nb02/nb04 full panels (~hours each) | Medium | Explicitly out of scope; M6 supersedes. The milestone ends at the slice-level verdict |
| `ingested_at`-based joining proposed as "more correct" alternative | Low | Rejected here: backfilled history has `ingested_at` = backfill date, which would wipe out all macro features for 20 years of bars. Publication-lag shift on observation dates is the correct point-in-time approximation; document why |

## Acceptance

- [ ] `docs/concepts/fred-publication-lag.md` exists with an *empirically verified* lag table and the decision-time convention
- [ ] `engineering.py` docstring no longer claims `ingested_at` join semantics
- [ ] `FRED_PUBLICATION_LAGS` pinned in code; lagged join is the `build_features` default; `None` reproduces legacy behavior (tested)
- [ ] Macro-only probe + full-feature A/B executed on the slice in `notebooks/07_phase4a_fred_leakage.ipynb` with per-regime impact
- [ ] Written verdict: leak confirmed/refuted; materiality judged against the pinned thresholds (sign-flip fraction > 5% OR |ΔSharpe| > 0.1), with re-statement note in `CLAUDE.md` if needed
- [ ] All existing tests pass; new publication-lag tests added
- [ ] PRD Milestone 5 row updated
- [ ] Patterns mirrored, not reinvented (per the table above)

---
*Status: DRAFT — awaiting user confirmation before implementation.*
