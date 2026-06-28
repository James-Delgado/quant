# The Data-Freshness Monitor (C1-M3)

> **Living reference.** Operational companion to
> [`data-freshness-slas.md`](data-freshness-slas.md) (the C1-M1 contract this
> monitor enforces). This document is the **C1-M3 deliverable**: how to run
> `scripts/monitor_freshness.py`, what its exit codes mean, the per-source
> staleness predicates it implements, and how to wire it into `cron`. The SLA
> *values* are frozen in the C1-M1 doc and live in **one module constant**
> (`SOURCE_SLAS`) under a drift test — this doc describes the runner, it does
> **not** redefine the contract (METHODOLOGY §2). Update it when the runner's
> CLI, exit-code contract, or cron recipe changes.

---

## Why this exists

A live system that silently trades on yesterday's data because a feed was late
is worse than one that halts and alerts (C1 PRD, Problem §2). The batch lake is
fine for *backtesting* — it reads history at leisure — but same-day inference
needs to know, **before** any market-relevant decision, that every feed is
current. This monitor is the **G3 operational gate**: it flags exactly the stale
feeds, with no false alarm on a feed that is merely on the late edge of its
normal window.

It is the third C1 milestone, building on:

- **C1-M1** — [`data-freshness-slas.md`](data-freshness-slas.md), the pinned
  per-source SLA table (the contract).
- **C1-M2** — [`../../src/quant/storage/realtime.py`](../../src/quant/storage/realtime.py),
  the point-in-time same-day reader (the consumer that trades on fresh data).

---

## Running it

```bash
# Evaluate every feed as of now; exit non-zero if any feed is stale/missing.
.venv/bin/python scripts/monitor_freshness.py

# Replay a past instant (testing, post-mortem of a late-feed day):
.venv/bin/python scripts/monitor_freshness.py --now 2026-06-24T23:30:00Z
```

Example output (a healthy lake):

```
Freshness monitor @ 2026-06-24T23:30:00+00:00
  [   OK] alpaca       latest=2026-06-24   latest 2026-06-24 ≥ required 2026-06-24
  [   OK] tiingo       latest=2026-06-23   latest 2026-06-23 ≥ required 2026-06-23
  [   OK] fred:DGS10   latest=2026-06-22   latest 2026-06-22 ≥ required 2026-06-22
  [   OK] fred:DFF     latest=2026-06-22   latest 2026-06-22 ≥ required 2026-06-22
  [   OK] fred:VIXCLS  latest=2026-06-22   latest 2026-06-22 ≥ required 2026-06-22
  [   OK] edgar        latest=2026-06-23   latest 2026-06-23 ≥ required 2026-06-23
  [   OK] rss          latest=2026-06-24   latest 2026-06-24 ≥ required 2026-06-23
  → ALL FRESH
```

### Exit-code contract (the alert channel)

| Exit | Meaning |
|---|---|
| `0` | every monitored feed is **fresh** |
| `1` | at least one feed is **stale** or **missing** |

The MVP delivery channel is **exit-code + stderr** (C1 PRD Open-Question "Monitor
delivery channel"): on any alert the report is written to **both stdout and
stderr**, so `cron`'s default mail-on-stderr surfaces it without extra wiring. A
richer channel (email/Slack/dashboard) is a **C5-monitoring** concern, flagged as
a follow-up — not a C1 deliverable.

---

## The monitored feeds and their staleness predicates

Seven feeds are monitored: 2 equity datasets, the 3 **model-relevant** FRED
series (`DGS10`, `DFF`, `VIXCLS` — the set the feature path uses; CPIAUCSL/UNRATE
are deliberately excluded, see C1-M1), and the EDGAR / RSS document streams. Each
feed reports `fresh | stale | missing`. The predicate is deterministic — a feed
is **fresh** iff its latest observation date is on/after the SLA's *required
date*, **missing** iff the dataset has no observation, **stale** otherwise.

| Feed | Dataset | Required-date rule | Source-of-truth constant |
|---|---|---|---|
| `alpaca` | `equity_bars_daily` | most recent trading day `T` whose **23:00 UTC on T** deadline has passed | `ALPACA_DEADLINE_HOUR_UTC=23`, `…DAY_OFFSET=0` |
| `tiingo` | `equity_eod_tiingo` | most recent trading day `T` whose **12:00 UTC on T+1** deadline has passed | `TIINGO_DEADLINE_HOUR_UTC=12`, `…DAY_OFFSET=1` |
| `fred:<s>` | `macro_fred` | **`lag + grace` business days** before now; `lag` from `FRED_PUBLICATION_LAGS[s]` | `engineering.FRED_PUBLICATION_LAGS`, `FRED_GRACE_BDAYS=1` |
| `edgar` | `text_documents` (`source='edgar'`) | scan-liveness: newest filing within **1 trading day** | `EDGAR_MAX_STALE_TRADING_DAYS=1` |
| `rss` | `text_documents` (`source LIKE 'rss%'`) | item-liveness: newest item within **1 calendar day** | `RSS_MAX_STALE_CALENDAR_DAYS=1` |

The price feeds key off the **last trading day** (`utils/calendar.py`), so
weekends and exchange holidays never trip a false "stale" alert — `get_pit_bar`
on a Saturday returns Friday's bar, and so does the monitor's required date.
EDGAR and RSS are **liveness**-checked rather than deadline-checked because both
are event-driven and a quiet period is normal, not stale (C1-M1).

### How the runner is structured (why it is testable)

The staleness decision is a **pure function** — `evaluate_feed(spec, latest,
now)` — that touches no lake. That is the heart the **G3 gate**
(`freshness_gate_report`) exercises on synthetic fresh/stale fixtures:
`(false_stale_count, missed_stale_count) == (0, 0)`. The lake-reading wrappers
(`read_latest`) are thin adapters that feed the pure core the real
`catalog.latest_timestamp` per dataset. This separation is why G3 is checkable on
fixtures without waiting for a real outage (C1 PRD G3).

---

## Cron wiring

The batch ingest flow runs at **22:30 UTC** on weekdays
(`flows/daily.py`). Schedule the monitor **after** the latest feed's SLA so a
genuinely-late feed is the only thing that trips it — the Tiingo `T+1 12:00 UTC`
deadline is the loosest, so a daily check at **13:00 UTC** catches the previous
session's full set:

```cron
# Data-freshness check — 13:00 UTC daily (after Tiingo's T+1 12:00 UTC SLA).
# Non-zero exit + stderr output ⇒ cron emails the operator.
0 13 * * 1-5  cd /Users/jamesdelgado/Projects/quant && .venv/bin/python scripts/monitor_freshness.py
```

For a same-evening price-only check (Alpaca settles 23:00 UTC on T), a second
entry just after the close is reasonable; the monitor is idempotent and
read-only, so it is safe to run as often as desired.

> **Do not loosen an SLA to silence an alert.** A late feed is the condition this
> monitor *exists to surface*, not a threshold to tune away. Changing an SLA
> value is a PRD revision + a new ledger entry that updates **both** the C1-M1
> doc and the module constant in lock-step (the drift contract,
> [`data-freshness-slas.md`](data-freshness-slas.md) "Update protocol").

---

## Declared approximations (METHODOLOGY §9)

- **Business days = NYSE trading days.** The FRED publication-lag arithmetic
  (weekday business days via `numpy`) and the EDGAR scan-liveness window (NYSE
  trading days via `utils/calendar.py`) approximate the U.S. *federal* release
  calendar FRED actually uses, which differs on a few days a year (e.g.
  Columbus/Veterans Day). The one extra business-day grace on the FRED window
  (`FRED_GRACE_BDAYS = 1`) absorbs this and the "occasional 2-business-day
  first-release lag" the C1-M1 audit notes, so the approximation can only cause a
  one-session-late detection, never a false-stale alert.
- **Availability times are the C1-M1 desk-research estimates**, not live-measured
  (`C1-M1-MEASURE` is the follow-up to confirm/tighten them). They are
  deliberately conservative so the monitor does not cry wolf.

---

## References

- C1-M1 SLA contract — [`data-freshness-slas.md`](data-freshness-slas.md).
- C1-M2 PIT reader — [`../../src/quant/storage/realtime.py`](../../src/quant/storage/realtime.py).
- C1 PRD (G3 gate, Open-Question "delivery channel") —
  [`../../.claude/prds/c1-live-data.prd.md`](../../.claude/prds/c1-live-data.prd.md).
- FRED publication lags reused as the parity lever —
  [`fred-publication-lag.md`](fred-publication-lag.md).
- METHODOLOGY §1 (pinned thresholds), §2 (gates in code), §6 (drift contracts),
  §9 (declared deviations) — [`../METHODOLOGY.md`](../METHODOLOGY.md).

---

*Status: ACTIVE (C1-M3, 2026-06-28) — the runner enforces the frozen C1-M1 SLA
table. The pinned SLA values live in `scripts/monitor_freshness.py::SOURCE_SLAS`
under the `tests/test_monitor_freshness.py` drift test; changes follow the C1-M1
update protocol (PRD revision + ledger entry).*
