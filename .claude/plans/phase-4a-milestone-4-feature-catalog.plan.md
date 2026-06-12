# Plan: Phase 4A — Milestone 4 (Feature Catalog — Machine-Readable Registry)

**Source PRD**: `.claude/prds/phase-4a-feature-and-label-redesign.prd.md`
**Selected Milestone**: Milestone 4 — Feature catalog (YAML registry)
**Complexity**: Small
**Depends on**: Milestone 3 (the final Phase 4A feature list must be settled so every feature is registered exactly once), Milestone 5 (publication-lag metadata is a catalog field)
**Execution order**: third of the remaining milestones (after M3, before M6).

## Summary

Create a machine-readable registry of every feature `build_features()` (plus
`add_cross_sectional_features()`) produces, with ~12 metadata fields per entry,
and an enforcement test that fails whenever code and catalog drift in either
direction. The PRD success metric is binary: **100% of features registered**.
The catalog is the contract the Phase 5 continuous-agent pair will read and
write; for now its consumers are the researcher and the test suite. Format
decision: **YAML** (human-diffable in PRs, comment-friendly) validated through
**pydantic** models — mirroring how `config.py` already uses pydantic for typed
settings.

**Anti-over-engineering rule (from the PRD risk table, pre-committed):** the
schema ships with the fields listed below and *no* agent-runtime concepts
(ownership, scheduling, prompts, run history). Those belong to Phase 5.

## Catalog schema (pre-committed)

One YAML document, `features:` list, one entry per feature column:

```yaml
features:
  - name: ret_21d                      # exact column name in the feature matrix
    family: price                      # enum: price | volume | macro | macro_derived | sentiment | cross_sectional | regime
    source: alpaca_ohlcv               # enum: alpaca_ohlcv | fred | edgar_finbert | derived
    formula: "close.pct_change(21)"    # one-line expression or function ref
    lookback_bars: 21                  # warmup bars before first valid value (0 if none)
    publication_lag_days: 0            # business days between observation and availability (M5)
    point_in_time_rule: "uses only closes <= t"
    added_phase: "2"                   # project phase that introduced it
    glossary_ref: "docs/concepts/feature-glossary.md#ret_21d"
    ablation_status: untested          # enum: untested | tested_no_edge | tested_edge | retired
    regime_notes: null                 # free text, e.g. "0.13 Sharpe lift in rate_cycle (nb08)"
    depends_on: []                     # other feature names this one is derived from, e.g. yield_curve -> [DGS10, DFF]
```

Twelve fields. `ablation_status` and `regime_notes` are the two mutable fields
the future agent pair (and M3/M6 results) write back; everything else is
descriptive and changes only when the feature definition changes.

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Typed config via pydantic | `src/quant/config.py` | pydantic models with field types + enums; validation errors at load time, not use time |
| Module layout | `src/quant/features/labels.py:1-7` | Top docstring stating the invariant; `from __future__ import annotations`; small public surface |
| Error style | `src/quant/features/engineering.py:223-227` | `ValueError` with the offending names listed, not just a count |
| Enforcement-by-test | `tests/test_portfolio_harness.py` harness self-tests | A test that encodes an invariant ("catalog == code") so drift cannot land silently |
| Tests | `tests/test_features.py` | Synthetic prices + synthetic FRED fixtures already exist for building a real feature matrix offline |
| Docs cross-linking | `docs/concepts/feature-glossary.md` | Glossary remains the prose source of truth; catalog entries link to it via `glossary_ref` |

## Files to Change

| File | Action | Why |
|---|---|---|
| `src/quant/features/catalog.yaml` | CREATE | The registry: one entry per feature column currently produced — 17 base features (incl. the FRED columns + `yield_curve`), 3 sentiment columns (`sentiment_score`, `doc_count`, `has_coverage`), and the M3 additions. Exact count fixed at write time from the M3 verdict |
| `src/quant/features/catalog.py` | CREATE | `FeatureRecord` pydantic model (field types + `Literal` enums); `load_catalog() -> dict[str, FeatureRecord]` (keyed by name, raises on duplicates); `validate_catalog_coverage(features_by_symbol)` — exact two-way set comparison between catalog names and produced columns, raising with the missing/extra names listed |
| `src/quant/features/__init__.py` | UPDATE | Re-export `load_catalog`, `validate_catalog_coverage` for notebook use |
| `tests/test_catalog.py` | CREATE | Schema validation, loader behavior, and the drift-enforcement test (see Task 3) |
| `pyproject.toml` | UPDATE (if needed) | Add `pyyaml` if not already a pinned dependency (prefect likely brings it — verify before adding) |
| `docs/concepts/feature-glossary.md` | UPDATE | Note at top: catalog is the machine-readable index; glossary is the prose rationale; the enforcement test keeps them honest via `glossary_ref` |
| `CLAUDE.md` | UPDATE | Milestone 4 status; codebase-map entry for `catalog.yaml` / `catalog.py` |

## Tasks

### Task 1: `FeatureRecord` model + loader

- **Action**: In `catalog.py`, define a pydantic `FeatureRecord` with the 12
  schema fields, using `Literal[...]` for the `family`, `source`, and
  `ablation_status` enums and default `None`/`[]` only for `regime_notes` /
  `depends_on`. Implement `load_catalog(path=DEFAULT_CATALOG_PATH)`:
  `yaml.safe_load` → validate each entry through `FeatureRecord` → return
  `{name: FeatureRecord}`. Raise `ValueError` listing duplicate names and any
  `depends_on` reference that names an unregistered feature (referential
  integrity).
- **Mirror**: `config.py` pydantic style; `ValueError`-with-names error style.
- **Validate**: `.venv/bin/pytest tests/test_catalog.py -v -k "load or schema"` — valid file loads; a bad enum value, a duplicate name, and a dangling `depends_on` each raise with the offender named.

### Task 2: Author `catalog.yaml` for the full current feature set

- **Action**: Register every column the feature pipeline produces as of M3
  completion: the 17 Phase 2/2.5 features (incl. FRED columns + `yield_curve`),
  the 3 sentiment columns, and the M3 additions (all 7 candidates are
  registered regardless of ablation outcome — `ablation_status` records the
  verdict: `tested_edge` / `tested_no_edge` from nb08). `publication_lag_days`
  comes from the M5 pinned lag table for the macro family, 0 elsewhere.
  `lookback_bars` values must match the warmup arithmetic already documented in
  the glossary (e.g., `ma200_ratio` → 200).
- **Mirror**: Field content sourced from `feature-glossary.md` entries — the catalog indexes, the glossary explains.
- **Validate**: `load_catalog()` succeeds; spot-check that `ret_252d.lookback_bars == 252` and every macro entry carries the M5 lag.

### Task 3: Drift-enforcement test (the milestone's teeth)

- **Action**: In `tests/test_catalog.py`, build a real feature matrix offline —
  synthetic OHLCV (+ synthetic FRED frame + sentiment frame via the existing
  `tests/test_features.py` fixture patterns) through `build_features` +
  `add_cross_sectional_features` — and assert:
  ```python
  assert set(produced.columns) == set(load_catalog().keys())
  ```
  with a failure message that prints `unregistered` and `phantom` name lists.
  This is the PRD's "100% coverage" metric expressed as a permanently-running
  test: adding a feature without registering it, or retiring one without
  updating the catalog, fails CI. Also assert every `glossary_ref` anchor
  matches the `### <name>` headings present in `feature-glossary.md` (cheap
  regex pass) so prose and registry can't silently diverge.
- **Mirror**: Harness self-test philosophy from `backtest/CLAUDE.md` ("if a change breaks these tests, the change is wrong — not the tests").
- **Validate**: `.venv/bin/pytest tests/test_catalog.py -v` — green on the registered set; deliberately commenting out one YAML entry makes the test fail naming it.

### Task 4: Docs + CLAUDE.md

- **Action**: Add the catalog/glossary division-of-labor note to the glossary
  header. Update `CLAUDE.md`: Milestone 4 status, codebase map rows for
  `catalog.yaml` + `catalog.py`, and one line telling future agents the rule:
  *"new feature ⇒ glossary entry + catalog entry + the drift test passes."*
- **Mirror**: Existing CLAUDE.md codebase-map row format.
- **Validate**: `grep "catalog" CLAUDE.md docs/concepts/feature-glossary.md`.

## Validation

```bash
# Full suite:
.venv/bin/pytest tests/ -v

# Targeted:
.venv/bin/pytest tests/test_catalog.py -v --cov=src/quant/features/catalog --cov-report=term-missing

# Lint:
.venv/bin/ruff check src/quant/features/catalog.py tests/test_catalog.py

# Sanity: confirm pyyaml availability before touching pyproject:
.venv/bin/python -c "import yaml; print(yaml.__version__)"
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Over-engineering toward Phase 5 agent-runtime concepts | Medium | Pre-committed 12-field schema in this plan; any field addition requires a PR explaining the consumer that needs it *today* |
| Catalog and glossary drift apart | Medium | Two-way enforcement: the drift test checks columns↔catalog and catalog↔glossary anchors |
| `mom_21d` positional-index coupling (`MomentumBaseline` reads column 5) makes column *order* significant, but the catalog only checks *set* membership | Medium | Out of catalog scope by design — order is a model-contract concern; the regression test from the M3 plan covers it. Note the limitation in `catalog.py`'s docstring |
| Sentiment columns are conditional (only when `sentiment_df` passed), so the drift test's "produced columns" depends on fixtures | Medium | The drift test builds the *maximal* matrix (with sentiment) and the catalog registers the maximal set; document that callers running without sentiment produce a registered subset |
| pyyaml missing from the environment | Low | Prefect depends on pyyaml; verify with the import check above before editing `pyproject.toml` |
| M3 verdict changes the feature list after the catalog is authored | Low (sequencing) | M4 runs strictly after M3's verdict; the catalog is authored once against the settled list |

## Acceptance

- [ ] `catalog.yaml` registers 100% of produced feature columns (PRD metric), each with all 12 fields
- [ ] `load_catalog()` validates schema, duplicates, and `depends_on` referential integrity
- [ ] Drift-enforcement test fails on either unregistered or phantom features, naming them
- [ ] Glossary-anchor check ties every catalog entry to its prose entry
- [ ] Macro entries carry the M5 publication lags; lookbacks match the glossary
- [ ] `CLAUDE.md` updated with status + the "new feature ⇒ three updates" rule
- [ ] Full test suite green
- [ ] Patterns mirrored, not reinvented (per the table above)

---
*Status: DRAFT — awaiting user confirmation before implementation.*
