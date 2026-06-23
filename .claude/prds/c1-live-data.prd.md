# C1 — Live Data + Same-Day Inference Pipeline

> **Project**: C (Live execution & deployment infrastructure) — sub-project C1.
> **Roadmap**: [`docs/PROJECT_ROADMAP.md`](../../docs/PROJECT_ROADMAP.md) §4 Project C,
> §7 "C1 — Live data + same-day inference pipeline", §8 ratified decisions 1 & 4.
> **Methodology** (binding): [`docs/METHODOLOGY.md`](../../docs/METHODOLOGY.md) — esp.
> §1 (pre-committed thresholds), §2 (gates-in-code), §4 (contract before consumer),
> §6 (drift contracts), §8 (invariant-parity audits), §15/§17 (tests + E2E notebook).
> **Existing substrate**: `src/quant/ingest/` (5 batch ingestors), `src/quant/flows/daily.py`
> (nightly orchestrator), `src/quant/storage/{lake,catalog}.py`, `src/quant/features/engineering.py`.
> **Backlog tasks**: `C1-M1`, `C1-M2`, `C1-M3` in
> [`docs/PRIORITIES.yaml`](../../docs/PRIORITIES.yaml).

## Problem

The research substrate is mature; **the deployment substrate is empty**
(ROADMAP §2: "Live data pipeline (same-day) — ❌ Not built. Ingestion is
nightly batch"). Today every data source lands through a Prefect flow on
`cron 30 22 * * 1-5` — **22:30 UTC on weekdays, after the US close**
(`src/quant/flows/daily.py:56`). Each ingestor is incremental from
`catalog.latest_timestamp(dataset)` and fetches the gap `[last + 1, now]`.
This is correct for *backtesting* — it produces a clean, deduplicated,
point-in-time-stamped historical lake — but it answers a different question
than *trading* does.

Backtesting asks: *"given history up to date T, what is the feature matrix
for T?"* and reads the whole lake at leisure. Same-day inference asks:
**"it is `now`; what is the most recent point-in-time-correct bar/observation
for every universe symbol and macro series, and can I build today's feature
row from it without look-ahead?"** Three gaps separate the second question
from the substrate that answers the first:

1. **No "as-of `now`" reader.** `build_features(symbols, prices_by_symbol, …)`
   (`features/engineering.py:343`) takes a pre-assembled `{symbol: OHLCV}`
   dict and has **no `asof` parameter**. There is no function that returns
   "the latest PIT-correct bar as of a given moment" — callers hand-assemble
   the price dict from `catalog.query(...)`. To run inference for *today* we
   need a reader that does this assembly correctly and refuses to hand back
   anything stamped after the as-of instant.
2. **No freshness contract.** Each ingestor *can* run any time, but nothing
   states *by what time of day today's bar is actually available*, nor
   detects when a feed is stale. Alpaca's free IEX daily bar, Tiingo's
   per-ticker EOD, FRED's release calendar (already lag-pinned in M5), and
   EDGAR's filing stream each have a different real-world availability
   profile. A live system that silently trades on yesterday's data because a
   feed was late is worse than one that halts and alerts.
3. **No train/serve parity guarantee.** The single largest correctness risk
   in any ML deployment is **train/serve skew** — the features computed at
   serving time diverging from those the model was trained/backtested on. The
   batch path (backtest) and the same-day path (inference) must produce
   **bit-for-bit identical** feature rows for the same `(symbol, date)`, or
   every backtest Sharpe is a fiction at deployment.

C1 does **not** trade, size, or predict (those are C2–C4). C1 builds the
**data plumbing** that every later C milestone and every live B-model run
stands on: a PIT-correct same-day reader, a pinned per-source freshness SLA,
and a monitor that enforces it. It is the C-project analog of `A-LEDGER` —
infrastructure that unblocks everything downstream — and per ROADMAP §3.2 it
is built *in parallel with* refocused research, not gated behind a positive
B verdict.

## Evidence

From the existing code (read at draft time):

| Source | Ingestor | Cadence today | Incremental key | Real-world freshness (to confirm in C1-M1) |
|---|---|---|---|---|
| Equity daily OHLCV | `alpaca_bars.py` (IEX free feed) | nightly batch | `catalog.latest_timestamp` | IEX consolidated daily bar settles shortly after 16:00 ET close; pullable same evening |
| Equity adjusted EOD | `tiingo_eod.py` (per-ticker) | nightly batch | `catalog.latest_timestamp` | EOD adjusted close typically available T+0 evening / T+1 morning |
| Macro series | `fred_macro.py` (long-form, 45-day revision overlap) | nightly batch | `catalog.latest_timestamp − 45d` | Per-series **release calendar**; lags already pinned `FRED_PUBLICATION_LAGS = {DGS10:1, DFF:1, VIXCLS:1}` (M5) |
| SEC filings | `edgar.py` (submissions API) | nightly batch | `catalog.latest_timestamp` | 8-K within ~4 business days of event; available minutes after filing |
| News/RSS | `rss.py` | nightly batch | `catalog.latest_timestamp` | Intraday, but daily-cadence consumption per ratified decision |

Structural facts that shape the design:

- **The lake is already point-in-time aware.** Every processed row carries an
  `ingested_at` stamp (`alpaca_bars.py:71`), and dedup keeps the latest
  `ingested_at` per `(symbol, timestamp)`. The PIT reader reuses this — it
  does not re-implement storage; it adds an *as-of query discipline* on top
  of `storage/catalog.py`.
- **FRED publication-lag correctness is solved and pinned** (M5,
  `docs/concepts/fred-publication-lag.md`). The same-day reader must apply the
  identical `FRED_PUBLICATION_LAGS` so that a feature row built live matches
  the backtest row. This is the train/serve-parity lever for macro features.
- **`build_features` already enforces PIT for FRED via an ASOF join**; the
  Phase 3 `validate_point_in_time()` guard (`features/sentiment.py`) is the
  reference pattern for "no row may carry information stamped after the
  decision instant." C1's PIT reader generalizes that guard to *price* bars.
- **Daily cadence is ratified** (ROADMAP §7 "No intraday — daily cadence").
  C1 builds a same-day **daily** pipeline, not an intraday tick pipeline.
  "Same-day" means "today's settled daily bar, available the evening of T,"
  not "live quotes."

## Users

- **Primary**: the **C2 execution layer** (LEAN local + paper). C2's
  placeholder ARIMA(1,0,0) algorithm needs `build_features(asof=today)` to
  emit a same-day prediction; `C2-PRD` **depends_on `C1-M2`** in the backlog.
  C1-M2's reader API is the contract C2 consumes.
- **Secondary**: every **live B-model run**. Once a B sub-project surfaces a
  deployable target, it runs through the same same-day reader → `build_features`
  → predict path C1 establishes. C1 makes "run the model on today's data" a
  one-call operation instead of bespoke per-model glue.
- **Tertiary (operations)**: whoever runs the daily pipeline reads the C1-M3
  freshness monitor to know, before market-relevant decisions, that every feed
  is current.
- **Not for**: backtesting (the batch lake path is unchanged and remains the
  source of truth for historical evaluation); intraday/HFT (explicitly out of
  scope per the ratified daily cadence).

## Hypothesis

We believe that **a point-in-time-correct same-day reader (`get_pit_bar`) plus
an `asof`-parameterized `build_features` path produces feature rows that are
bit-for-bit identical to the batch backtest path**, and that **a pinned
per-source freshness SLA with an automated monitor** makes data staleness a
detected, alertable condition rather than a silent corruption — for **the C2
execution layer and every future live model run** — closing the
"research-ready, deployment-empty" gap (ROADMAP §2).

We'll know we're right when (all thresholds pinned in "Success Metrics" before
any compute, METHODOLOGY §1, and reproduced verbatim in the C1-M2/M3 gate
functions, METHODOLOGY §2):

- **G1 (PIT / no look-ahead)**: `get_pit_bar(symbol, asof)` never returns a
  bar whose `timestamp > asof`, across a property-based test sweep of as-of
  instants — **zero** violations.
- **G2 (train/serve parity)**: features built via the same-day reader at
  `asof = T` equal the batch-path features for `T` to within floating-point
  tolerance (`rtol ≤ 1e-9`) on every shared column, over a held-out sample of
  ≥ 250 `(symbol, date)` pairs spanning ≥ 2 regimes — **zero** material
  mismatches.
- **G3 (freshness SLA enforced)**: the C1-M3 monitor flags **every** feed
  whose latest observation is older than its pinned SLA, with **no** false
  "stale" on a feed that is within SLA — verified on synthetic
  fresh/stale fixtures.

If G2 fails (the live and batch feature rows diverge), the verdict is **"train/serve
skew present — same-day inference is not deployment-safe until reconciled"**:
a valid, pre-committed negative that **blocks C2** until the divergence source
is found and fixed. C1 does not ship a reader that silently disagrees with the
backtest.

## Success Metrics

The deliverable is **correct, monitored data plumbing**, not a strategy edge.
The gate is therefore a **correctness + freshness** gate, not a Sharpe gate —
DSR/deflation is undefined here and C1 does **not** depend on `A-DSR-GATE`.
**All numeric thresholds are pinned here before any compute (METHODOLOGY §1)
and are the source of truth reproduced in the C1-M2/M3 gate functions
(METHODOLOGY §2).**

| # | Claim | Measured on | Statistic | Threshold (pinned) | Reference |
|---|---|---|---|---|---|
| G1 | `get_pit_bar` never returns a future bar | property-based sweep of as-of instants × universe symbols | count of bars with `timestamp > asof` returned | **exactly 0** | generalizes `validate_point_in_time()` (Phase 3) |
| G2 | Same-day reader ⇒ batch-identical features (no train/serve skew) | ≥ 250 `(symbol, date)` pairs, ≥ 2 regimes | max abs/rel diff per shared feature column | **`rtol ≤ 1e-9`, 0 material mismatches** | batch `build_features` path = ground truth |
| G3 | Freshness monitor flags exactly the stale feeds | synthetic fresh/stale fixtures per source | (false-stale count, missed-stale count) | **(0, 0)** | pinned SLA table below |

**Pinned per-source freshness SLA** (the materiality contract; finalized in
C1-M1 against measured availability, frozen before C1-M3 codes the monitor —
the values below are the PRD's pre-commitment, revisable only by a PRD
revision + new ledger entry, not in-flight):

| Source | SLA: today's (T) observation available by | Stale ⇒ alert when |
|---|---|---|
| Alpaca IEX daily bar | 23:00 UTC on trading day T | latest bar date < last trading day at 23:00 UTC |
| Tiingo adjusted EOD | 12:00 UTC on T+1 | latest bar date < last trading day at 12:00 UTC T+1 |
| FRED (per series) | release date + `FRED_PUBLICATION_LAGS[series]` business days | latest obs older than `expected_release + lag` |
| EDGAR 8-K | best-effort (event-driven) | no new filing scan in > 1 trading day |
| RSS | best-effort (daily cadence) | no new item in > 1 calendar day |

Notes on the metric choices:

- **G1 and G2 are the merge-blocking gates.** A reader that leaks future data
  (G1) or disagrees with the backtest (G2) is not deployment-safe; both are
  hard tests, not advisory diagnostics.
- **G3 is the operational gate.** "Stale and silent" is the failure mode C1
  exists to eliminate; a monitor that misses a stale feed or cries wolf on a
  fresh one is not doing its job. `(0, 0)` is checkable on synthetic fixtures
  without waiting for a real outage.
- **Materiality before significance (METHODOLOGY §10).** There is no
  statistical "significance" axis here — C1 makes no noisy estimate. The gates
  are deterministic correctness predicates, which is the strongest possible
  form of the materiality-first principle.

## Scope

**MVP** — the three milestones below, executed in order, reusing the existing
storage (`lake`, `catalog`), ingestors, FRED lag pins, and feature builder.
**No new data source, no new model, no new universe, no intraday** — C1 reads
the *existing* feeds *as of today* and monitors them; only the *as-of reader*,
the *`asof` integration*, and the *freshness monitor* are new.

1. **C1-M1 — Same-day data SLA audit.** `docs/concepts/data-freshness-slas.md`:
   for each of the 5 ingestors, the *measured* real-world freshness ("by what
   UTC time is today's bar/observation actually pullable?"), the gaps (e.g.
   weekend/holiday handling, FRED release-calendar variance), and the **pinned
   SLA table** (the one above, finalized against measurement). This is the
   concept contract (METHODOLOGY §4 — contract before the C1-M2/M3 consumers);
   no code.
2. **C1-M2 — Point-in-time today's-bar reader + `build_features` integration.**
   `src/quant/storage/realtime.py` exposing `get_pit_bar(symbol, asof)` (the
   most recent PIT-correct bar for a symbol as of an instant, reusing
   `storage/catalog.py`; never returns `timestamp > asof`) and a panel helper
   that assembles `{symbol: OHLCV-as-of-asof}` for `build_features`. Extend
   `build_features` to accept `asof` (default `None` = current full-history
   behavior, bit-for-bit; A/B-safe per the `fred_publication_lags=None` pattern
   already in the signature) so `build_features(symbols, asof=today)` works.
   Ship the **G1 (PIT) and G2 (parity) gate functions** with pinned thresholds.
   Tests land with the module (METHODOLOGY §15); a cross-module E2E notebook
   (`notebooks/13_c1_live_data.ipynb`, number confirmed against the live
   sequence at build time) exercises the reader → `build_features(asof)` →
   batch-parity check on the real lake and renders the G1/G2 verdict
   (METHODOLOGY §17).
3. **C1-M3 — Freshness monitor + alert.** `scripts/monitor_freshness.py`: reads
   `catalog.latest_timestamp` per dataset, compares against the pinned SLA
   table, emits a per-feed status (`fresh | stale | missing`) and a non-zero
   exit / alert on any stale feed. Ships the **G3 gate function** and a cron
   doc (`docs/concepts/freshness-monitor.md`). Tests cover the SLA arithmetic
   and the `(0,0)` fresh/stale-fixture gate (METHODOLOGY §15).

**Out of scope**

- **Intraday / live quotes / tick data** — daily cadence is ratified (ROADMAP
  §7, §8). C1 reads today's *settled daily* bar, not live ticks.
- **Trading, sizing, prediction emission** — C2 (execution), C3 (sizing), C4
  (confidence). C1 produces feature rows; it never acts on them.
- **New ingestors / data sources / universe** — C1 monitors and reads the
  *existing* 5 feeds. A discovered need for a faster feed (e.g. a paid Alpaca
  SIP tier) is a *finding*, not a C1 deliverable.
- **Replacing the nightly batch** — C1 *supplements* the batch path (which
  remains the backtest source of truth). The same-day reader and the batch
  lake share storage; C1 adds the as-of discipline, it does not migrate the
  pipeline.
- **Backfilling or revising historical data** — C1's parity gate (G2) *uses*
  the historical lake as ground truth; it does not modify it.

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->

| # | Milestone | Outcome | PRIORITIES task | Depends on |
|---|---|---|---|---|
| 1 | Same-day SLA audit | `docs/concepts/data-freshness-slas.md` documents measured freshness per ingestor and pins the SLA table | `C1-M1` | `C1-PRD` |
| 2 | PIT reader + `build_features(asof)` | `storage/realtime.py::get_pit_bar` + `asof` integration exist; G1 (no look-ahead) and G2 (batch parity, `rtol ≤ 1e-9`) gate functions pass | `C1-M2` | `C1-M1` |
| 3 | Freshness monitor | `scripts/monitor_freshness.py` flags exactly the stale feeds (G3 = `(0,0)`); cron doc shipped | `C1-M3` | `C1-M2` |
| Gate | The same-day reader is PIT-correct (G1) AND batch-identical (G2) AND freshness is enforced (G3) | Binary. **Pass** → a deployment-safe same-day data path is in code; C2 is unblocked. **Fail (G2)** → train/serve skew documented; C2 stays blocked until reconciled. | — | — |

## Pre-committed gate (verbatim — implemented across C1-M2 and C1-M3)

The gate functions are the source of truth; this prose describes them
(METHODOLOGY §2). C1's gate is the conjunction of three deterministic
predicates:

1. **PIT / no look-ahead (G1, C1-M2)** — for every `(symbol, asof)` in the
   property-based sweep, `get_pit_bar(symbol, asof).timestamp <= asof`. The
   returned-future-bar count must be **exactly 0**.
2. **Train/serve parity (G2, C1-M2)** — for ≥ 250 `(symbol, date)` pairs over
   ≥ 2 regimes, `features_live(asof=date)` equals `features_batch(date)` per
   shared column within `rtol = 1e-9`; the material-mismatch count must be
   **0**. (NaN warmup rows compare equal to NaN; the comparison is on the
   intersection of non-warmup columns.)
3. **Freshness SLA (G3, C1-M3)** — on synthetic fresh/stale fixtures derived
   from the pinned SLA table, `(false_stale_count, missed_stale_count) == (0, 0)`.

`rtol`, the sweep size, the pair count, the regime count, and the SLA table
are all pinned arguments/constants with the defaults above — changing any of
them after a result is visible invalidates the run and requires a PRD revision
plus a new ledger entry (METHODOLOGY §1). The SLA *values* live in one place
(a module constant consumed by both the monitor and its tests) under a drift
contract (METHODOLOGY §6), so prose and code cannot diverge.

## Open Questions

- [ ] **Does `get_pit_bar` read from `processed` (deduped) or also consider
      the latest `raw` landing?** The processed layer lags raw by one
      `to_processed` pass. **Resolved in C1-M1, before C1-M2 code**: the reader
      reads `processed` only (the deduped, schema-validated, PIT-stamped layer)
      so live and backtest share *exactly* one source — the train/serve-parity
      requirement (G2) forbids the live path reading a layer the backtest never
      sees. The same-day flow must therefore run `to_processed` before the
      reader is queried; documented as the pipeline ordering in C1-M1.
- [ ] **`asof` semantics: bar-date vs wall-clock instant.** `asof` is a
      timezone-aware UTC instant compared against the bar `timestamp`
      (Alpaca 04:00 UTC, Tiingo 00:00 UTC — the existing convention in
      `tiingo_eod.py:58`). The reader normalizes to date for *selection* but
      keeps the instant for the look-ahead guard. Frozen in C1-M1; the test
      sweep includes intraday as-of instants to catch off-by-one-day errors at
      the UTC boundary.
- [ ] **Weekend/holiday as-of behavior.** `get_pit_bar(symbol, Saturday)` must
      return Friday's bar, not error or return a stale flag. The trading
      calendar (`utils/calendar.py`) is the authority; pinned in C1-M1.
- [ ] **FRED parity lever.** The same-day reader must apply the identical
      `FRED_PUBLICATION_LAGS` the batch path uses (M5). G2 *measures* this — a
      macro-feature mismatch would mean the lag was applied differently live vs
      batch. No new lag values; reuse the pinned dict.
- [ ] **Monitor delivery channel (C1-M3).** Exit-code + stderr log for the MVP
      (cron-friendly, testable); a richer channel (email/Slack/dashboard) is a
      C5-monitoring concern, flagged as a follow-up, not a C1 deliverable.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `get_pit_bar` introduces a look-ahead bug the batch path doesn't have | Low | **Very High** | G1 is a property-based test (`timestamp <= asof`, exactly 0 violations) that **blocks merge**; generalizes the proven `validate_point_in_time()` guard. The reader reuses `storage/catalog.py` — no new query-of-future-data path. |
| Train/serve skew: live features silently differ from backtest (G2 fails) | Medium | **Very High** | G2 is a pre-committed merge-blocking parity gate (`rtol ≤ 1e-9`). A failure is a *valid negative* that blocks C2 until reconciled — C1 never ships a divergent reader. The single shared `processed` source (Open Q 1) is the structural mitigation. |
| `build_features(asof=…)` change breaks existing batch callers | Low | High | `asof` defaults to `None` = current full-history behavior, bit-for-bit (the exact pattern `fred_publication_lags=None` already uses in the signature). Existing 467-test suite must stay green; a regression test pins default-path equivalence. |
| Pinned SLA is wrong (too tight ⇒ false alerts; too loose ⇒ misses real staleness) | Medium | Medium | SLA is *measured* in C1-M1 before being frozen, not guessed; G3 fixtures test both directions. Revising it later is a PRD revision + ledger entry, visible in `git diff`. |
| Real feed is genuinely late on a live day (Alpaca/FRED outage) | Medium | Medium | This is the condition the monitor *exists to surface* — C1-M3 alerts rather than letting a downstream model trade on stale data. Not a bug; the designed-for case. |
| Reader reads `processed` which lags `raw` by one ingest pass | Medium | Medium | Resolved in Open Q 1: live flow runs `to_processed` before querying; live and batch share exactly one layer. Pipeline ordering documented in C1-M1. |
| Weekend/holiday off-by-one (returns no bar or a future bar near the UTC date boundary) | Medium | Medium | `utils/calendar.py` is the calendar authority; the G1 sweep includes weekend/holiday and intraday-boundary as-of instants. |
| C1 ships but no B model is ever deployable (Project B finds no edge) | Medium | Low | Independent by design: C1's value (a deployment-safe same-day path) accrues to C2's placeholder ARIMA regardless of any B verdict — that is exactly the "build deployment in parallel" rationale (ROADMAP §3.2). |

## Sequencing notes

- **C1-M1 ships the SLA concept contract before C1-M2/M3 write code**
  (METHODOLOGY §4). The freshness table is the contract both the reader
  (implicitly, via the `processed`-only decision) and the monitor (directly)
  consume.
- **C1-M2 ships the G1/G2 gate functions with thresholds pinned before any
  parity is measured** (METHODOLOGY §2). No `rtol` is computed against an
  unwritten gate.
- **C1-M2 must not touch walk-forward split logic.** The PIT reader is a
  *storage-layer* read discipline; it adds no new split path and leaves
  `walkforward.py` / `harness.py` purge+embargo invariants untouched
  (`backtest/CLAUDE.md`). The harness self-tests (random ⇒ ~0 edge, leaky ⇒
  caught) must stay green.
- **C1 does NOT depend on `A-DSR-GATE`** (no Sharpe claim → no deflation). It
  depends only on `A-LEDGER` (done), already encoded in `PRIORITIES.yaml`.
- **Ledger discipline.** C1 is infrastructure, not a research trial — it makes
  no pre-registered edge claim, so it contributes **no** research trials to the
  cross-PRD deflation `N`. If a C1 milestone run emits a verdict artifact, it
  may record an audit-only ledger entry (`n_comparisons = 0`) per the
  A-LEDGER-RUNNERS pattern; this is bookkeeping, not a deflation contribution.
- **C2-PRD depends on C1-M2** (not the whole of C1): the execution layer needs
  the `asof` reader, and can be drafted once the reader's contract is fixed.
  C1-M3 (monitor) runs in parallel with early C2 work.
- **Project-C closeout.** Project C has no `C-CLOSE` task yet. When one is
  created (METHODOLOGY §21 / AGENT_OPERATION "Project closeout"), C1-M1/M2/M3
  must be added to its `depends_on`. Flagged as a discovered follow-up.
- **Methodology open-question sync.** METHODOLOGY §"Open questions" lists
  "Materiality thresholds for non-Sharpe targets … Set during Project B1 / C4
  PRD drafting." C1 pins *operational* thresholds (freshness SLAs, parity
  `rtol`), not research-target materiality, so it does **not** resolve that
  bullet — but the precedent of pinning non-Sharpe correctness gates is worth a
  forward-looking notes-section sync, flagged as a low-priority follow-up
  (mirroring A-METH-NONSHARPE-SYNC / A-METH-OOSATTR-SYNC).

---
*Status: DRAFT (2026-06-23) — pre-commitment for Project C1. Thresholds in
"Success Metrics" and "Pre-committed gate" (G1 = 0 future bars, G2 `rtol ≤ 1e-9`
with 0 mismatches, G3 = `(0,0)`) and the freshness SLA table are frozen on
ratification; changes require a PRD revision and a new ledger entry, not an
in-flight override. Next: `/plan` turns C1-M1 into an implementation plan.*
