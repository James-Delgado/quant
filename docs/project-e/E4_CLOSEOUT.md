# E4 Closeout — Data & Market Status

> **Status: COMPLETE.** Project E4 (Data & Market Status) is delivered. The
> end-to-end closeout validation passes: a fresh export from the real lake
> carries live per-ingestor SLA verdicts, lake gap reports, and the live
> market environment; and a **seeded SLA breach fires a real alert through
> the pinned cron channel** — `python -m quant.console alerts` exiting
> non-zero with the breach on stderr. Validated 2026-09-01 on `main`
> (post-E4-ALERTS-CRON-DOC, `415479d`). Companion:
> [`E4-data-market-status.prd.md`](E4-data-market-status.prd.md),
> [`DECISIONS.md`](DECISIONS.md), [`E1_CLOSEOUT.md`](E1_CLOSEOUT.md),
> [`E2_CLOSEOUT.md`](E2_CLOSEOUT.md). Closes `E4-CLOSE`
> (METHODOLOGY §21 / AGENT_OPERATION "Project closeout").

## 1 — What E4 delivered

The live counterpart of E1's static "Data & Market" snapshot: feed health
judged against the pinned C1 SLAs, alerting on breaches, and the live market
environment — all in the existing service layer (`src/quant/console/`) +
console panels; no new datastore.

| Milestone | Delivered |
|---|---|
| **M1** Live feed health + gaps | `DataStatusView` gains per-ingestor **SLA verdicts** (the C1-M3 `monitor_freshness` machinery consumed verbatim — E4 never re-judges freshness, METHODOLOGY §6) + per-dataset **lake gap reports** (missing NYSE sessions inside the observed span; exact counts, honest `n_gaps=None` could-not-check degrade). Rendered as SLA cards + gap pills in `DataMarket.tsx` |
| **M2** Alerting | `console/alerts.py`: four pure producers (staleness / gap / drift / regime-change — the last folded in from superseded C5) composed by `evaluate_alerts`; thresholds **pinned in code** (§1); two severities + materiality windows guard alert fatigue (§10 — recent-gap window 21 sessions, one aggregate drift alert, regime flip = last two observations). Pinned channel: **log/cron** — `python -m quant.console alerts` prints the report, emits to stderr, and exits 1 on any alert (the C1-M3 cron-mail contract); `alerts.json` exported for the console |
| **M3** Live market + drift | `MarketSnapshot` extended live: vol/trend/rates regime labels from the **same labellers as the Conditions panel** (§6), 2s10s curve (DGS2/DGS10), breadth above MA200; every field degrades to `None` + an explanatory note, never a fabricated value (§9). Feature drift extends the E1 lake-backed monitor to the alerting surface (drift producer above). Breadth/2s10s tiles un-hardcoded in `DataMarket.tsx` / `Overview.tsx` |
| **ALERTS-CRON-DOC** | `docs/concepts/freshness-monitor.md` documents the `console alerts` cron wiring (invocation, schedule after the parity-safe Tiingo T+1 bar, `--no-monitor` tradeoff) |

## 2 — Validation method (the closeout "notebook")

Per the E1/E2 precedent, a service+UI project's closeout artifact is a
**scripted end-to-end gate**:
[`scripts/e4_closeout_check.sh`](../../scripts/e4_closeout_check.sh):

```
scripts/e4_closeout_check.sh          # full lake-backed run
FAST=1 scripts/e4_closeout_check.sh   # --no-monitor (skips the ~1-2 min drift panel)
```

Seven stages, exiting non-zero on the first failure: (1) fresh
`console export --check` from real artifacts; (2) `data_status.json` carries
non-empty SLA verdicts (states in the enum) + gap-checked datasets with real
windows; (3) `market.json` regime labels in their enums (or `None` **with** a
degrade note), curve + breadth fields present, `alerts.json` well-formed;
(4) honest **current-state** `console alerts` run (either verdict accepted,
crash fails); (5) **seeded breach** — `console alerts --now <now+30d>`
(horizon pinned in the script) must exit **1** with `SLA breach` lines and the
alert summary on **stderr**, the exact cron-mail delivery contract; (6) drift
check that `freshness-monitor.md` documents the invocation + a crontab line;
(7) frontend build + full test suite, then the console service-layer +
API suites. This crosses the seam the per-milestone tests never do: they
inject sources/fixtures; the gate runs the real CLI over the real lake
through the real exit-code channel.

## 3 — Evidence (this run, 2026-09-01 ~19:55 UTC)

- **Fresh export:** 17 files written; fan-out complete (4 strategies × detail + provenance).
- **E4-M1 live status:** **7 SLA verdicts** — all honestly `stale` (`alpaca`, `tiingo`, `fred:DGS10`, `fred:DFF`, `fred:VIXCLS`, `edgar`, `rss`; the lake was last ingested 2026-08-19/20 and the daily cron is not installed on this machine — the panel reflects real state, METHODOLOGY §9). **2/2 datasets gap-checked**: Daily equity bars 564 gaps (the known historical backlog tracked by `C1-ALPACA-GAP-AUDIT`), Tiingo adjusted EOD 0.
- **E4-M3 live market env:** regimes `low_vol` / `uptrend` / `rates_steady`; 2s10s spread **+0.46 pp**; breadth **84.8% above MA200** (n=33). `alerts.json` well-formed (9 alerts).
- **Current-state alerts (honest):** exit **1** with **9 warnings** — 7 staleness (one per SLA feed), 1 aggregate drift (6 of 27 monitored features), 1 regime-change (`mid_vol → low_vol`, 2026-08-18 → 08-19). Notably **no gap alert despite the 564-gap backlog** — all gaps fall outside the pinned 21-session materiality window, demonstrating the §10 fatigue guard live.
- **Seeded breach:** `--now 2026-10-01T19:55:28+00:00` → exit **1**, staleness breaches delivered on **stderr** with required-dates shifted to the seeded clock (`required 2026-09-29/30` vs `2026-08-28/31` at current time) — the SLA judgment is computed live against the evaluation instant, not cached.
- **Cron doc:** invocation + crontab line present in `freshness-monitor.md`.
- **Frontend:** build green (72.35 kB gz JS); **137 tests pass** (22 files).
- **Service layer:** **222 tests pass** (`tests/test_console.py` + `tests/test_console_api.py`).

## 4 — Definition of done (PRD §7)

- [x] Live feed health reflects real freshness vs SLA (7 live verdicts; same machinery as `GET /health` + the cron monitor).
- [x] Gaps detected (564/0 verified counts over real observed windows; honest could-not-check degrade tested in the suite).
- [x] Breaches alert via the chosen channel (seeded breach → exit 1 + stderr; channel decision recorded in DECISIONS.md).
- [x] Live market environment + feature-drift monitor render (regime/curve/breadth tiles live; drift feeding the aggregate alert).
- [x] Coverage ≥80% (suite-wide console coverage certified in E1; E4 modules landed with their milestone tests inside the 222-test service suite).
- [x] `E4-CLOSE` validation + this report landed.

## 5 — Honesty posture

The alerts cannot disagree with the panels: staleness consumes the C1-M3 SLA
verdicts verbatim, drift consumes the E1 feature-monitor verdicts, and the
regime axis reuses `VIXThresholdDetector` with its pinned 15/25 thresholds —
one source of truth per signal (§6). Every degrade is explicit: unreadable
inputs produce could-not-check notes, never an all-clear (§9). Alerting is
**state-based, not edge-triggered** — a persisting breach re-alerts on every
cron run (as the C1-M3 monitor does); the regime-change message carries both
observation dates so a stale-data flip is self-evidently old.

## 6 — Deviations (declared, METHODOLOGY §9)

- **The seeded breach exercises the staleness producer end-to-end; gap /
  drift / regime-change producers are seeded only in the unit suite.**
  Seeding those through the real lake would require mutating lake data (a
  destructive fixture on production Parquet). Bounded impact: all four
  producers are pure functions over the same view-models the gate validates
  live in stages 2–3, and their seeded-breach acceptance tests run in stage 7;
  in this run drift + regime-change alerts additionally fired live (§3).
- **The live browser render was not re-captured for this closeout** — the
  E4 surfaces render through the same panels certified live in the E1/E2
  closeouts, and the component render tests (137 green, including
  `DataMarket.test.tsx` SLA/gap/market-tile cases) run inside the gate.

## 7 — Deferred / out of scope

All pre-tracked in `PRIORITIES.yaml`; none changes whether live status is
honest or breaches alert:

- `MISC-SCHED-INSTALL` (78) — actually install + verify the operator launchd/cron entries (the wiring is documented; installation is an operator action).
- `E4-ALERTS-PANEL` (146) — render `alerts.json` in the console Data & Market panel.
- `E4-GAP-PER-SYMBOL` (144) / `E4-GAP-ALERT-RECENT-COUNT` (147) / `E4-BREADTH-STALE-SYMBOL` (151) — optional gap/breadth refinements.
- `C1-DGS2-SLA` (150) — whether the 2s10s short leg gets its own SLA row.
- `C1-ALPACA-GAP-AUDIT` (143) — the 564-session historical backlog the gap scan surfaced.
- `E4-CLOSE-CI` (152, appended by this closeout) — wire the FAST gate into CI alongside `E2-CLOSE-CI`.

## 8 — What's next

E4-CLOSE was the last open E4 task; within Project E only **E3 (Live
Monitoring)** remains, still gated on the C3 sizing milestones
(`E3-M1` ← `C3-M1..M3`). Next up on the backlog after this closeout:
`C2-M3-LIVE-REPLAY` (70) and the C6/C3/C4 execution band.
