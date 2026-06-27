# Same-Day Data Freshness SLAs and the As-Of Read Contract

> **Living reference.** Companion to
> [`fred-publication-lag.md`](fred-publication-lag.md) and
> [`purging-and-embargo.md`](purging-and-embargo.md). This document is the
> **C1-M1 deliverable**: the per-ingestor freshness audit and the **pinned
> per-source SLA table** that the C1-M3 monitor enforces, plus the read
> contract (processed-only, as-of semantics, weekend/holiday behaviour) that
> the C1-M2 PIT reader is built against. It is the *contract before the
> consumer* (METHODOLOGY §4): C1-M2 and C1-M3 write code against the
> decisions frozen here. Update it when an ingestor's cadence, a source's
> real-world availability, or the SLA table changes — via the update protocol
> at the end, not ad hoc.

---

## Why this document

The research substrate is mature; the deployment substrate is empty
(ROADMAP §2). Every data source lands through one Prefect flow on
`cron 30 22 * * 1-5` — **22:30 UTC on weekdays, after the US close**
(`src/quant/flows/daily.py:56`). Each ingestor is incremental from
`catalog.latest_timestamp(dataset)` and fetches the gap `[last + 1, now]`.
That schedule is correct for *backtesting*: it produces a clean,
deduplicated, point-in-time-stamped historical lake read at leisure.

Same-day inference asks a different question: **"it is `now`; what is the
most recent point-in-time-correct bar/observation for every universe symbol
and macro series, and is today's bar actually available yet?"** Two gaps
separate that question from the batch substrate, and C1-M1 closes the
*contract* side of both:

1. **No freshness contract.** Each ingestor *can* run any time, but nothing
   states *by what time of day today's observation is actually available*,
   nor detects when a feed is stale. A live system that silently trades on
   yesterday's data because a feed was late is worse than one that halts and
   alerts. This document pins, per source, the time-of-day by which today's
   observation is expected and the condition under which the feed is
   "stale ⇒ alert." That table is the C1-M3 monitor's source of truth.
2. **No agreed read discipline.** The same-day reader (C1-M2,
   `storage/realtime.py::get_pit_bar`) must read *exactly* the layer the
   backtest reads, apply *exactly* the same publication lags, and never hand
   back a bar stamped after the decision instant — or the train/serve-parity
   gate (G2) fails and every backtest Sharpe is a fiction at deployment.
   This document freezes the three read decisions the C1 PRD assigns to
   C1-M1: **processed-only**, **as-of instant semantics**, and
   **weekend/holiday behaviour**.

C1-M1 is **documentation only** — no code. It is the C-project analog of the
pre-commitment step: pin the contract, then let C1-M2/M3 implement against it
(METHODOLOGY §1, §4).

---

## The ingestion substrate today

| Source | Ingestor | Dataset | Incremental key | Overlap re-pull | Bar/obs timestamp convention |
|---|---|---|---|---|---|
| Equity daily OHLCV | `ingest/alpaca_bars.py` (IEX free feed) | `equity_bars_daily` | `catalog.latest_timestamp` | none (`last + 1d`) | **04:00 UTC** per session day (convention documented at `tiingo_eod.py:58`) |
| Equity adjusted EOD | `ingest/tiingo_eod.py` (per-ticker) | `equity_eod_tiingo` | `catalog.latest_timestamp` | none (`last + 1d`) | **00:00 UTC** (midnight) per session day (`tiingo_eod.py:58`) |
| Macro series | `ingest/fred_macro.py` | `macro_fred` | `catalog.latest_timestamp − 45d` | **45-day** revision overlap (`fred_macro.py:78`) | observation date 00:00 UTC; lags applied downstream (see below) |
| SEC filings | `ingest/edgar.py` (submissions API) | `text_documents` | latest `published_at` in lake | **7-day** amended-filing overlap (`edgar.py:354`) | `filed_at` = SEC filing date (authoritative, never revised) |
| News / RSS | `ingest/rss.py` | `text_documents` | none (feeds expose current items only) | n/a (backfill is a no-op) | `published_at` from `<pubDate>` (RFC 2822 → UTC); unparseable ⇒ **dropped** |

All five run inside `daily_ingest` (`flows/daily.py`) with per-source failure
isolation: one feed failing is logged and does not abort the others. The lake
is already point-in-time aware — every processed row carries an `ingested_at`
stamp, and dedup keeps the latest `ingested_at` per natural key. The C1-M2
reader reuses this; it does not re-implement storage.

---

## Per-source freshness audit

The "available by" times below are **desk-research / code-derived estimates**
of each publisher's real-world release schedule, not the output of a
multi-day live-measured pull (declared deviation — see "Declared deviations").
They are deliberately conservative: the SLA must not fire a false "stale"
alert on a feed that is merely on the late edge of its normal window.

### 1. Alpaca IEX daily bar (`equity_bars_daily`)

- **What it is.** The consolidated IEX daily OHLCV bar for each universe
  symbol, free tier (`DataFeed.IEX`).
- **Real-world availability.** The IEX daily bar settles shortly after the
  16:00 ET equity close. It is reliably pullable the same evening; by
  **23:00 UTC** (≈ 18:00–19:00 ET depending on DST) the settled bar for
  trading day *T* is available. The existing batch flow already exploits this
  — it runs at 22:30 UTC, after the close.
- **Edge cases.** Weekends/holidays produce no bar; the SLA is keyed off the
  *last trading day* (`utils/calendar.py::last_trading_day`), not the calendar
  day, so Saturday does not trip a false alert. The IEX feed is a single
  venue, not the full SIP consolidated tape — a known data-completeness
  caveat carried from ingestion, not a freshness concern.

### 2. Tiingo adjusted EOD (`equity_eod_tiingo`)

- **What it is.** Split/dividend-adjusted EOD prices, per ticker, an
  independent cross-check against Alpaca and the source of the adjusted-close
  column.
- **Real-world availability.** Adjusted EOD typically lands T+0 evening to
  T+1 morning; adjustment factors (splits/dividends) can settle overnight.
  The conservative contract is **12:00 UTC on T+1** — by midday UTC the day
  after the session, the fully-adjusted bar for *T* is available.
- **Edge cases.** Per-ticker fetch with a `try/except` that skips a bad
  ticker (`tiingo_eod.py:38`), so a single delisted/renamed symbol does not
  abort the batch but *does* leave that symbol's bar absent — a per-symbol
  staleness the monitor sees as a stale feed for that key.

### 3. FRED macro series (`macro_fred`)

- **What it is.** `settings.fred_series = [DGS10, DFF, VIXCLS, CPIAUCSL,
  UNRATE]` are *ingested*, but only **DGS10, DFF, VIXCLS** feed the model
  (`engineering._FRED_SERIES`). **CPIAUCSL and UNRATE are deliberately
  excluded** from the feature path because they are heavily revised and the
  lake stores latest-vintage only — see
  [`fred-publication-lag.md`](fred-publication-lag.md) "Latest-vintage storage
  limitation." The freshness SLA therefore governs the three model-relevant
  series.
- **Real-world availability.** Each series follows a **release calendar**, not
  a fixed wall-clock. The publication lags are already pinned and verified
  (M5): `FRED_PUBLICATION_LAGS = {DGS10: 1, DFF: 1, VIXCLS: 1}` business days.
  Today's value for series *s* is expected to be available at
  **`expected_release_date(s) + FRED_PUBLICATION_LAGS[s]` business days**;
  staleness is "latest stored observation is older than that expectation,"
  evaluated per series.
- **Edge cases.** Holiday-shifted releases (the occasional 2-business-day
  first-release lag noted in the M5 evidence table); the decision-time
  convention (VIXCLS carries lag 1 because Cboe's 16:15 ET print post-dates
  the 16:00 ET signal close). The same-day reader **must apply the identical
  `FRED_PUBLICATION_LAGS`** the batch path uses — this is the macro
  train/serve-parity lever that G2 measures.

### 4. SEC EDGAR 8-K / 10-K / 10-Q (`text_documents`)

- **What it is.** Filings filed *by* each universe company, fetched via the
  submissions API by CIK.
- **Real-world availability.** Event-driven, not scheduled. An 8-K is filed
  within ~4 business days of its triggering event and is retrievable from
  EDGAR minutes after filing; `filed_at` is the SEC filing date and is never
  revised. There is therefore no meaningful "today's filing is late" SLA —
  the correct freshness predicate is **liveness of the scan**, not arrival of
  a specific document: alert if no new filing scan has completed in **> 1
  trading day**.
- **Edge cases.** The 7-day incremental overlap (`edgar.py:354`) catches
  back-dated amended filings; long stretches with zero new filings for the
  universe are normal and must not be read as staleness — hence the
  scan-liveness framing rather than a per-document deadline.

### 5. News / RSS (`text_documents`)

- **What it is.** Financial-news headlines from configured feeds
  (`settings.rss_feed_urls`), symbol attribution derived from the feed URL.
- **Real-world availability.** Intraday in reality, but consumed at **daily
  cadence** per the ratified decision (ROADMAP §7). RSS feeds expose only
  current items, so there is no backfill and no fixed deadline — the
  freshness predicate is again liveness: alert if **no new item in > 1
  calendar day**.
- **Edge cases.** Items with missing/unparseable `<pubDate>` are *dropped*,
  never stamped with ingestion time (`rss.py:94`) — so a feed format change
  manifests as "no new items," which the > 1-day liveness check surfaces.

---

## Pinned per-source freshness SLA (the materiality contract)

This is the C1 PRD's pre-committed SLA table, **finalized here against the
audit above and frozen** (METHODOLOGY §1). It is the source of truth the
C1-M3 monitor reproduces in code; the values live in **one module constant**
consumed by both the monitor and its tests, under a drift contract
(METHODOLOGY §6), so prose and code cannot diverge. Changing any value is a
PRD revision + a new ledger entry, visible in `git diff` — never an in-flight
override.

| Source | SLA: today's (T) observation available by | Stale ⇒ alert when | Freshness key |
|---|---|---|---|
| Alpaca IEX daily bar | **23:00 UTC on trading day T** | latest bar date < last trading day, evaluated at/after 23:00 UTC | last trading day (`calendar`) |
| Tiingo adjusted EOD | **12:00 UTC on T+1** | latest bar date < last trading day, evaluated at/after 12:00 UTC T+1 | last trading day (`calendar`) |
| FRED (per series) | **release date + `FRED_PUBLICATION_LAGS[series]` business days** | latest obs older than `expected_release + lag` | per-series release calendar |
| EDGAR 8-K | **best-effort (event-driven)** | no new filing scan in > **1 trading day** | scan liveness |
| RSS | **best-effort (daily cadence)** | no new item in > **1 calendar day** | item liveness |

The audit confirms the PRD's pre-committed values are correct against the
publishers' real-world schedules; no value is loosened or tightened from the
PRD. The two "best-effort" sources (EDGAR, RSS) are intentionally
liveness-checked rather than deadline-checked, because both are event-driven
and a quiet period is normal, not stale.

---

## Read contract decisions (frozen for C1-M2)

The C1 PRD "Open Questions" assigns three read decisions to C1-M1, to be
resolved *before* C1-M2 writes code. They are frozen here.

### Processed-only, with pipeline ordering

`get_pit_bar` reads the **`processed`** (deduped, schema-validated,
PIT-stamped) layer **only** — never the latest `raw` landing. The
train/serve-parity requirement (G2) forbids the live path reading a layer the
backtest never sees; live and backtest must share *exactly one* source. The
processed layer lags `raw` by one `to_processed` pass, so the **same-day flow
must run `to_processed` before the reader is queried**. Pipeline ordering for
a same-day inference run: *ingest → `to_processed` → `get_pit_bar` →
`build_features(asof)` → predict*. (This ordering is a C1-M2/C2 obligation;
it is recorded here as the contract.)

### As-of semantics: instant, not bare date

`asof` is a **timezone-aware UTC instant** compared against the bar
`timestamp`. The reader normalizes to date for *selection* (which session's
bar) but keeps the instant for the **look-ahead guard**: it must never return
a bar whose `timestamp > asof`. Because Alpaca stamps 04:00 UTC and Tiingo
00:00 UTC for the same session, the instant comparison is what prevents an
off-by-one-day leak at the UTC boundary — e.g. an `asof` of 02:00 UTC must
not pick up an Alpaca bar stamped 04:00 UTC the same calendar day. The C1-M2
G1 property-test sweep must include intraday as-of instants to exercise this
boundary.

### Weekend / holiday behaviour

`get_pit_bar(symbol, Saturday)` must return **Friday's bar** — not an error,
not a stale flag. The trading calendar
(`utils/calendar.py::last_trading_day`) is the authority for "what is the most
recent session on or before `asof`." Freshness for the price feeds is keyed
off the last *trading* day, so weekends and exchange holidays never trip a
false "stale" alert. The G1 sweep must include weekend/holiday as-of instants.

### FRED parity lever

The same-day reader applies the identical `FRED_PUBLICATION_LAGS` the batch
path uses (M5). No new lag values are introduced; the reader reuses the pinned
dict. G2 *measures* this — a macro-feature mismatch between the live and batch
rows would mean the lag was applied differently, which is exactly the
train/serve skew G2 exists to catch.

---

## Ledger discipline

C1 is **infrastructure, not a research trial** — it makes no pre-registered
edge claim, so it contributes **no** research trials to the cross-PRD
deflation `N` (METHODOLOGY §12). C1-M1 is documentation and emits no run or
verdict artifact, so it records **no** ledger entry. If a later C1 milestone
run emits a verdict artifact, it may record an audit-only entry
(`n_comparisons = 0`) per the `A-LEDGER-RUNNERS` pattern — bookkeeping, not a
deflation contribution.

---

## Declared deviations (METHODOLOGY §9)

- **Availability times are desk-research / code-derived, not live-measured.**
  The "available by" times in the audit are reasoned from each publisher's
  documented release schedule and the existing ingestor code (cadence,
  overlap windows, timestamp conventions), not from a multi-day live pull
  that timestamps the first moment today's bar is fetchable. They are
  deliberately conservative so the SLA does not false-alarm. A live-measured
  tightening is captured as a follow-up (`C1-M1-MEASURE`); it can only loosen
  the conservatism, and any change is a PRD-revision + ledger-entry path, not
  an in-flight edit. The pinned SLA values are unchanged from the ratified
  PRD regardless — this audit confirms them, it does not re-derive them.

---

## Update protocol

The SLA table and the read-contract decisions are intended to be stable; they
were pinned in the C1 PRD before any C1 code existed. To change them:

1. **For an SLA value:** establish the new real-world availability (ideally a
   live-measured pull, see `C1-M1-MEASURE`), open a PR updating both this doc
   *and* the single C1-M3 module constant in the same change (the drift
   contract keeps them in lock-step), and add a new ledger entry recording the
   revision.
2. **For a read-contract decision** (processed-only, as-of semantics,
   weekend/holiday, FRED parity): these are correctness invariants the G1/G2
   gates depend on. Changing one requires a PRD revision and re-running the
   C1-M2 G1/G2 gates, not an ad-hoc edit.
3. **When a new ingestor/source is added:** audit its real-world availability
   here, add an SLA row + the matching module constant entry, and screen it
   for any revision behaviour (the FRED CPI/UNRATE lesson) before it joins the
   feature path.

Do not loosen an SLA to silence an alert: a late feed is the condition C1-M3
*exists to surface*, not a threshold to tune away.

---

## References

- C1 PRD — [`.claude/prds/c1-live-data.prd.md`](../../.claude/prds/c1-live-data.prd.md)
  (Problem, Evidence, the pre-committed SLA table and G1/G2/G3 gates).
- ROADMAP §2 (research-ready / deployment-empty), §7 (C1 same-day pipeline,
  daily cadence), §8 (ratified decisions) —
  [`docs/PROJECT_ROADMAP.md`](../PROJECT_ROADMAP.md).
- METHODOLOGY §1 (pre-committed thresholds), §4 (contract before consumer),
  §6 (drift contracts), §9 (declared deviations) —
  [`docs/METHODOLOGY.md`](../METHODOLOGY.md).
- IEX daily bar settlement — Alpaca Market Data (free IEX feed).
- Tiingo EOD adjusted prices — https://www.tiingo.com/documentation/end-of-day
- SEC EDGAR submissions API & 8-K timing —
  https://www.sec.gov/os/accessing-edgar-data

---

*Sister documents:
[fred-publication-lag.md](fred-publication-lag.md) — the per-series macro
lags this SLA reuses for FRED freshness and parity.
[purging-and-embargo.md](purging-and-embargo.md) — the label-side leakage
controls; this doc is the read-side (feature/serving) PIT discipline.
[feature-glossary.md](feature-glossary.md) — definitions of the features the
same-day reader assembles.*

*Status: ACTIVE (C1-M1, 2026-06-27) — the freshness SLA table and the
processed-only / as-of-instant / weekend-holiday read decisions are frozen.
The C1-M3 monitor and the C1-M2 reader implement against this contract;
changes follow the update protocol above (PRD revision + ledger entry).*
