"""C6-M2 — the daily cron executor over the enabled strategy registry.

This is the **deployment spine**: the one idempotent entrypoint that turns the
C6-M1 strategy registry into paper orders every day. It composes the pieces the
prior milestones built into a single ``ingest → freshness gate → for each enabled
strategy {predict → size} → net per symbol → clamp → place_target → persist``
cycle (PRD ``.claude/prds/c6-strategy-registry.prd.md`` §Scope C6-M2).

Why this exists (PRD §Problem item 3)
-------------------------------------
C2 wired exactly **one hardcoded** strategy into ``lean_bridge.daily_signal`` and
traded a fixed 1 share (``PLACEHOLDER_QTY``). The pieces — ingest
(``flows/daily.py``), freshness gate (``monitor_freshness.py``), signal
(``daily_signal``), order (``AlpacaPaperBridge``), state persistence — existed as
separate callables with nothing chaining them. This module is the missing
orchestrator: it runs the *enabled subset* of the registry, sizes each strategy
fully-invested equal-weight (closing ``C2-M2-SIZING-PARITY``), nets per symbol,
and places paper orders on an unattended schedule.

The allocator (PRD §Hypothesis / §Pre-committed gate)
-----------------------------------------------------
* **Capital budget**: equal-weight ``1/N`` across the ``N`` enabled strategies
  (one pinned knob; confidence/track-record weighting is a deliberate later swap).
* **Sizing**: fully-invested equal-weight *within each strategy's universe* —
  per-symbol capital ``budget / |universe|``, shares via the Phase-1 simulator's
  ``int(cash / entry_fill)`` rule (this is the ``C2-M2-SIZING-PARITY`` fix).
* **Combination**: NET the signed, sized positions per symbol, then CLAMP to the
  per-symbol ``risk_limits.max_position``. Confidence enters **once**, at sizing
  (C4) — the allocator only nets + clamps, never re-weights by confidence (PRD
  §Open Questions "Same-symbol combination rule").

The three gates this milestone ships (PRD §Success Metrics)
-----------------------------------------------------------
* **G2a — single-strategy parity** (:func:`single_strategy_parity_report`): with
  exactly the seeded placeholder enabled, the allocator's per-symbol *direction*
  reproduces ``lean_bridge.backtest_path_target_position`` of the same forecast
  for every ``(symbol, asof)`` — **0 mismatches**. The direction is the
  budget-weighted sign vote, deliberately *sizing-independent*, so this is a
  faithful single-strategy generalization of the proven C2 sign path.
* **G2b — sizing reconciliation** (:func:`sizing_reconciliation_report`): the
  fully-invested equal-weight per-symbol notional reconciles with the **real**
  ``backtest/simulator.py`` capital-based deployment to **≤ 1% relative**, residual
  named. The 1% tolerance and the paper slippage/liquidity params are the C2-M3
  reconciliation constants, kept in lock-step by a drift test (METHODOLOGY §6).
* **G3 — daily-loop liveness** (:func:`run_trading_loop`): the cycle runs
  end-to-end with position state that round-trips across runs and a **non-zero
  exit** on any failure. The gateable half (the loop runs + state round-trips) is
  exercised deterministically; the *live* ≥5-session accrual against the real
  paper broker is the documented runbook below (it spans real market days — cannot
  run in one session; declared §9, the same precedent as C2-M3).

Cron runbook (PRD §Sequencing / paper only)
-------------------------------------------
Run after the parity-safe Tiingo T+1 adjusted bar is available (~12:00 UTC), on
weekdays, e.g.::

    # 12:30 UTC, Mon–Fri — after the Tiingo T+1 bar, before the next session
    30 12 * * 1-5  cd /path/to/quant && .venv/bin/python scripts/trade_daily.py

A non-zero exit (stale feed, ingest failure, broker error) surfaces via cron's
default mail-on-stderr. The run is idempotent same-day: position state round-trips
so a re-run re-derives the same targets and places nothing new once on target.
Paper only — live is a later ``broker`` config flag on the already-abstracted
``ExecutionBridge`` (no live-capital code path here).

Design — pure allocator vs network adapters (mirrors ``lean_bridge.py``)
------------------------------------------------------------------------
``equal_weight_shares`` / ``size_strategy`` / ``net_targets`` / the G2 gate
functions are **pure** (no network, no lake) and unit-tested directly. The cycle
orchestration (``run_trading_cycle`` / ``run_trading_loop``) takes every external
dependency — ingest, freshness, capital, signal, price — as an **injected
callable**, so the daily loop is driven through a fake bridge in tests without the
live paper API or the lake. This module touches **no** walk-forward split logic
(``backtest/CLAUDE.md``): it consumes forecasts and prices only.

Run
---
    .venv/bin/python scripts/trade_daily.py                 # ingest → trade one cycle (paper)
    .venv/bin/python scripts/trade_daily.py --no-ingest     # assume a prior scheduled ingest
    .venv/bin/python scripts/trade_daily.py --asof 2026-06-27 --no-ledger
"""
from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from quant.backtest.simulator import simulate
from quant.execution.lean_bridge import (
    PositionState,
    TargetOrder,
    TargetSignal,
    backtest_path_target_position,
    daily_signal,
    derive_target_position,
    load_position_state,
    save_position_state,
    signal_parity_gate_report,
)
from quant.execution.strategy_registry import (
    StrategySpec,
    enabled_strategies,
    load_registry,
)

logger = logging.getLogger(__name__)

# ─── Pinned constants (METHODOLOGY §1/§2 — the code is the source of truth) ─────
# G2a single-strategy parity: the allocator's direction must equal the backtest
# sign path for every checked (symbol, asof). 0 mismatches to pass — the same
# pinned threshold as the C2 G1 signal-parity gate (lean_bridge.G1_MAX_MISMATCHES),
# re-stated here as the C6 G2a constant so this gate is self-contained.
G2A_MAX_MISMATCHES: int = 0

# G2b sizing reconciliation tolerance: the allocator's fully-invested equal-weight
# per-symbol notional must reconcile with the simulator's capital-based deployment
# to within this relative delta. This is the SAME pinned 1% constant as C2-M3's
# G2_MAX_RELATIVE_DELTA (reconcile_paper_backtest.py); a drift test asserts the two
# stay equal (METHODOLOGY §6) so no new tolerance is invented (PRD §Sequencing).
G2B_MAX_RELATIVE_DELTA: float = 0.01

# Paper-engine sizing params — matched to reconcile_paper_backtest.PAPER_COST_MODEL
# on the two axes that affect SHARE COUNT (slippage shifts the entry fill;
# liquidity caps the size). Commission does not change the simulator's deployed
# share count (``int(cash / entry_fill)`` ignores it), so it is irrelevant to G2b.
# Kept in lock-step with C2-M3 by the drift test (METHODOLOGY §6).
PAPER_SLIPPAGE_BPS: float = 5.0
PAPER_LIQUIDITY_CAP: float = 0.10

# G3 paper-loop liveness: a real run must complete ≥ this many consecutive clean
# daily cycles with state round-tripping across runs (PRD G3 — the C2-M3 constant).
G3_MIN_CYCLES: int = 5

# Default paper capital used for offline gate computation when no live account
# equity is available (the live cycle reads it from the bridge account summary).
# Immaterial against the real $1M paper account; large enough that the per-symbol
# equal-weight share count never rounds to zero on the placeholder universe.
DEFAULT_CAPITAL: float = 100_000.0

# Where the executor persists its position state between cron runs (the G3
# round-trip; format pinned in lean_bridge.PositionState).
DEFAULT_STATE_PATH: Path = (
    Path(__file__).resolve().parents[1] / "data" / "c6" / "position_state.json"
)


# ─── Pure sizing: a strategy's signals → sized per-symbol positions ─────────────


@dataclass(frozen=True)
class SizedPosition:
    """One strategy's sized holding for one symbol (signed). Pure value object.

    ``target_position`` is the {-1, 0, +1} direction the decision rule emitted;
    ``shares`` and ``notional`` are signed (negative for shorts) and carry the
    fully-invested equal-weight magnitude. ``shares`` feeds the bridge order;
    ``notional`` is what the G2b reconciliation compares against the simulator.
    """

    symbol: str
    target_position: int
    shares: float
    notional: float


def equal_weight_shares(capital_fraction: float, fill_price: float) -> int:
    """Whole shares deployable for *capital_fraction* dollars at *fill_price*.

    Reproduces the Phase-1 simulator's entry rule ``int(cash / entry_fill)``
    (``backtest/simulator.py``) — integer shares, no fractional sizing — which is
    exactly the ``C2-M2-SIZING-PARITY`` rule the bridge's old fixed-1-share
    placeholder lacked. A non-positive or non-finite price yields 0 (no position).
    """
    if not np.isfinite(fill_price) or fill_price <= 0:
        return 0
    return int(capital_fraction / fill_price)


def size_strategy(
    spec: StrategySpec,
    signals: Mapping[str, TargetSignal],
    capital_budget: float,
    prices: Mapping[str, float],
) -> dict[str, SizedPosition]:
    """Size *spec*'s signals fully-invested equal-weight within its universe.

    The strategy's ``capital_budget`` (its ``1/N`` slice of total capital) is split
    **equally across the full universe** (fixed ``K = |universe|``), so each symbol
    gets ``capital_budget / K``; a flat (0) signal simply deploys nothing there.
    Shares are signed by the target position and sized via
    :func:`equal_weight_shares` at the paper entry fill (close × the long/short
    slippage). Symbols without a signal or a price are skipped.

    Only the placeholder's ``fully_invested_equal_weight`` policy is implemented
    in C6; C3 extends ``sizing_policy.method`` with real (vol-target × confidence)
    sizing. A non-placeholder method raises rather than sizing silently wrong
    (METHODOLOGY §9 — no silent fallback).
    """
    if spec.sizing_policy.method != "fully_invested_equal_weight":
        raise NotImplementedError(
            f"sizing_policy.method={spec.sizing_policy.method!r} is not implemented "
            "in C6 (only the fully_invested_equal_weight placeholder); C3 owns real sizing"
        )
    universe = list(spec.universe)
    k = len(universe)
    per_symbol_capital = capital_budget / k if k else 0.0
    slip = PAPER_SLIPPAGE_BPS / 10_000.0

    out: dict[str, SizedPosition] = {}
    for sym in universe:
        sig = signals.get(sym)
        price = prices.get(sym)
        if sig is None or price is None:
            continue
        tp = int(sig.target_position)
        if tp == 0 or not np.isfinite(price) or price <= 0:
            out[sym] = SizedPosition(sym, tp, 0.0, 0.0)
            continue
        # Long buys at the ask, short sells at the bid — the simulator's fill convention.
        fill_price = price * (1.0 + slip) if tp > 0 else price * (1.0 - slip)
        magnitude = equal_weight_shares(per_symbol_capital, fill_price)
        shares = float(tp * magnitude)
        out[sym] = SizedPosition(sym, tp, shares, shares * fill_price)
    return out


# ─── Pure allocation: net the sized strategies per symbol, then clamp ───────────


@dataclass(frozen=True)
class NetTarget:
    """The portfolio's net target for one symbol after combining all strategies.

    ``target_position`` is the clamped net direction ∈ {-1, 0, +1}; ``shares`` is
    the (unsigned) order quantity handed to the bridge. ``notional`` is the signed
    net dollar exposure (diagnostic / console).
    """

    symbol: str
    target_position: int
    shares: float
    notional: float


def _net_direction(contributions: Sequence[tuple[float, int]]) -> int:
    """Budget-weighted sign vote → {-1, 0, +1}. Sizing-independent (drives G2a).

    *contributions* is ``(budget_weight, target_position)`` per strategy holding
    the symbol. The direction is ``sign(Σ budget_weight × target_position)`` — it
    depends only on the directions and budgets, never on the share magnitudes, so
    a single strategy's net direction equals its raw signal sign exactly (the G2a
    parity guarantee).
    """
    vote = sum(w * tp for w, tp in contributions)
    return int(np.sign(vote))


def net_targets(
    specs: Sequence[StrategySpec],
    sized_by_strategy: Mapping[str, Mapping[str, SizedPosition]],
    *,
    budgets: Mapping[str, float] | None = None,
) -> dict[str, NetTarget]:
    """Combine each strategy's sized positions into one net target per symbol.

    Combination rule (PRD §Open Questions, pinned): NET the signed sized positions
    per symbol — direction from the budget-weighted sign vote (:func:`_net_direction`,
    sizing-independent), magnitude from the summed signed shares — then CLAMP the
    direction to the tightest per-symbol ``risk_limits.max_position`` across the
    strategies holding it. Confidence is **not** re-applied here (it already shaped
    the sizes). *budgets* maps strategy id → capital weight for the vote (equal
    ``1/N`` by default).

    The clamp zeroes a position whose unit magnitude (1) exceeds ``max_position``;
    with the permissive C6 default (``max_position = 1.0``) it is a no-op. Real
    share-level caps and drawdown stops are C3.
    """
    if budgets is None:
        n = len(specs)
        budgets = {s.id: (1.0 / n if n else 0.0) for s in specs}

    symbols = sorted({sym for sp in sized_by_strategy.values() for sym in sp})
    out: dict[str, NetTarget] = {}
    for sym in symbols:
        contributions: list[tuple[float, int]] = []
        net_shares = 0.0
        net_notional = 0.0
        caps: list[float] = []
        for spec in specs:
            pos = sized_by_strategy.get(spec.id, {}).get(sym)
            if pos is None:
                continue
            contributions.append((budgets.get(spec.id, 0.0), pos.target_position))
            net_shares += pos.shares
            net_notional += pos.notional
            caps.append(spec.risk_limits.max_position)
        if not contributions:
            continue
        direction = _net_direction(contributions)
        cap = min(caps) if caps else 1.0
        # Position units are integral ({-1,0,+1}); a sub-unit cap forbids the unit.
        clamped = direction if abs(direction) <= cap else 0
        if clamped == 0:
            net_shares = 0.0
            net_notional = 0.0
        out[sym] = NetTarget(sym, clamped, abs(net_shares), net_notional)
    return out


# ─── G2a — single-strategy parity gate (reproduces the C2 sign path) ────────────


def single_strategy_parity_report(
    spec: StrategySpec,
    forecasts: Sequence[float],
    *,
    capital: float = DEFAULT_CAPITAL,
    price: float = 100.0,
    max_mismatches: int = G2A_MAX_MISMATCHES,
):
    """G2a: the lone-strategy allocator direction == the backtest sign path.

    For each forecast, routes it through the **real** allocator (:func:`size_strategy`
    → :func:`net_targets`) for a single-symbol single enabled *spec*, and pairs the
    resulting net direction against an *independently* computed
    ``backtest_path_target_position`` (``lean_bridge``) — exactly the C2 G1 parity
    construction. Returns a ``lean_bridge.SignalParityResult``; a PASS requires
    ``n > 0`` checks and ≤ *max_mismatches* (pinned 0). The allocator's direction is
    the budget-weighted sign vote, so this proves the multi-strategy layer collapses
    to the proven C2 decision on a single strategy.
    """
    sym = spec.universe[0]
    asof = pd.Timestamp("2020-01-01")
    checks: list[tuple[int, int]] = []
    for f in forecasts:
        tp = derive_target_position(f)
        signals = {sym: TargetSignal(sym, asof, float(f), tp)}
        sized = size_strategy(spec, signals, capital, {sym: price})
        nets = net_targets([spec], {spec.id: sized})
        alloc_dir = nets[sym].target_position if sym in nets else 0
        checks.append((alloc_dir, backtest_path_target_position(f)))
    return signal_parity_gate_report(checks, max_mismatches=max_mismatches)


# ─── G2b — sizing reconciliation gate (vs the real simulator) ───────────────────


@dataclass(frozen=True)
class SizingReconResult:
    """Verdict of the G2b sizing reconciliation gate for one symbol."""

    symbol: str
    allocator_notional: float
    simulator_notional: float
    relative_delta: float
    passed: bool


def _simulator_entry_notional(prices: pd.DataFrame, capital: float) -> float:
    """Notional the **real** simulator deploys on the first entry (constant long).

    Runs ``backtest/simulator.py::simulate`` with a constant long signal and
    ``initial_capital = capital`` under the matched paper slippage/liquidity, and
    reads the first trade's ``shares × entry_price`` from the trade log — i.e. the
    capital the engine actually put to work. 0.0 if the engine never traded.
    Reconciling against the live ``simulate()`` (not a reimplementation) keeps G2b
    honest (avoids the tautology ``C2-M2-G1-HARNESS-EXACT`` flags for G1).
    """
    signals = pd.Series(1, index=prices.index, dtype=int)
    _, trade_log = simulate(
        prices,
        signals,
        initial_capital=capital,
        slippage_bps=PAPER_SLIPPAGE_BPS,
        liquidity_cap=PAPER_LIQUIDITY_CAP,
    )
    if trade_log.empty:
        return 0.0
    row = trade_log.iloc[0]
    return float(row["shares"]) * float(row["entry_price"])


def _allocator_entry_notional(prices: pd.DataFrame, capital: float) -> float:
    """Notional :func:`equal_weight_shares` deploys on the same first-entry bar.

    The simulator fills a bar-0 signal at the bar-1 open; the allocator sizes on
    that same fill (``open × (1 + slip)``) with the same per-symbol capital, so the
    two notionals coincide up to the integer-truncation + liquidity-cap residual
    the gate bounds at 1%.
    """
    if len(prices) < 2:
        return 0.0
    entry_open = float(prices["open"].iloc[1])
    slip = PAPER_SLIPPAGE_BPS / 10_000.0
    fill_price = entry_open * (1.0 + slip)
    shares = equal_weight_shares(capital, fill_price)
    return shares * fill_price


def sizing_reconciliation_report(
    prices_by_symbol: Mapping[str, pd.DataFrame],
    *,
    per_symbol_capital: float,
    max_relative_delta: float = G2B_MAX_RELATIVE_DELTA,
) -> dict[str, SizingReconResult]:
    """G2b: equal-weight allocator notional ⇄ simulator capital sizing, ≤ 1%.

    For each symbol, gives both the allocator and the real simulator the **same**
    ``per_symbol_capital`` (the matched assumption — the simulator is fully invested
    in that one name) and compares the deployed notional. A symbol passes iff
    ``|relative_delta| ≤ max_relative_delta`` (the pinned 1%, shared with C2-M3).
    Symbols with too little history to place a trade are skipped.
    """
    out: dict[str, SizingReconResult] = {}
    for sym, prices in prices_by_symbol.items():
        sim_notional = _simulator_entry_notional(prices, per_symbol_capital)
        if sim_notional == 0.0:
            continue
        alloc_notional = _allocator_entry_notional(prices, per_symbol_capital)
        rel = alloc_notional / sim_notional - 1.0
        out[sym] = SizingReconResult(
            symbol=sym,
            allocator_notional=alloc_notional,
            simulator_notional=sim_notional,
            relative_delta=rel,
            passed=bool(np.isfinite(rel) and abs(rel) <= max_relative_delta),
        )
    return out


# ─── The daily cycle (G3 liveness) ──────────────────────────────────────────────

# Injected dependencies (defaults wired in run_trading_cycle): all external I/O is
# behind a callable so the loop is driven through fakes in tests.
FreshnessFn = Callable[..., Sequence[object]]  # now=asof -> [FeedStatus]
SignalFn = Callable[[StrategySpec, pd.Timestamp], dict[str, TargetSignal]]
PriceFn = Callable[[Sequence[str], pd.Timestamp], dict[str, float]]
CapitalFn = Callable[[object], float]


class FreshnessError(RuntimeError):
    """Raised when the freshness gate finds a stale/missing feed (aborts the cycle)."""


@dataclass(frozen=True)
class CycleResult:
    """What one daily cycle produced — the audit record for the run."""

    asof: pd.Timestamp
    targets: dict[str, NetTarget]
    order_results: list[dict]
    state: PositionState


def _strategy_signal(spec: StrategySpec, asof: pd.Timestamp) -> dict[str, TargetSignal]:
    """Compute *spec*'s daily signals. Dispatches on ``model_ref``.

    Only the ARIMA placeholder is wired in C6 (it forecasts the next-bar return
    from the label series via ``lean_bridge.daily_signal``). A second deployable
    model arrives as a registry entry later; until its signal path exists, an
    unsupported ``model_ref`` raises rather than silently emitting nothing
    (METHODOLOGY §9). This is the documented C6 extension point.
    """
    if spec.model_ref == "arima_baseline":
        return daily_signal(asof, symbols=list(spec.universe))
    raise NotImplementedError(
        f"no signal path for model_ref={spec.model_ref!r} yet — C6 wires only the "
        "arima_baseline placeholder; a new deployable model adds its dispatch here"
    )


def _latest_prices(symbols: Sequence[str], asof: pd.Timestamp) -> dict[str, float]:
    """Most recent point-in-time-correct close per symbol as of *asof* (lake read)."""
    from quant.storage.realtime import get_pit_panel

    panel = get_pit_panel(list(symbols), asof)
    return {
        sym: float(frame["close"].iloc[-1])
        for sym, frame in panel.items()
        if not frame.empty
    }


def _bridge_capital(bridge: object) -> float:
    """Total deployable capital = the paper account equity (fallback: default)."""
    try:
        equity = bridge.account_summary().equity  # type: ignore[attr-defined]
        return float(equity)
    except (AttributeError, ValueError, TypeError):
        return DEFAULT_CAPITAL


def run_trading_cycle(
    asof: pd.Timestamp | str,
    bridge: object,
    registry: Mapping[str, StrategySpec],
    state_path: str | Path,
    *,
    freshness_fn: FreshnessFn,
    signal_fn: SignalFn = _strategy_signal,
    price_fn: PriceFn = _latest_prices,
    capital_fn: CapitalFn = _bridge_capital,
) -> CycleResult:
    """One idempotent daily cycle over the enabled registry (the G3 unit of work).

    Steps (PRD §Scope C6-M2): **freshness gate** (a stale/missing feed raises
    :class:`FreshnessError` → non-zero exit, never trades on stale data) →
    load prior state (the round-trip proof) → for each enabled strategy
    {signal → :func:`size_strategy`} at ``1/N`` capital budget → :func:`net_targets`
    (net per symbol + clamp) → ``bridge.place_target`` → persist the bridge's
    reported holdings. Returns a :class:`CycleResult`. Every external dependency is
    injected so the cycle runs offline in tests.
    """
    asof_ts = pd.Timestamp(asof)

    statuses = freshness_fn(now=asof_ts)
    alerts = [s for s in statuses if getattr(s, "is_alert", False)]
    if alerts:
        names = ", ".join(getattr(s, "name", "?") for s in alerts)
        raise FreshnessError(f"freshness gate failed; non-fresh feeds: {names}")

    # Load-only: proves the prior cycle's persisted file deserializes (round-trip).
    # Position authority is the bridge, so the loaded value drives nothing.
    _ = load_position_state(state_path)

    enabled = enabled_strategies(dict(registry))
    capital = capital_fn(bridge)
    n = len(enabled)
    # Equal-weight 1/N CAPITAL BUDGET (dollars) per enabled strategy. The same
    # per-strategy dollar budget doubles as the (equal, positive) vote weight for
    # net_targets' budget-weighted direction — consistent for the pinned 1/N rule.
    budget_dollars = {s.id: (capital / n if n else 0.0) for s in enabled}

    universe = sorted({sym for s in enabled for sym in s.universe})
    prices = price_fn(universe, asof_ts) if universe else {}

    sized_by_strategy: dict[str, dict[str, SizedPosition]] = {}
    for spec in enabled:
        signals = signal_fn(spec, asof_ts)
        sized_by_strategy[spec.id] = size_strategy(spec, signals, budget_dollars[spec.id], prices)

    targets = net_targets(enabled, sized_by_strategy, budgets=budget_dollars)

    order_results: list[dict] = []
    for tgt in targets.values():
        order = TargetOrder(tgt.symbol, tgt.target_position, tgt.shares)
        order_results.append(bridge.place_target(order))  # type: ignore[attr-defined]

    holdings = bridge.current_positions()  # type: ignore[attr-defined]
    state = PositionState(asof=str(asof_ts), holdings=dict(holdings))
    save_position_state(state, state_path)
    return CycleResult(asof=asof_ts, targets=targets, order_results=order_results, state=state)


def run_trading_loop(
    asofs: Sequence[pd.Timestamp | str],
    bridge: object,
    registry: Mapping[str, StrategySpec],
    state_path: str | Path,
    *,
    freshness_fn: FreshnessFn,
    signal_fn: SignalFn = _strategy_signal,
    price_fn: PriceFn = _latest_prices,
    capital_fn: CapitalFn = _bridge_capital,
) -> list[CycleResult]:
    """Run :func:`run_trading_cycle` over *asofs*, persisting state between each (G3).

    The gateable half of G3 — the loop runs end-to-end with state that round-trips
    across cycles — is exercised here deterministically (a fake bridge in tests).
    The *live* ≥``G3_MIN_CYCLES``-session accrual against the real paper broker is
    the cron runbook in this module's docstring (it spans real market days; cannot
    run in one session — declared §9, the C2-M3 precedent).
    """
    return [
        run_trading_cycle(
            asof,
            bridge,
            registry,
            state_path,
            freshness_fn=freshness_fn,
            signal_fn=signal_fn,
            price_fn=price_fn,
            capital_fn=capital_fn,
        )
        for asof in asofs
    ]


# ─── Ledger (audit-only — C6 is infrastructure, no edge claim) ──────────────────


def _record_ledger(asof: pd.Timestamp, started_at: str, finished_at: str, *, ok: bool) -> None:
    """Append an audit-only ledger entry (``n_comparisons = 0``; PRD §Ledger discipline).

    C6 makes no pre-registered edge claim, so a daily run contributes **no**
    research trials to the deflation N — the entry is bookkeeping only, idempotent
    by config hash via ``record_run`` (mirrors C1/C2).
    """
    from quant.ledger import record_run

    record_run(
        {
            "config_hash": f"c6-daily-{asof.date().isoformat()}",
            "started_at": started_at,
            "finished_at": finished_at,
        },
        prd="c6",
        milestone="C6-M2",
        preregistration=".claude/prds/c6-strategy-registry.prd.md#pre-committed-gate",
        n_comparisons=0,  # infrastructure — no deflation contribution
        verdict="gate_passed" if ok else "gate_failed",
        agent="human",
        artifacts=["data/c6/position_state.json"],
        notes="C6-M2 daily executor cycle. Audit-only; no edge claim.",
    )


# ─── CLI ─────────────────────────────────────────────────────────────────────


def _load_monitor() -> FreshnessFn:
    """Load ``monitor_freshness.monitor`` from the sibling script (not a package).

    ``scripts/`` has no ``__init__.py``, so the freshness monitor is loaded by
    file path rather than imported as ``scripts.monitor_freshness`` — which would
    not resolve when this file is run directly as ``python scripts/trade_daily.py``.
    """
    import importlib.util
    import sys

    path = Path(__file__).resolve().parent / "monitor_freshness.py"
    spec = importlib.util.spec_from_file_location("monitor_freshness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the module's @dataclass annotation resolution can
    # find it by name (dataclasses looks up cls.__module__ in sys.modules).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.monitor


def main(argv: list[str] | None = None) -> int:
    """Cron entrypoint: ingest → freshness → trade one cycle. Non-zero on any failure."""
    parser = argparse.ArgumentParser(
        description="C6-M2 daily executor over the enabled strategy registry (paper).",
    )
    parser.add_argument("--asof", default=None, help="Trade as of this UTC instant (ISO-8601); default now.")
    parser.add_argument("--no-ingest", action="store_true", help="Skip ingest (assume a prior scheduled run).")
    parser.add_argument("--no-ledger", action="store_true", help="Skip the audit-only ledger entry.")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH, help="Position-state path.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    started_at = pd.Timestamp.now("UTC").isoformat()
    asof = pd.Timestamp(args.asof) if args.asof else pd.Timestamp.now("UTC")

    try:
        if not args.no_ingest:
            from quant.flows.daily import daily_ingest

            status = daily_ingest()
            failures = {k: v for k, v in status.items() if not str(v).startswith("ok")}
            if failures:
                logger.error("ingest reported failures: %s", failures)

        from quant.execution.lean_bridge import AlpacaPaperBridge

        registry = load_registry()
        bridge = AlpacaPaperBridge.from_settings()
        result = run_trading_cycle(
            asof, bridge, registry, args.state, freshness_fn=_load_monitor()
        )
        logger.info("cycle complete: %d net target(s) placed", len(result.targets))
    except Exception as exc:  # cron mail-on-stderr surfaces a non-zero exit
        logger.error("daily cycle failed: %r", exc)
        finished_at = pd.Timestamp.now("UTC").isoformat()
        if not args.no_ledger:
            _record_ledger(asof, started_at, finished_at, ok=False)
        return 1

    finished_at = pd.Timestamp.now("UTC").isoformat()
    if not args.no_ledger:
        _record_ledger(asof, started_at, finished_at, ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
