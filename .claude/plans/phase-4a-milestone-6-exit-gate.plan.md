# Plan: Phase 4A — Milestone 6 (Exit-Gate Report and Go/No-Go for Track A)

**Source PRD**: `.claude/prds/phase-4a-feature-and-label-redesign.prd.md`
**Selected Milestone**: Milestone 6 — Phase 4A exit-gate report and go/no-go for Track A
**Complexity**: Large (mostly compute + writing; little new library code)
**Depends on**: Milestones 1–5 — this is the integration milestone. M1 gate machinery, M2 label schemes, M3 final feature set, M4 catalog write-back, M5 corrected joins.
**Execution order**: **LAST**. Nothing here starts until M3's surviving-feature verdict and M5's corrected joins are landed.

## Summary

Run the definitive full-panel evaluation (33-symbol Dow 30 + ETF universe,
union-of-indices, ~116 folds, corrected FRED joins, final feature set) and
write the Phase 4A exit-gate report. The pre-committed gate, evaluated by
`phase4a_gate_report` exactly as implemented in M1: **GBM Sharpe > ARIMA Sharpe
in ≥ 2 of 3 recent regimes (`qe_bull`, `covid`, `rate_cycle`), with DM p < 0.05
in ≥ 1 of those regimes.** The verdict is binary — "almost passes" means "does
not pass" (PRD risk table) — and produces an explicit go/no-go for Track A
(transformers).

Per user decision (2026-06-12): the GBM runs under **all three label schemes**
(signed_returns, vol_scaled, triple_barrier) — cheap insurance on the M2
verdict, which came from an ARIMA control that cannot express the non-linear
PT/SL structure triple-barrier rewards. To keep the multi-arm design from
becoming selection bias, the gate arm is pre-declared (see below).

## Pre-committed evaluation protocol (anti-selection-bias)

1. **Primary gate arm = GBM + `signed_returns`** — the scheme M2 kept as
   default. The official Phase 4A gate verdict is computed on this arm alone,
   at the standard DM α = 0.05.
2. **Secondary arms = GBM + `vol_scaled`, GBM + `triple_barrier`** — reported
   as the label-scheme-under-GBM finding (the M2 re-test). If a secondary arm
   passes the gate while the primary fails, the report states *both* facts and
   applies a Bonferroni-adjusted significance bar (DM p < 0.05/3) to the
   secondary arm's claim; only a secondary arm clearing the *adjusted* bar can
   flip the go/no-go to "go."
3. **Control = ARIMA(1,0,0)** — one run; ARIMA forecasts returns directly and
   is label-scheme-independent, so a single control serves all three arms.
4. **DM error-unit contract — all DM inputs live in return space.** The
   signed arm's forecast errors are natively in return space. The vol_scaled
   arm's predictions are converted back to return space *before* error
   computation, by multiplying by the same point-in-time vol denominator
   used to scale its labels (invertible and leak-free by construction —
   the denominator is strictly bar-t information). The triple_barrier arm's
   residuals are classification residuals and are **not** commensurable with
   ARIMA's return errors: that arm reports Sharpe only; its DM numbers
   appear in a caveated appendix and can never support a gate claim.
5. **OOS index alignment.** The gate and every cross-arm table are evaluated
   on the **intersection** of the four runs' `oos_returns` indices — the
   schemes have different NaN warmups (e.g., triple_barrier's
   `vol_window + max_horizon` tail), so per-arm indices differ. The
   dropped-bar count per arm is reported next to every table;
   `compute_regime_metrics` raising on mismatched indices is the
   enforcement backstop, not the alignment mechanism.
6. **Sample-weight parity.** Before any run, audit whether López de Prado
   uniqueness weights (`features/weights.py`) are recomputed per label
   scheme along the harness path `run_label_ablation` uses, and record the
   answer in the nb09 config cell. Uniqueness weights derive from
   label-overlap structure, which differs by scheme — if they are
   scheme-dependent but not recomputed, fix that *before* the arms run, or
   the cross-scheme comparison is not apples-to-apples.
7. These rules are fixed *before* any run starts. The report quotes this
   section verbatim.

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Gate machinery | `src/quant/backtest/regime_metrics.py:135-227` (`phase4a_gate_report`) | Already implements the PRD metric verbatim — consume, don't reimplement |
| Scheme orchestration | `src/quant/backtest/ablation.py:32-103` (`run_label_ablation`) | Three GBM arms = `run_label_ablation` with the three schemes |
| Regime tagging | `src/quant/backtest/regimes.py` (`DateRangeDetector` defaults) | Era axis (`qe_bull`/`covid`/`rate_cycle`) is the PRD's gate axis |
| Reporters | `report.py` (`format_regime_report`, `format_ablation_report`, `regime_summary_table`) | All output tables come from existing reporters |
| Full-panel runs | `notebooks/02_phase2_modeling.ipynb` / `04_phase3_sentiment.ipynb` | 33-symbol union panel + full GBM (`n_iter=50`) conventions, 3600s-class timeouts |
| Honest-failure documentation | `CLAUDE.md` Phase 2.5/3 exit-gate paragraphs + `docs/concepts/evaluation-standards.md` | Negative results documented with the same rigor as positive ones; thresholds never retuned post-hoc |
| Report docs | `docs/PHASE_*.md` + `docs/REFACTOR_PORTFOLIO_UNION_INDEX.md` | Top-level docs/ markdown with evidence tables and decision rationale |

## Files to Change

| File | Action | Why |
|---|---|---|
| `notebooks/09_phase4a_exit_gate.ipynb` | CREATE | The evaluation notebook: panel build → 3 GBM arms + ARIMA control → checkpointing → gate report → scheme finding → report inputs |
| `scripts/run_phase4a_arms.py` | CREATE | Headless runner for the 4 full-panel runs with per-arm parquet checkpointing — hours-long compute should not live or die inside a notebook kernel (see Task 2) |
| `docs/PHASE_4A_REPORT.md` | CREATE | The written exit-gate report the PRD requires — verdict, evidence, go/no-go, next steps under each branch |
| `src/quant/features/catalog.yaml` | UPDATE | Write back `ablation_status` / `regime_notes` for features whose full-panel behavior differs from the nb08 slice verdict |
| `.claude/prds/phase-4a-feature-and-label-redesign.prd.md` | UPDATE | Milestone 6 row → complete; PRD status line → closed with verdict |
| `CLAUDE.md` | UPDATE | Final Phase 4A status block: gate outcome, go/no-go, pointer to the report |

## Tasks

### Task 1: Pin the run matrix and freeze inputs

- **Action**: Open nb09 with a frozen-config cell that records, before any
  compute: universe (`settings.equity_universe`, 33 symbols), feature set
  (17 base + M3 survivors, by exact column list from the M4 catalog), label
  schemes (the three M2 registry entries with `LDP_DEFAULT` for
  triple-barrier), walk-forward params (`train_window=504, test_window=63,
  step=63, embargo=3` — the nb02/nb04 conventions), GBM search (`n_iter=50`),
  FRED joins (`FRED_PUBLICATION_LAGS` default — corrected), sentiment column
  inclusion (match Phase 3's "+sentiment" arm convention so results are
  comparable), and the pre-committed protocol section from this plan quoted
  verbatim. The config hash is computed over the **ordered** feature-column
  list (order is contract-relevant — the `mom_21d` positional coupling from
  the M3 plan) plus all walk-forward/sim kwargs; the cell also records the
  sample-weight-parity audit answer (protocol item 6). This cell is the
  report's reproducibility appendix.
- **Mirror**: nb04's config-and-provenance preamble.
- **Validate**: Config cell renders all values; column list equals the M4 catalog's registered set minus retired entries.

### Task 2: Headless runner with checkpointing — `scripts/run_phase4a_arms.py`

- **Action**: A plain script (argparse: `--arm {signed,vol_scaled,triple_barrier,arima}`)
  that builds the panel, executes the corresponding full-panel run, and writes
  per-arm outputs to `data/phase4a/{arm}/`: `oos_returns.parquet`,
  `oos_forecast_errors.parquet`, and a small JSON of aggregate metrics + run
  metadata (git SHA, wall time, config hash). Rationale: each GBM arm is an
  nb02-scale run (~1h+); 3 arms + control serially is a multi-hour job, and a
  kernel death at hour 3 must not lose hours 1–2. nb09 *loads* checkpoints and
  only re-runs missing arms. The GBM arms run via `run_label_ablation` (one
  scheme at a time through the `--arm` flag) so the kwargs-discipline is
  inherited rather than re-implemented. Before the first run, confirm
  `data/phase4a/` is covered by `.gitignore` — checkpoints are research
  artifacts, not committed data.
- **Mirror**: `run_label_ablation` for orchestration; parquet conventions from the lake for the checkpoint writes (but under `data/phase4a/`, not the lake — these are research artifacts, not ingested data).
- **Validate**: `--arm arima` completes quickly and writes the three artifacts; re-invoking detects the existing checkpoint and skips (idempotency); a `--smoke` flag exercises the plumbing on a synthetic mini-panel in minutes (run manually, not in CI, to avoid CI hours).

### Task 3: Execute the four full-panel runs

- **Action**: Run arms in this order — `arima` (fast control first, sanity
  baseline), `signed` (primary gate arm), `vol_scaled`, `triple_barrier` —
  via the headless script in background shells. After each arm lands, run a
  sanity gate before starting the next: OOS span matches the control's
  (~2003→2026 union span), fold count ≈ 116, `oos_returns` non-empty and
  post-cost, ARIMA aggregate Sharpe in the neighborhood of the nb02 re-run
  (+0.434) — a wildly different control number means a setup bug; stop and
  investigate before burning compute-hours on the GBM arms.
- **Mirror**: CLAUDE.md's documented nb02/nb04 runtime expectations (3600s-class per GBM run).
- **Validate**: Four checkpoint directories present; metadata JSONs record the same config hash; sanity gates logged in nb09.

### Task 4: Gate evaluation + scheme finding (nb09 core)

- **Action**:
  1. Align: intersect the four arms' `oos_returns` indices (protocol
     item 5), report dropped bars per arm, and tag the aligned index with
     `DateRangeDetector()` era labels.
  2. Convert the vol_scaled arm's forecast errors to return space per the
     DM error-unit contract (protocol item 4); exclude the triple_barrier
     arm from all gate-relevant DM tables.
  3. **Primary verdict**: `phase4a_gate_report(gbm_signed, arima, labels)` —
     render `per_regime`, `pass_count`, `dm_p_values`, `gate_passed`.
  4. **Secondary arms**: same gate report per arm, plus
     `ablation_summary_table` / `ablation_composite_ranking` /
     `ablation_dm_matrix` across the three GBM arms — the
     label-scheme-under-GBM re-test of M2's ARIMA-control verdict (does
     vol_scaled still win the Borda composite under a tree model? does
     triple_barrier stop coming last?).
  5. Apply the pre-committed Bonferroni rule to any secondary-arm gate claim.
  6. Feature attribution summary: per-regime Sharpe of the final feature set
     vs the 17-feature baseline (one extra GBM arm on the baseline columns
     **only if** M3's slice verdict promoted ≥ 1 feature; otherwise the M3
     nb08 tables are cited directly — pre-commit this conditional now to cap
     compute).
  7. Max-DD caveat cell: the simulator's no-margin-call artifact (the −567%
     class of numbers) is documented wherever drawdown appears, as in nb04.
- **Mirror**: `phase4a_gate_report` consumption pattern from nb05; M2 reporter trio from nb06.
- **Validate**: nb09 executes end-to-end *from checkpoints* (no re-compute) in minutes; gate verdict cell prints an unambiguous `gate_passed: True/False` for the primary arm.

### Task 5: Write `docs/PHASE_4A_REPORT.md`

- **Action**: The PRD's required written report. Sections (pre-committed
  skeleton):
  1. **Verdict** — one paragraph: gate passed/failed, go/no-go for Track A.
  2. **The gate, verbatim** — PRD success metric + the pre-committed protocol
     from this plan, quoted before any numbers appear.
  3. **Evidence** — per-regime GBM-vs-ARIMA table (primary arm), DM p-values,
     n_bars per regime; secondary-arm tables; scheme-under-GBM finding vs the
     M2 ARIMA-control finding.
  4. **What Phase 4A changed** — M5 leakage verdict and any re-statement of
     Phase 2.5/3 numbers; M2 label verdict; M3 surviving features and
     SHAP-agreement result; M4 catalog state.
  5. **Interpretation** — why the result came out as it did, regime by regime,
     with the honest-failure discipline of the Phase 2.5/3 write-ups.
  6. **Decision** —
     - *Go*: Track A (transformers) gets its own PRD; enumerate what it
       inherits (label scheme, feature set, regime harness).
     - *No-go*: per the PRD risk table, the next move is new data sources or a
       fundamentally different label/target framing — **not** Track A;
       enumerate the 2–3 concrete candidate directions the evidence points to.
  7. **Trials registry** — a table counting every comparison run across
     Phase 4A (M2: 3 schemes × {aggregate + 3 era regimes}; M3: 7 features ×
     4 regime columns plus leave-one-out spot-checks; M5: 2-arm A/B; M6:
     3 GBM arms × regimes), with a qualitative deflated-Sharpe discussion:
     given N effective trials, how much of the best arm's observed Sharpe
     plausibly survives deflation? This makes the lesson of the Phase 2.5
     T4 failure (DSR = 0.364) structural — selection effects get *counted*,
     not guessed after the fact.
  8. **Reproducibility appendix** — config hash, git SHAs, checkpoint paths,
     runtimes.
- **Mirror**: `docs/REFACTOR_PORTFOLIO_UNION_INDEX.md` for decision-doc tone; CLAUDE.md exit-gate paragraphs for the honest-negative style.
- **Validate**: Report cross-links resolve (`grep -r 'PHASE_4A_REPORT' docs/ CLAUDE.md`); verdict section contains an explicit go/no-go sentence.

### Task 6: Close out — catalog write-back, PRD, CLAUDE.md

- **Action**: Update `catalog.yaml` `ablation_status`/`regime_notes` where
  full-panel results refine the nb08 slice verdicts (the drift test keeps this
  honest). Mark PRD Milestone 6 complete and set the PRD status line to closed
  with the verdict. Rewrite the `CLAUDE.md` Phase 4A block from "in progress"
  to a final summary mirroring the Phase 2.5/3 closing paragraphs, with the
  go/no-go and report pointer. Session log entry per the project convention.
- **Mirror**: CLAUDE.md phase-closing paragraph style (Phase 2.5 / Phase 3 blocks).
- **Validate**: `.venv/bin/pytest tests/test_catalog.py -v` (write-back keeps the catalog valid); `grep "Phase 4A" CLAUDE.md` shows the final status.

## Validation

```bash
# Library code is unchanged in this milestone except catalog write-back — full suite must stay green:
.venv/bin/pytest tests/ -v

# Headless arms (long-running; run in background, one at a time):
.venv/bin/python scripts/run_phase4a_arms.py --arm arima
.venv/bin/python scripts/run_phase4a_arms.py --arm signed
.venv/bin/python scripts/run_phase4a_arms.py --arm vol_scaled
.venv/bin/python scripts/run_phase4a_arms.py --arm triple_barrier

# Notebook executes from checkpoints (fast):
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=900 notebooks/09_phase4a_exit_gate.ipynb

# Lint:
.venv/bin/ruff check scripts/run_phase4a_arms.py
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Selection bias from running 3 GBM arms and gating on the best | High (if undisciplined) | Pre-declared primary arm (signed_returns) at α=0.05; secondary arms need Bonferroni-adjusted p < 0.05/3 to affect the go/no-go. Protocol section quoted verbatim in the report |
| Multi-hour compute lost to kernel/machine failure | High | Headless script + per-arm parquet checkpoints; nb09 only consumes checkpoints. Arms run serially with sanity gates between them |
| "Almost passes" pressure (e.g., GBM wins 2 regimes but no DM significance) | Medium | Gate is binary and pre-committed (PRD risk table); the report documents near-misses honestly under *Interpretation*, verdict stays "no-go" |
| DM forecast-error units differ across arms (vol_scaled errors in vol-scaled units; triple_barrier residuals are classification residuals) — naive DM vs ARIMA is invalid | High (if undisciplined) | Resolved by protocol item 4: vol_scaled predictions converted back to return space via the point-in-time vol denominator before error computation; triple_barrier reports Sharpe only, DM in caveated appendix, no gate claims. Primary arm unaffected |
| Thin `covid` regime (~2 years of bars) under-powers the DM test | Medium | `regime_dm_test` already returns `None` below `MIN_DM_OBS` and the gate counts "insufficient evidence" as not-significant rather than crashing; report shows n_bars per regime |
| Control drift — ARIMA number differs from nb02's +0.434 re-run because feature-warmup/dropna changes row alignment | Medium | ARIMA uses prices only, but dropna on the feature matrix sets the usable index; the Task 3 sanity gate catches a materially different control before GBM hours are spent |
| Max-DD simulator artifact misread as a strategy property in the report | Medium | Dedicated caveat cell + report footnote, mirroring nb04's handling (−567% class artifacts) |
| Scope creep: "one more arm" (meta-labeling, HMM regimes, extra features) | Medium | Run matrix pinned in Task 1 before compute starts. M2.5 stayed un-triggered; anything new is a follow-up phase finding |

## Acceptance

- [ ] Four full-panel runs complete with checkpoints (3 GBM label-scheme arms + ARIMA control), identical walk-forward/sim kwargs, corrected FRED joins, final feature set
- [ ] Primary gate verdict computed by `phase4a_gate_report` on the pre-declared arm; binary outcome recorded
- [ ] Label-scheme-under-GBM finding reported with the M2 comparison (Borda composite under GBM vs under ARIMA control)
- [ ] Bonferroni rule applied to any secondary-arm gate claim
- [ ] `docs/PHASE_4A_REPORT.md` exists with all eight sections (incl. the trials registry), an explicit go/no-go for Track A, and next steps under the chosen branch
- [ ] DM error-unit contract enforced: vol_scaled errors converted to return space; triple_barrier excluded from gate-relevant DM tables
- [ ] Cross-arm index-intersection policy applied; dropped-bar counts reported per arm
- [ ] Sample-weight parity audited and recorded in the config cell before the arms ran
- [ ] Catalog `ablation_status`/`regime_notes` written back; drift test green
- [ ] PRD closed out; `CLAUDE.md` Phase 4A block finalized; session log written
- [ ] Full test suite green
- [ ] Patterns mirrored, not reinvented (per the table above)

---
*Status: DRAFT — awaiting user confirmation before implementation.*
