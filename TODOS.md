# TODOS

Items considered during Phase 2 engineering review and deferred.
Each entry has enough context to be picked up independently.

---

## T1 — Expand universe to S&P 500+ constituents

**What:** Replace the current 10-stock basket (AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, SPY, QQQ, IWM) with a broader, point-in-time correct universe.

**Why:** The current 10 stocks are highly correlated mega-caps plus their ETF wrappers (~2-3 effective degrees of freedom). Cross-sectional ranking, sample uniqueness weighting, and portfolio-level statistics are all statistically meaningful only at 100-500+ names. Current universe results are dominated by tech-beta, not alpha.

**Pros:** Cross-sectional alpha signals become meaningful; portfolio harness produces reliable statistics; graduate from "tech-basket" to a real quant universe.

**Cons:** Survivorship-bias-free constituent history requires a paid data source (Polygon S&P constituent history, ~$29/mo, or CRSP if institutional access is available). Significant data engineering work.

**Context:** Phase 2 is explicitly foundational — the portfolio harness, feature pipeline, and model wrappers are designed to scale. This TODO is the Phase 3 data dependency. The design doc acknowledges survivorship bias as a known limitation. Don't expand until Phase 2 validates that the gradient-boost approach produces any signal on the current basket.

**Depends on:** Phase 2 exit gate cleared; data subscription decision.

---

## T2 — Data-derived embargo length (replace fixed constant with ACF-estimated h)

**What:** Estimate the embargo length `h` from the sample autocorrelation function (ACF) of the label and feature processes, rather than using a fixed conservative constant.

**Why:** The current fixed embargo is correct and safe, but potentially over-conservative — it costs training data that might not need to be embargoed. The ACF/mixing-coefficient decay of the specific features and labels used in Phase 2 may allow a shorter embargo than the current constant, freeing data without reintroducing bias.

**Pros:** More data-efficient; embargo becomes adaptive to the actual feature lookback used; aligns with López de Prado's full recommendation.

**Cons:** Requires implementing ACF estimation and a calibrated threshold; adds complexity to the split generator; risk of under-embargoing if estimation is wrong.

**Context:** Explicitly deferred in `docs/concepts/purging-and-embargo.md` §7. Only do this after Phase 2 features are finalized and the maximum feature lookback window is known. The fixed conservative constant is adequate in the meantime.

**Depends on:** Phase 2 feature set finalized.

---

## T3 — Combinatorial Purged Cross-Validation (CPCV)

**What:** Implement CPCV (López de Prado, AFML ch. 12) as an alternative to walk-forward CV to produce a distribution of OOS Sharpe ratios rather than a single point estimate.

**Why:** Walk-forward gives one OOS Sharpe. CPCV gives a distribution (mean, variance, regime breakdown), which is what you need to answer "is this edge stable across different market regimes?" It also amortizes the data cost of purging/embargo across many path combinations, making evaluation more data-efficient.

**Pros:** Distribution of outcomes reveals regime dependence; more data-efficient than a single walk-forward path; stronger statistical inference.

**Cons:** Significantly more complex to implement and debug; multiple OOS paths share data (not independent); interpretation requires care.

**Context:** The `walkforward.py` interface is already designed to be CPCV-ready. Do this after Phase 2 validates that there's signal worth investigating further.

**Depends on:** Phase 2 exit gate cleared.

---

## T4 — Universe expansion data sourcing decision

**What:** Evaluate and select a point-in-time-correct universe data source for Phase 3+: Polygon.io S&P constituent history (~$29/mo), CRSP (institutional), or EDGAR-based reconstruction.

**Why:** See T1. Data source decision has a 1-month lead time. Decide early so it doesn't block Phase 3 start.

**Pros:** Unblocks T1 and Phase 3.

**Cons:** Ongoing subscription cost (~$29/mo for Polygon).

**Context:** Polygon is the realistic path for a solo operator. CRSP requires institutional affiliation. EDGAR fundamentals don't reliably cover historical constituency.

**Depends on:** Phase 2 exit gate cleared.
