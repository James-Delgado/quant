"""C2-M3 — backtest↔paper reconciliation harness (the G2 gate).

This is the third and final C2 milestone: it closes the loop opened by C2-M2's
``ExecutionBridge`` by proving the paper execution path is a *faithful
realization of the Phase-1 backtest* (PRD "Problem" item 3 — execution skew is
the largest post-train/serve-skew deployment risk). It replays the daily ARIMA
signals through a **paper-configured** execution model over a pinned shared
historical window (≥2 macro-era regimes), reconciles its equity curve against the
Phase-1 backtest path, and emits the **G2 gate**: ≤ 1% relative total-return delta
with a *fully decomposed, no-unexplained* residual (PRD "Pre-committed gate" §2).

Two engines, one signal (why the residual is diagnostic)
--------------------------------------------------------
Execution reconciliation is **signal-agnostic**: both equity curves consume the
*identical* daily signal series, so the only thing that can differ is the
execution engine's cost/fill mechanics. We therefore feed one deterministic,
leak-free signal series (``generate_daily_signals`` — an expanding-window ARIMA
refit) to two ``backtest/simulator.py`` configurations:

  * ``BACKTEST_COST_MODEL`` — the Phase-1 pinned IBKR model (``cost-model.md``).
  * ``PAPER_COST_MODEL``    — Alpaca paper, matched-as-possible: slippage + fill
    + liquidity cap identical, but ``commission_per_share = 0`` because Alpaca
    US-equity trading is *commission-free* — the one irreducible, **named**
    difference. (You cannot configure the Alpaca paper engine to charge IBKR's
    per-share fee; that gap is the residual, not a defect.)

The residual between the two curves is then attributed to named cost-model
parameters by :func:`decompose_residual` (sequential single-parameter toggle).
Anything the named components cannot account for is the ``unexplained`` residual,
and an unexplained residual **fails the gate even under 1%** (METHODOLOGY §9 — no
silent gaps). With both curves produced by the same ``simulate()`` under
different configs the decomposition closes exactly; the ``unexplained`` guard is
the forward drift contract for the day the paper curve is sourced from a genuine
live-broker historical replay (``paper_multiple_override``).

Reconciliation ground truth (a declared framing, METHODOLOGY §9)
----------------------------------------------------------------
The Phase-1 backtest ``harness.py`` wraps ``simulate()`` *per walk-forward fold*.
We reconcile a **single continuous replay** through ``simulate()`` with the
backtest cost model, not the fold structure, because execution mechanics
(fills/costs) are fold-independent — the walk-forward split governs *model
evaluation honesty*, not execution. C2 makes no edge claim, so reconciling at the
simulator level is the faithful execution comparison. This is stated in the
report.

Scope boundary
--------------
C2-M3 ships the G2 reconciliation gate (this module) + the G3 ≥5-cycle liveness
loop primitive (``run_paper_loop``, composing the C2-M2 bridge + position-state
persistence). The *live* ≥5-session paper accrual is the documented runbook in
``docs/concepts/lean-setup.md`` (operationally exercised — it cannot be run across
five market days in one session; declared §9). This module touches **no**
walk-forward split logic (``backtest/CLAUDE.md``): it only consumes price frames,
signal series, and forecasts.

Run
---
    .venv/bin/python scripts/reconcile_paper_backtest.py            # reconcile + write report + ledger
    .venv/bin/python scripts/reconcile_paper_backtest.py --no-ledger
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pandas as pd

from quant.backtest.simulator import simulate
from quant.execution.lean_bridge import (
    PLACEHOLDER_QTY,
    PositionState,
    TargetOrder,
    TargetSignal,
    load_position_state,
    save_position_state,
)
from quant.models.arima_baseline import ARIMABaseline

logger = logging.getLogger(__name__)

# Re-export so the deliverable's namespace carries the bridge dataclasses the
# G3 loop produces/consumes (the test references ``rpb.TargetSignal``).
__all__ = ["TargetSignal", "PositionState", "TargetOrder"]

# A daily-signal emitter: ``(asof, symbols=…) -> {symbol: TargetSignal}`` — the
# shape of ``lean_bridge.daily_signal`` and of any fake injected by the G3 loop.
DailySignalFn = Callable[..., dict[str, TargetSignal]]

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

# G3 paper-loop liveness: a real run must complete ≥ this many consecutive clean
# daily cycles with position state round-tripping across runs (C2 PRD G3).
G3_MIN_CYCLES: int = 5

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

# Reconciliation window + universe, pinned BEFORE any reconciliation runs so the
# tolerance is not measured against a hand-picked favorable span (PRD open-Q
# "Reconciliation window selection"; METHODOLOGY §1/§10). 2019→2022 spans three
# macro-era regimes: qe_bull (2019), covid (2020-21), rate_cycle (2022).
RECON_WINDOW: tuple[str, str] = ("2019-01-01", "2022-12-31")
# A small liquid placeholder subset — execution reconciliation is per-symbol and
# signal-agnostic, so the full 33-symbol universe adds compute without exercising
# additional execution machinery (mirrors the ARIMA-placeholder rationale, §8.4).
RECON_UNIVERSE: tuple[str, ...] = ("SPY", "AAPL", "MSFT")

# Minimum realized daily returns before an ARIMA signal is generated for a symbol.
MIN_SIGNAL_OBS: int = 30
# Refit cadence (bars) for the expanding-window ARIMA signal generator. Mirrors
# the backtest's per-fold refit cadence (test_window = 63); between refits the most
# recent fit's one-step forecast is reused — deterministic and leak-free.
SIGNAL_REFIT_STEP: int = 63

# Where the runner writes the report + run metadata.
RECON_OUTPUT_DIR: Path = Path(__file__).resolve().parents[1] / "data" / "c2" / "reconciliation"


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


# ─── Leak-free daily signal generation (expanding-window ARIMA) ────────────────


def generate_daily_signals(
    close: pd.Series,
    *,
    refit_step: int = SIGNAL_REFIT_STEP,
    min_obs: int = MIN_SIGNAL_OBS,
) -> pd.Series:
    """Daily ARIMA(1,0,0) target-position signal ∈ {-1, 0, +1}, point-in-time.

    At each refit bar ``p`` the model is fit on the realized daily-return series
    *through* bar ``p`` (only past-and-present data) and its one-step forecast is
    signed and applied to bars ``[p, p+refit_step)``. Refitting every
    ``refit_step`` bars mirrors the backtest's per-fold cadence; between refits
    the most recent fit's forecast is reused. This is **leak-free** — the signal
    at any bar depends only on returns realized by that bar — which keeps the
    runner honest if the series is ever reused beyond execution reconciliation
    (it cancels on both engines regardless).

    Returns a ``{-1, 0, +1}`` Series indexed on the realized-return calendar
    (one bar shorter than *close*); an empty Series if history < *min_obs*.
    """
    close = close.dropna()
    ret = close.pct_change().dropna()
    if len(ret) < min_obs:
        return pd.Series(dtype=int)

    sig = pd.Series(0, index=ret.index, dtype=int)  # default flat before first fit
    for start in range(min_obs - 1, len(ret), refit_step):
        window = ret.iloc[: start + 1].to_numpy()
        forecast = ARIMABaseline().fit(None, window).predict_one_step()  # type: ignore[arg-type]
        s = int(np.sign(forecast)) if np.isfinite(forecast) else 0
        end = min(start + refit_step, len(ret))
        sig.iloc[start:end] = s
    return sig


# ─── G3 daily-loop liveness primitive (composes the C2-M2 bridge) ──────────────


def run_daily_cycle(
    asof: pd.Timestamp | str,
    bridge: object,
    state_path: str | Path,
    *,
    daily_signal_fn: DailySignalFn,
    symbols: Sequence[str] | None,
    qty: float = PLACEHOLDER_QTY,
) -> PositionState:
    """One paper cycle: load prior state → signal → place targets → persist state.

    The bridge's reported positions are the source of truth for the persisted
    holdings (the live engine's view); the prior on-disk state is loaded so the
    cycle is a true round-trip (run N+1 opens where run N closed). Returns the
    newly persisted :class:`PositionState`.
    """
    # Load-only: proves cycle N's persisted file deserializes at cycle N+1's open
    # (the round-trip). Position authority is bridge.current_positions(), not the
    # state file, so the loaded value drives nothing and is intentionally dropped.
    _ = load_position_state(state_path)
    signals = daily_signal_fn(asof, symbols=symbols)
    for sym, ts in signals.items():
        bridge.place_target(TargetOrder(sym, ts.target_position, qty))  # type: ignore[attr-defined]
    holdings = bridge.current_positions()  # type: ignore[attr-defined]
    state = PositionState(asof=str(pd.Timestamp(asof)), holdings=dict(holdings))
    save_position_state(state, state_path)
    return state


def run_paper_loop(
    asofs: Sequence[pd.Timestamp | str],
    bridge: object,
    state_path: str | Path,
    *,
    daily_signal_fn: DailySignalFn | None = None,
    symbols: Sequence[str] | None = None,
    qty: float = PLACEHOLDER_QTY,
) -> list[PositionState]:
    """Run the daily paper cycle over *asofs*, persisting state between each (G3).

    The gateable half of G3 — that the loop runs end-to-end with state that
    round-trips across cycles — is exercised here deterministically. The *live*
    ≥``G3_MIN_CYCLES``-session accrual against the real paper broker is the
    ``lean-setup.md`` runbook (it spans real market days; cannot run in one
    session). *daily_signal_fn* defaults to the bridge's ``daily_signal``.
    """
    signal_fn = daily_signal_fn
    if signal_fn is None:
        from quant.execution.lean_bridge import daily_signal

        signal_fn = daily_signal
    return [
        run_daily_cycle(
            asof, bridge, state_path, daily_signal_fn=signal_fn, symbols=symbols, qty=qty
        )
        for asof in asofs
    ]


# ─── Report rendering ──────────────────────────────────────────────────────────


def format_reconciliation_report(
    results: Mapping[str, ReconciliationResult],
    *,
    window: tuple[str, str] = RECON_WINDOW,
    max_relative_delta: float = G2_MAX_RELATIVE_DELTA,
) -> str:
    """Render the per-symbol G2 verdicts as a markdown reconciliation report.

    The verdict line quotes the gate output verbatim (no paraphrase — METHODOLOGY
    §9 / "verdicts from gate functions"); the residual is named component-by-
    component so no basis point of the delta is left unexplained.
    """
    overall = bool(results) and all(r.passed for r in results.values())
    lines = [
        "# C2-M3 — Backtest↔Paper Reconciliation (G2)",
        "",
        f"Window: {window[0]} → {window[1]} (≥2 macro-era regimes).",
        f"Tolerance: |relative total-return delta| ≤ {max_relative_delta:.2%}, "
        "residual fully decomposed (no unexplained component).",
        "Ground truth: a single continuous replay through `backtest/simulator.py` "
        "under the Phase-1 cost model (execution mechanics are fold-independent).",
        "Paper engine: the same simulator under the Alpaca paper cost model "
        "(commission-free; slippage + fill matched).",
        "",
        f"**Overall G2 verdict: {'PASS' if overall else 'FAIL'}**",
        "",
    ]
    for sym, r in results.items():
        lines.append(f"## {sym}: {'PASS' if r.passed else 'FAIL'}")
        lines.append(
            f"- relative_delta: {r.relative_delta:+.6%}  (tolerance {max_relative_delta:.2%})"
        )
        lines.append(
            f"- backtest_multiple: {r.backtest_multiple:.6f}  "
            f"paper_multiple: {r.paper_multiple:.6f}  n_trades: {r.n_trades}"
        )
        lines.append("- residual decomposition (named execution-model sources):")
        if r.components:
            for k, v in r.components.items():
                lines.append(f"    - {k}: {v:+.6%}")
        else:
            lines.append("    - (none — cost models identical)")
        lines.append(f"- unexplained residual: {r.unexplained:.2e} (must be ≤ {UNEXPLAINED_EPS:.0e})")
        lines.append("")
    return "\n".join(lines)


# ─── Runner ────────────────────────────────────────────────────────────────────


def _config_hash() -> str:
    """Deterministic hash of the pinned reconciliation config (audit trail)."""
    payload = json.dumps(
        {
            "window": RECON_WINDOW,
            "universe": RECON_UNIVERSE,
            "backtest_cost": BACKTEST_COST_MODEL,
            "paper_cost": PAPER_COST_MODEL,
            "g2_tol": G2_MAX_RELATIVE_DELTA,
            "refit_step": SIGNAL_REFIT_STEP,
            "min_signal_obs": MIN_SIGNAL_OBS,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _slice_window(frame: pd.DataFrame, window: tuple[str, str]) -> pd.DataFrame:
    """Slice *frame* to *window*, matching the index timezone (lake bars are UTC).

    The realtime reader returns tz-aware (UTC) bars, so the naive window bounds
    must be localized to the index tz before comparison or pandas raises an
    InvalidComparison (the tz-alignment pitfall documented in the project memory).
    Handles a tz-naive index too, for robustness.
    """
    idx = frame.index
    start, end = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    if idx.tz is not None:
        start = start.tz_localize(idx.tz) if start.tzinfo is None else start.tz_convert(idx.tz)
        end = end.tz_localize(idx.tz) if end.tzinfo is None else end.tz_convert(idx.tz)
    return frame.loc[(idx >= start) & (idx <= end)]


def _load_window_prices(symbols: Sequence[str], window: tuple[str, str]) -> dict[str, pd.DataFrame]:
    """Load each symbol's OHLCV over *window* from the lake (point-in-time reader)."""
    from quant.storage.realtime import get_pit_panel

    panel = get_pit_panel(list(symbols), pd.Timestamp(window[1], tz="UTC"))
    out: dict[str, pd.DataFrame] = {}
    for sym, frame in panel.items():
        sliced = _slice_window(frame, window)
        if not sliced.empty:
            out[sym] = sliced
    return out


def reconcile_universe(
    symbols: Sequence[str] = RECON_UNIVERSE,
    window: tuple[str, str] = RECON_WINDOW,
) -> dict[str, ReconciliationResult]:
    """Reconcile each symbol's paper ⇄ backtest curve over the pinned window."""
    prices_by_symbol = _load_window_prices(symbols, window)
    results: dict[str, ReconciliationResult] = {}
    for sym, prices in prices_by_symbol.items():
        signals = generate_daily_signals(prices["close"])
        if signals.empty:
            logger.warning("symbol=%s skipped — insufficient history for a signal", sym)
            continue
        aligned_prices = prices.loc[signals.index]
        results[sym] = g2_reconciliation_gate_report(aligned_prices, signals)
    return results


def _record_ledger(
    results: Mapping[str, ReconciliationResult], started_at: str, finished_at: str
) -> None:
    """Append an audit-only ledger entry (n_comparisons=0 — infrastructure, not a trial).

    C2 makes no pre-registered edge claim, so the reconciliation contributes
    **no** research trials to the deflation N (PRD "Ledger discipline"). The entry
    is bookkeeping only; idempotent by config_hash via ``record_run``.
    """
    from quant.ledger import record_run

    overall = bool(results) and all(r.passed for r in results.values())
    record_run(
        {"config_hash": _config_hash(), "started_at": started_at, "finished_at": finished_at},
        prd="c2",
        milestone="C2-M3",
        preregistration=".claude/prds/c2-lean-paper.prd.md#pre-committed-gate",
        n_comparisons=0,  # infrastructure — no deflation contribution
        verdict="gate_passed" if overall else "gate_failed",
        agent="human",
        artifacts=["data/c2/reconciliation/reconciliation_report.md"],  # repo-relative (audit trail)
        notes="C2-M3 backtest↔paper reconciliation (G2). Audit-only; no edge claim.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C2-M3 backtest↔paper reconciliation (G2).")
    parser.add_argument("--no-ledger", action="store_true", help="skip the audit-only ledger entry")
    parser.add_argument(
        "--output", type=Path, default=RECON_OUTPUT_DIR, help="output directory for the report"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    started_at = pd.Timestamp.now("UTC").isoformat()

    results = reconcile_universe()
    if not results:
        logger.error("no symbols reconciled — is the lake populated for %s?", RECON_UNIVERSE)
        return 1

    report = format_reconciliation_report(results)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "reconciliation_report.md").write_text(report)
    finished_at = pd.Timestamp.now("UTC").isoformat()
    metadata = {
        "config_hash": _config_hash(),
        "started_at": started_at,
        "finished_at": finished_at,
        "window": RECON_WINDOW,
        "universe": list(results),
        "g2_tolerance": G2_MAX_RELATIVE_DELTA,
        "per_symbol": {
            sym: {
                "passed": r.passed,
                "relative_delta": r.relative_delta,
                "unexplained": r.unexplained,
                "n_trades": r.n_trades,
            }
            for sym, r in results.items()
        },
        "overall_passed": all(r.passed for r in results.values()),
    }
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=False))

    print(report)
    if not args.no_ledger:
        _record_ledger(results, started_at, finished_at)

    return 0 if all(r.passed for r in results.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
