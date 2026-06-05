# Phase 3 — Sentiment Feature (LLM-Derived)

> **Spec document.** See `PROJECT_OVERVIEW.md` for project context,
> `PHASE_1_BACKTESTER.md` for the evaluation harness, and
> `PHASE_2_MODELING.md` for the model this phase augments.

---

## Objective

Add an **LLM-derived sentiment signal** as an additional feature to the Phase 2
model, and determine — by rigorous ablation — whether it produces a
**measurable, robust improvement** in out-of-sample, cost-net, risk-adjusted
performance.

Important framing: LLMs are used here as a **feature source**, not as a
standalone price predictor. The sentiment score becomes one more column in the
feature store feeding the gradient-boosted model. The model still does the
prediction.

---

## Entry gate (prerequisites)

- Phase 2 complete: the gradient-boosted model beats the ARIMA baseline
  out-of-sample, net of costs.
- The feature store and Phase 1 harness are operational.

---

## Scope — what to build

1. A **text ingestor** — news and filings into the data lake.
2. A **FinBERT inference module** — text to sentiment scores.
3. A **sentiment feature** — aggregated, point-in-time-aligned scores.
4. An **ablation evaluation** — the model with vs. without sentiment.

---

## Design detail

### Text sources

All free to start:

- **SEC EDGAR** filings — 8-Ks (material events), 10-K / 10-Q (periodic).
  Official, structured, reliable.
- **News** — free RSS feeds; **GDELT** for broad global news coverage.

The text ingestor follows the **same four-step pattern as the Phase 0
ingestors** (determine range → fetch → land raw immutably → process). Raw text
lands in `data/raw/`; processed sentiment lands in `data/processed/`.

### FinBERT inference

- **FinBERT** — a BERT variant pretrained on financial text. Lightweight;
  inference runs comfortably on the M2 (CPU or MPS). No GPU required.
- Batch documents for throughput.
- Output per document: a sentiment score / probability distribution
  (positive / negative / neutral).

### Sentiment feature construction

- Aggregate document-level scores into a **per-symbol, per-day** sentiment
  feature (e.g. mean and dispersion of scores; volume of coverage).
- **Point-in-time alignment is the critical risk here.** Each document must be
  timestamped by its **publication time**, and the feature for a given trading
  day may only use text published *before* that day's decision point. Using a
  news item before it was public is look-ahead leakage and will manufacture a
  fake edge. Beware data sources that backfill or revise timestamps.

### Integration and evaluation

- Add the sentiment feature as a column in the Phase 2 feature store.
- Retrain the gradient-boosted model **with** and **without** the sentiment
  feature.
- Run **both versions through the Phase 1 harness** and compare. This is an
  **ablation**: the only difference is the sentiment feature, so any
  performance delta is attributable to it.

---

## Deliverables

- The text ingestor (extends the Phase 0 ingestion package).
- The FinBERT inference module.
- The sentiment feature, integrated into the feature store.
- An **ablation report**: model with vs. without sentiment, evaluated
  out-of-sample on risk-adjusted, cost-net metrics.

---

## Exit gate (success criteria)

The sentiment feature is **adopted** only if the ablation shows a
**measurable and robust** improvement in out-of-sample risk-adjusted
performance — robust meaning it holds across the walk-forward folds (and CPCV
distribution), not just on average.

If it does not improve the model, that is a legitimate outcome: **document it
and drop the feature.** Adding a feature that does not help adds noise,
maintenance burden, and overfitting surface.

Clearing this gate (with or without adopting sentiment) marks the end of the
**prototype**. Phase 4 then assesses what to build next.

---

## Risks and pitfalls

- **Publication-time leakage** — the dominant risk. Using text before it was
  public fabricates edge. Audit every timestamp.
- **Edge decay** — news-sentiment edges erode quickly as the same models become
  widely used. A backtested edge may not survive live.
- **Sparse coverage** — many names have little news; the feature will be mostly
  empty for them. Handle missing-ness deliberately.
- **Revised / backfilled news data** — some providers alter historical
  timestamps or content. Prefer sources with stable, original timestamps.

---

## Tooling

`transformers` (FinBERT), `httpx` for fetching, the SEC EDGAR API, GDELT,
the Phase 0 ingestion and storage packages, the Phase 1 harness.

---

## What comes next

Phase 4 assesses the proven prototype and decides whether to build an advanced
model (transformer / foundation model), stand up the execution layer, or
explore the separate Polymarket track.

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 6 issues, 1 critical gap (FinBERT cold-start error handling) |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**UNRESOLVED:** 0 open decisions (D1–D6 all resolved)

**VERDICT:** ENG CLEARED — 6 decisions made, 9 implementation tasks defined, 1 critical gap documented (FinBERT offline error handling). Ready to implement.
