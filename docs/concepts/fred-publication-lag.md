# FRED Publication Lags and the Macro ASOF Join

> **Living reference.** Companion to `docs/concepts/feature-glossary.md`.
> This document specifies how FRED macro features travel from ingestion to
> the model's feature matrix, the publication-lag corrections applied along
> the way, and the evidence behind the pinned lag values in
> `src/quant/features/engineering.py:FRED_PUBLICATION_LAGS`. Update it when
> a series is added or a lag is re-verified. Do not retune lags to make a
> failing model pass.

---

## Why this document

The Phase 3 in-sample SHAP rankings are dominated by macro features (DFF,
yield_curve, DGS10, VIXCLS) while the same model shows no OOS edge — the
classic signature of a look-ahead artifact. The Phase 4A Milestone 5 audit
found a concrete discrepancy: the `engineering.py` module docstring claimed
the FRED join attached "the most recent observation whose **ingested_at**
<= bar_date", but the code actually merges on the **observation date**
(`timestamp`). DFF for day *t* is published by the NY Fed the *next*
business day — so under the unlagged join the model saw macro values one
business day before they were publicly knowable.

The fix is a per-series **publication-lag shift**: each series' observation
dates are moved forward by its pinned lag (in business days) before the
asof merge, so bar *t* only receives observations that were published at or
before the close of bar *t*.

**Pre-committed decision rule (anti-p-hacking):** the lagged join is the
default on *correctness* grounds, regardless of whether it helps or hurts
measured performance. The Milestone 5 A/B experiment exists to quantify the
re-statement of earlier results, not to decide whether to adopt the fix —
the same discipline as the pinned VIX regime thresholds and the frozen
`TripleBarrierConfig` defaults.

---

## The join path, end to end

1. **Ingestion** (`src/quant/ingest/fred_macro.py`). `fetch_series` pulls
   each series from the FRED API keyed by **observation date**
   (`timestamp`). `to_processed` stamps each row with `ingested_at`,
   re-pulls a 45-day overlap window on incremental runs, and dedups by
   sorting on `ingested_at` and keeping the **last** row per
   `(series_id, timestamp)` — i.e., the lake stores the *latest vintage
   only* (see the limitation section below).
2. **Wide pivot + ffill** (`engineering.py:_load_fred_wide`). One DuckDB
   query loads the approved series (`_FRED_SERIES`), pivots to one column
   per series on the union of observation dates, and forward-fills. The
   ffill exists because DFF publishes every calendar day while DGS10 and
   VIXCLS publish only on market days — without it, the asof merge would
   hand NaN to bars that align with another series' gap rows.
3. **Publication-lag shift + ASOF merge**
   (`engineering.py:_attach_fred_features` via `_apply_publication_lags`).
   Each series' observation dates are shifted forward by its pinned lag in
   business days (`numpy.busday_offset(..., roll="forward")` — weekend
   observation dates first roll to the next business day, then count the
   lag, so a Sunday DFF print with lag 1 becomes available Tuesday,
   matching the NY Fed's actual weekend release behaviour). When several
   observation dates collide on one availability date, the latest
   observation wins. The frame is re-ffilled, then a backward
   `pd.merge_asof` attaches, for each bar, the most recent row whose
   shifted date <= bar date. Shifting the already-ffilled frame is
   leak-safe: an ffilled entry duplicates an *older* observation, so the
   shift can never move information earlier in time.
4. **Derived feature.** `yield_curve = DGS10 − DFF` is computed *after* the
   merge, so it inherits the shifted (point-in-time correct) inputs.

`build_features(..., fred_publication_lags=None)` bypasses the shift and
reproduces the legacy unlagged join bit-for-bit — needed for the A/B
control arm and for reproducing Phase 2.5 / Phase 3 historical numbers.

---

## The decision-time convention (hard invariant)

> A feature value at bar *t* must be knowable at the **close** of bar *t* —
> the moment the trading signal is formed — even though fills happen at the
> next open.

This is stricter than "published on calendar day *t*". A value disseminated
at 4:15pm ET on day *t* is **not** knowable at the 4:00pm ET equity close,
so it must not appear in bar *t*'s features. This is why VIXCLS carries
lag 1 even though ALFRED records same-day availability (see below).

---

## Empirical evidence and pinned lags

Method: ALFRED vintage metadata via `fredapi.get_series_all_releases`,
`realtime_start >= 2025-01-01`, sampled the last 25 observations per
series; lag = `realtime_start − observation_date` in business days.
Run 2026-06-12.

| Series   | Observed first-release lag                          | Example                                              | Pinned lag |
|----------|-----------------------------------------------------|------------------------------------------------------|-----------|
| `DFF`    | 1 business day in 24/25 obs; 2 in 1/25. Weekend obs (Sat/Sun) release the following Tuesday. | obs Tue 2026-06-09 → first release Wed 2026-06-10    | **1**     |
| `DGS10`  | 1 business day in 23/25 obs; 2 in 2/25.             | obs Wed 2026-06-10 → release Thu 2026-06-11          | **1**     |
| `VIXCLS` | 0 business days in ALFRED (24/25 same-day; one weekend artifact). | Cboe disseminates the close ~4:15pm ET same day      | **1**     |

```python
FRED_PUBLICATION_LAGS: dict[str, int] = {"DGS10": 1, "DFF": 1, "VIXCLS": 1}
```

**The VIXCLS lag is convention-driven, not ALFRED-driven.** ALFRED shows
the VIX close available on the observation date itself, but Cboe publishes
the official close at ~4:15pm ET — *after* the 4:00pm equity close where
our signal forms. Under the decision-time convention above, same-day VIXCLS
is not knowable at signal time, so lag 1 is pinned conservatively. If the
signal-formation time ever moves (e.g., to a next-morning schedule), this
is the one lag whose rationale must be revisited — via the update protocol,
not ad hoc.

The occasional 2-day first-release lags (1/25 for DFF, 2/25 for DGS10) are
holiday artifacts; lag 1 covers the overwhelmingly common case and the
residual exposure is a rare 1-day early peek at a slow-moving rate, not a
systematic leak.

---

## Measured impact (nb07, slice-level)

Milestone 5's A/B experiment
(`notebooks/07_phase4a_fred_leakage.ipynb`, 5-symbol × ~8-year slice, GBM
preview `n_iter=10`, identical rows and seeds across arms — run 2026-06-12)
measured the lagged-vs-unlagged difference against the thresholds pinned in
the plan *before* any result existed:

| Quantity (full 17-feature A/B)        | Measured                      | Pinned threshold | Trips |
|----------------------------------------|-------------------------------|------------------|-------|
| sign-flip fraction of OOS bars         | **23.27%**                    | > 5%             | yes   |
| \|ΔSharpe\| aggregate                  | **0.265** (−0.540 → −0.806)   | > 0.1            | yes   |
| \|ΔSharpe\| `covid`                    | **0.253** (+1.614 → +1.867)   | > 0.1            | yes   |
| \|ΔSharpe\| `rate_cycle`               | **0.377** (−0.905 → −1.282)   | > 0.1            | yes   |

**Verdict: LEAK CONFIRMED + MATERIAL.** All pre-fix numbers (Phase 2.5,
Phase 3, nb02–nb06) are unreliable at the ±0.1 Sharpe granularity. The
deltas cut both directions by regime (lagged better in `covid`, worse in
`rate_cycle` and aggregate on this slice), so this is sensitivity, not a
uniform haircut. Corrected full-panel numbers land in Milestone 6;
nb02/nb04 are deliberately not re-run before then.

**Attribution caveat (the negative finding):** the leak does *not* explain
nb03's IS macro dominance. In the macro-only probe, IS hit-rate *improved*
under the lag (56.7% → 59.4%) and the arms' forecast accuracy is
statistically indistinguishable (DM two-sided p = 0.72); full-feature SHAP
top-5 rankings are stable between arms (4/5 overlap, Spearman ρ = +0.93).
The measured deltas read as model variance — the GBM hyperparameter search,
re-run on day-shifted inputs, lands on materially different fits — not as
lost predictive information. The IS-dominant / OOS-absent puzzle
re-attributes to feature instability or label misspecification and is
handed to Milestone 3 as a finding.

---

## Latest-vintage storage limitation

`to_processed()` in `src/quant/ingest/fred_macro.py` dedups by sorting on
`ingested_at` and keeping `last` per `(series_id, timestamp)` — revised
values **overwrite** originals. The lake therefore holds the latest
vintage, not the vintage that was visible historically.

- **Acceptable for DGS10 / DFF / VIXCLS**: these series have negligible
  revision risk — the first print is, for practical purposes, the final
  value.
- **This is precisely why CPI and UNRATE stay excluded** from
  `_FRED_SERIES`: both are revised substantially weeks after the reference
  period, so latest-vintage storage would silently feed the model revised
  values it could never have seen. Including them would require a separate
  real-time vintage source (ALFRED), not just a lag shift.

Do not rediscover this: any new FRED series must be screened for revision
behaviour *before* being added to `_FRED_SERIES`.

---

## Why not join on `ingested_at`?

The module docstring used to (falsely) claim the join used `ingested_at`,
and joining on it sounds "more correct" — it is not, given this lake:

- Backfilled history carries `ingested_at` = the backfill run date. An
  `ingested_at` join would make 20 years of macro observations "available"
  only from the backfill date onward, wiping out macro features for nearly
  every bar in the panel.
- `ingested_at` measures when *our pipeline* ran, not when the value became
  public. A weekend pipeline outage would masquerade as a publication delay.

The publication-lag shift on observation dates is the correct point-in-time
approximation: it models when the *publisher* released the value,
independent of our ingestion schedule.

---

## Update protocol

The pinned lags are intended to be stable. They were committed before any
lagged-vs-unlagged model comparison was run. To change them:

1. Re-run the empirical verification: pull ALFRED vintage metadata via
   `fredapi.get_series_all_releases` for each series, sample ≥ 20 recent
   observations, and tabulate `realtime_start − observation_date` in
   business days.
2. Open a PR with the new evidence table and the publisher-schedule
   citation that explains the change (e.g., a release-time change announced
   by the NY Fed or Cboe).
3. Re-run the Milestone 5 A/B slice with old and new lags and include the
   before/after comparison in the PR.
4. Do **not** retune lags to make a failing model pass — a lag is a fact
   about a publisher's release schedule, not a hyperparameter. The same
   discipline applies to the regime definitions in
   `regime-evaluation.md` and the T1–T6 thresholds in
   `evaluation-standards.md`.

When adding a **new** series: verify its lag empirically (step 1), screen
its revision behaviour (latest-vintage section above), add it to both
`_FRED_SERIES` and `FRED_PUBLICATION_LAGS`, and extend the evidence table
here.

---

## References

- Federal Reserve Bank of New York. *Effective Federal Funds Rate (EFFR)*
  — published the next business day at approximately 9:00am ET.
  https://www.newyorkfed.org/markets/reference-rates/effr
- Board of Governors of the Federal Reserve System. *H.15 Selected
  Interest Rates* — released daily at approximately 4:15pm ET; the FRED
  vintage typically lands the next morning.
  https://www.federalreserve.gov/releases/h15/
- Cboe. *VIX Index* — official closing value disseminated at
  approximately 4:15pm ET, after the 4:00pm equity close.
  https://www.cboe.com/tradable_products/vix/
- Federal Reserve Bank of St. Louis. *ALFRED: Archival FRED* — vintage
  (real-time) series used for the lag verification.
  https://alfred.stlouisfed.org/

---

*Sister documents:
[feature-glossary.md](feature-glossary.md) — definitions of the macro
features this lag table governs.
[evaluation-standards.md](evaluation-standards.md) — the aggregate-gate
(T1–T6) thresholds; Phase 2.5/3 numbers predate the lagged join.
[purging-and-embargo.md](purging-and-embargo.md) — the other half of the
leakage-control story (label-side, walk-forward splits).*
