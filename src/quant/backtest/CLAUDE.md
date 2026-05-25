# Backtester — Agent Instructions

This directory implements the **purged walk-forward backtester** — the
project's evaluation engine. If it is wrong, every model result downstream is
invalid. Treat changes here with corresponding care.

## Hard invariants — cross-validation leakage controls

Any code in this directory that performs train/test splitting **must** preserve
all six of the following:

1. **Purge.** Drop any training sample whose label-evaluation window overlaps
   any test sample's label-evaluation window.
2. **Embargo.** Enforce an additional temporal gap between the training set and
   the test set, beyond label overlap.
3. **Embargo length** ≥ the larger of (a) the maximum feature lookback window
   and (b) the label/feature autocorrelation decay lag. It is currently a
   **fixed conservative constant** — do not under-set it to save data.
4. **Test fold length** must be much greater than `label_horizon + embargo`.
   If a configuration violates this, flag it rather than proceeding — the
   leakage controls would discard most of the training data.
5. **Purging and embargo apply to the backtest/CV path ONLY.** The
   production-refit path trains on all data with realized labels and applies
   no embargo.
6. **Hyperparameter tuning runs inside each walk-forward training window** —
   never across the train/test boundary.

Violating 1–3 **silently inflates backtested performance** — the most
dangerous class of bug in this codebase, because it produces no error, only a
falsely optimistic result. Violating 4–6 wastes data or reintroduces leakage.

## Coupling warning

The label horizon used by the purging logic is **coupled to the label
definition**. If the label definition changes (e.g. horizon, triple-barrier
parameters), the purging logic must be updated to match. They cannot drift
apart.

## Before modifying split logic

Read `docs/concepts/purging-and-embargo.md` first. It contains the rationale,
the covariance/mixing argument, the information-cost analysis, and the full
implementation checklist.

## Harness self-tests

Changes here must keep the harness self-tests passing: a random/no-skill
strategy must yield approximately zero edge net of costs, and an intentionally
leaky strategy must be caught by the purge/embargo controls. If a change breaks
these tests, the change is wrong — not the tests.
