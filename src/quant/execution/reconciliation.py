"""Backtest↔paper reconciliation core — the G2 gate + residual decomposition.

This is the reusable arithmetic CORE of the C2-M3 reconciliation harness, lifted
out of ``scripts/reconcile_paper_backtest.py`` so both the CLI runner and the E3
Live-Monitoring console (ROADMAP §4 Project E — "paper-vs-backtest reconciliation"
over a tested ``src/quant/`` service layer) import ONE tested implementation with
no duplicated arithmetic. The script remains the deliverable CLI entry point and a
thin consumer of this module; the per-symbol/window orchestration, signal
generation, report rendering, ledger write, and the G3 paper-loop primitive stay
there.

Two engines, one signal (why the residual is diagnostic)
--------------------------------------------------------
Execution reconciliation is **signal-agnostic**: both equity curves consume the
*identical* daily signal series, so the only thing that can differ is the
execution engine's cost/fill mechanics. The runner feeds one deterministic,
leak-free signal series to two ``backtest/simulator.py`` configurations:

  * ``BACKTEST_COST_MODEL`` — the Phase-1 pinned IBKR model (``cost-model.md``).
  * ``PAPER_COST_MODEL``    — Alpaca paper, matched-as-possible: slippage + fill
    + liquidity cap identical, but ``commission_per_share = 0`` because Alpaca
    US-equity trading is *commission-free* — the one irreducible, **named**
    difference. (You cannot configure the Alpaca paper engine to charge IBKR's
    per-share fee; that gap is the residual, not a defect.)

The residual between the two curves is attributed to named cost-model parameters
by :func:`decompose_residual` (sequential single-parameter toggle). Anything the
named components cannot account for is the ``unexplained`` residual, and an
unexplained residual **fails the gate even under 1%** (METHODOLOGY §9 — no silent
gaps). With both curves produced by the same ``simulate()`` under different configs
the decomposition closes exactly; the ``unexplained`` guard is the forward drift
contract for the day the paper curve is sourced from a genuine live-broker
historical replay (``paper_multiple_override``).

Reconciliation ground truth (a declared framing, METHODOLOGY §9)
----------------------------------------------------------------
The Phase-1 backtest ``harness.py`` wraps ``simulate()`` *per walk-forward fold*.
We reconcile a **single continuous replay** through ``simulate()`` with the
backtest cost model, not the fold structure, because execution mechanics
(fills/costs) are fold-independent — the walk-forward split governs *model
evaluation honesty*, not execution. C2 makes no edge claim, so reconciling at the
simulator level is the faithful execution comparison. This module touches **no**
walk-forward split logic (``backtest/CLAUDE.md``): it only consumes price frames
and signal series.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np
import pandas as pd

from quant.backtest.simulator import simulate

__all__ = [
    "G2_MAX_RELATIVE_DELTA",
    "UNEXPLAINED_EPS",
    "BACKTEST_COST_MODEL",
    "PAPER_COST_MODEL",
    "ReconciliationResult",
    "equity_curve",
    "growth_multiple",
    "relative_delta",
    "decompose_residual",
    "g2_reconciliation_gate_report",
]

# ─── Pinned constants (METHODOLOGY §1/§2 — the code is the source of truth) ─────
# G2 reconciliation tolerance: the relative per-period total-return delta between
# the paper and backtest equity curves must be ≤ this to pass (C2 PRD G2 / ROADMAP
# §7 "any >1% delta investigated"). It lives in ONE constant consumed by the gate
# and its tests under a drift contract (METHODOLOGY §6); changing it after a result
# is visible invalidates the run and requires a PRD revision + a new ledger entry.
G2_MAX_RELATIVE_DELTA: float = 0.01

# The decomposed residual must reconstruct the full backtest→paper multiple gap to
# within this absolute (multiple-space) tolerance; anything larger is an
# *unexplained* residual and fails the gate even under 1% (METHODOLOGY §9).
UNEXPLAINED_EPS: float = 1e-9

# The Phase-1 pinned cost model (docs/concepts/cost-model.md) — the reconciliation
# ground truth. The paper model matches it on every axis EXCEPT commission: Alpaca
# US-equity trading is commission-free, the single irreducible (named) difference.
BACKTEST_COST_MODEL: dict[str, float] = {
    "commission_per_share": 0.005,
    "slippage_bps": 5.0,
    "liquidity_cap": 0.10,
}
PAPER_COST_MODEL: dict[str, float] = {
    "commission_per_share": 0.0,  # Alpaca equities are commission-free
    "slippage_bps": 5.0,  # matched
    "liquidity_cap": 0.10,  # matched
}

# The order in which cost parameters are toggled backtest→paper when decomposing
# the residual. Fixed so the decomposition is deterministic (METHODOLOGY §1).
_COST_PARAM_ORDER: tuple[str, ...] = ("commission_per_share", "slippage_bps", "liquidity_cap")


# ─── Equity-curve construction + reconciliation arithmetic ─────────────────────


def _validate_aligned(prices: pd.DataFrame, signals: pd.Series) -> None:
    if not prices.index.equals(signals.index):
        raise ValueError(
            "prices and signals must have aligned (identical) indexes — "
            "slice them to a common window before reconciling"
        )


def _simulate(
    prices: pd.DataFrame, signals: pd.Series, cost_model: Mapping[str, float]
) -> tuple[pd.Series, pd.DataFrame]:
    """Run the Phase-1 simulator under *cost_model*; return (equity, trade_log)."""
    return simulate(prices, signals, **cost_model)


def equity_curve(
    prices: pd.DataFrame, signals: pd.Series, cost_model: Mapping[str, float]
) -> pd.Series:
    """Equity curve from replaying *signals* on *prices* under *cost_model*.

    Thin wrapper over ``backtest/simulator.py::simulate`` — the SAME engine the
    Phase-1 backtest uses, so the only difference between the backtest and paper
    curves is the cost model passed here (the "two engines, one signal" design).
    """
    _validate_aligned(prices, signals)
    eq, _ = _simulate(prices, signals, cost_model)
    return eq


def growth_multiple(equity: pd.Series) -> float:
    """Terminal/initial equity ratio (1.0 for an empty curve — no growth)."""
    if len(equity) == 0:
        return 1.0
    first = float(equity.iloc[0])
    if first == 0.0:
        return float("nan")
    return float(equity.iloc[-1]) / first


def relative_delta(backtest_multiple: float, paper_multiple: float) -> float:
    """Relative difference of the paper growth multiple vs the backtest's.

    ``paper / backtest − 1``: positive when paper out-grows the backtest (e.g.
    commission-free paper beating IBKR-cost backtest). Robust because the
    denominator is a growth multiple (~1), never the near-zero total return.
    """
    if backtest_multiple == 0.0:
        return float("nan")
    return paper_multiple / backtest_multiple - 1.0


def decompose_residual(
    prices: pd.DataFrame,
    signals: pd.Series,
    *,
    backtest_cost: Mapping[str, float],
    paper_cost: Mapping[str, float],
) -> dict[str, float]:
    """Attribute the backtest→paper multiple gap to named cost parameters.

    Walks ``_COST_PARAM_ORDER``, toggling each parameter that differs between the
    two models from its backtest value to its paper value (cumulatively), and
    records the marginal relative change in growth multiple it causes:
    ``component[k] = m_after / m_before − 1``. The components compose
    multiplicatively to exactly the full gap (telescoping), so the gate's
    ``unexplained`` residual is zero by construction here — and non-zero only if
    the paper multiple is later sourced from an *independent* engine (the forward
    drift contract). Parameters with equal values contribute no component.
    """
    differing = [k for k in _COST_PARAM_ORDER if backtest_cost.get(k) != paper_cost.get(k)]
    components: dict[str, float] = {}
    cfg = dict(backtest_cost)
    prev_mult = growth_multiple(equity_curve(prices, signals, cfg))
    for k in differing:
        cfg = {**cfg, k: paper_cost[k]}
        mult = growth_multiple(equity_curve(prices, signals, cfg))
        components[k] = (mult / prev_mult - 1.0) if prev_mult != 0.0 else float("nan")
        prev_mult = mult
    return components


@dataclass(frozen=True)
class ReconciliationResult:
    """Verdict of the G2 backtest↔paper reconciliation gate for one symbol."""

    backtest_multiple: float
    paper_multiple: float
    relative_delta: float
    components: Mapping[str, float]  # read-only view — the verdict must not be mutated
    unexplained: float
    n_trades: int
    passed: bool


def g2_reconciliation_gate_report(
    prices: pd.DataFrame,
    signals: pd.Series,
    *,
    backtest_cost: Mapping[str, float] = BACKTEST_COST_MODEL,
    paper_cost: Mapping[str, float] = PAPER_COST_MODEL,
    max_relative_delta: float = G2_MAX_RELATIVE_DELTA,
    unexplained_eps: float = UNEXPLAINED_EPS,
    paper_multiple_override: float | None = None,
) -> ReconciliationResult:
    """G2: paper ⇄ backtest reconcile to ≤ *max_relative_delta*, residual decomposed.

    A PASS requires all three (PRD "Pre-committed gate" §2):
      * a non-empty reconciliation surface (the backtest replay placed ≥ 1 trade —
        flat-forever signals have nothing to reconcile),
      * ``|relative_delta| ≤ max_relative_delta`` (the pinned 1% tolerance), AND
      * ``|unexplained| ≤ unexplained_eps`` — the named components fully account
        for the gap (an unexplained residual fails even under 1%, METHODOLOGY §9).

    *paper_multiple_override* injects a paper growth multiple from an independent
    source (a live-broker replay); when set, the decomposition no longer closes
    the gap and any remainder surfaces as ``unexplained``.
    """
    _validate_aligned(prices, signals)
    bt_eq, bt_log = _simulate(prices, signals, backtest_cost)
    bt_mult = growth_multiple(bt_eq)

    if paper_multiple_override is None:
        paper_mult = growth_multiple(equity_curve(prices, signals, paper_cost))
    else:
        paper_mult = float(paper_multiple_override)

    components = decompose_residual(
        prices, signals, backtest_cost=backtest_cost, paper_cost=paper_cost
    )
    reconstructed = bt_mult
    for c in components.values():
        reconstructed *= 1.0 + c
    unexplained = paper_mult - reconstructed
    rel = relative_delta(bt_mult, paper_mult)
    n_trades = int(len(bt_log))

    passed = (
        n_trades > 0
        and np.isfinite(rel)
        and abs(rel) <= max_relative_delta
        and abs(unexplained) <= unexplained_eps
    )
    return ReconciliationResult(
        backtest_multiple=bt_mult,
        paper_multiple=paper_mult,
        relative_delta=rel,
        components=MappingProxyType(components),  # frozen verdict — no post-hoc mutation
        unexplained=float(unexplained),
        n_trades=n_trades,
        passed=bool(passed),
    )
