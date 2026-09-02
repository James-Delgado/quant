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
    .venv/bin/python scripts/reconcile_paper_backtest.py --live-replay
        # C2-M3-LIVE-REPLAY: paper multiple sourced from the LIVE Alpaca paper
        # fill log (a genuine independent engine) via ``paper_multiple_override``
        # — the ``unexplained`` residual is load-bearing in this mode. Exits 3
        # until the account accrues ≥ LIVE_MIN_FILL_SESSIONS fill sessions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# The reconciliation arithmetic CORE (G2 gate + residual decomposition) lives in
# ``quant.execution.reconciliation`` so the E3 console and this CLI runner share
# ONE tested implementation (C2-M3-RECON-CORE-LIFT). This script is a thin CLI
# consumer: it imports + re-exports the core and adds the per-symbol/window
# orchestration, leak-free signal generation, report rendering, ledger write, and
# the G3 paper-loop primitive.
from quant.execution.reconciliation import (
    BACKTEST_COST_MODEL,
    G2_MAX_RELATIVE_DELTA,
    PAPER_COST_MODEL,
    UNEXPLAINED_EPS,
    ReconciliationResult,
    decompose_residual,
    equity_curve,
    g2_reconciliation_gate_report,
    growth_multiple,
    relative_delta,
)
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

# Re-export the lifted reconciliation core + the bridge dataclasses the G3 loop
# produces/consumes, so the script's namespace (``rpb.*`` in the tests, the
# ``trade_daily`` drift test) carries the same surface it did before the lift.
__all__ = [
    "TargetSignal",
    "PositionState",
    "TargetOrder",
    "BACKTEST_COST_MODEL",
    "G2_MAX_RELATIVE_DELTA",
    "PAPER_COST_MODEL",
    "UNEXPLAINED_EPS",
    "ReconciliationResult",
    "decompose_residual",
    "equity_curve",
    "g2_reconciliation_gate_report",
    "growth_multiple",
    "relative_delta",
    # Live-replay surface (C2-M3-LIVE-REPLAY)
    "Fill",
    "InsufficientLiveAccrualError",
    "LIVE_MIN_FILL_SESSIONS",
    "fetch_paper_fills",
    "fills_to_position_series",
    "signals_from_positions",
    "live_growth_multiple",
    "run_live_replay",
]

# A daily-signal emitter: ``(asof, symbols=…) -> {symbol: TargetSignal}`` — the
# shape of ``lean_bridge.daily_signal`` and of any fake injected by the G3 loop.
DailySignalFn = Callable[..., dict[str, TargetSignal]]

# ─── Pinned constants (METHODOLOGY §1/§2 — the code is the source of truth) ─────
# The G2 tolerance, the unexplained-residual epsilon, and the two cost models are
# the pinned reconciliation arithmetic — they live in (and are imported from)
# ``quant.execution.reconciliation`` so the script and the E3 console agree by
# construction (METHODOLOGY §6). The script-only constants below govern the CLI's
# orchestration (loop liveness, signal cadence, window/universe selection).

# G3 paper-loop liveness: a real run must complete ≥ this many consecutive clean
# daily cycles with position state round-tripping across runs (C2 PRD G3).
G3_MIN_CYCLES: int = 5

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


# The default provenance line for the report's paper-side curve. The live-replay
# mode (C2-M3-LIVE-REPLAY) overrides it so a report whose paper multiple came from
# the real broker fill log is labeled as such — a reader must never mistake a
# live-sourced verdict for the deterministic simulate()-vs-simulate() replay.
DEFAULT_PAPER_SOURCE: str = (
    "the same simulator under the Alpaca paper cost model "
    "(commission-free; slippage + fill matched)"
)
LIVE_PAPER_SOURCE: str = (
    "the LIVE Alpaca paper account fill log (an independent engine — the "
    "`unexplained` residual is load-bearing in this mode)"
)


def format_reconciliation_report(
    results: Mapping[str, ReconciliationResult],
    *,
    window: tuple[str, str] = RECON_WINDOW,
    max_relative_delta: float = G2_MAX_RELATIVE_DELTA,
    paper_source: str = DEFAULT_PAPER_SOURCE,
) -> str:
    """Render the per-symbol G2 verdicts as a markdown reconciliation report.

    The verdict line quotes the gate output verbatim (no paraphrase — METHODOLOGY
    §9 / "verdicts from gate functions"); the residual is named component-by-
    component so no basis point of the delta is left unexplained. *paper_source*
    names where the paper-side growth multiple came from (the simulate() paper
    config by default; the live fill log in ``--live-replay`` mode).
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
        f"Paper engine: {paper_source}.",
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
        lines.append(
            f"- unexplained residual: {r.unexplained:.2e} (must be ≤ {UNEXPLAINED_EPS:.0e})"
        )
        lines.append("")
    return "\n".join(lines)


# ─── Live-replay reconciliation (C2-M3-LIVE-REPLAY) ────────────────────────────
# The deterministic replay above runs BOTH curves through the same ``simulate()``,
# so its decomposition closes exactly and the ``unexplained`` guard is structurally
# satisfied (~1e-16). This section sources the paper growth multiple from a genuine
# independent engine — the live Alpaca paper account's historical fill log — and
# feeds it through ``paper_multiple_override``, making the guard load-bearing: any
# execution mechanic the cost-model decomposition cannot name (real fill prices vs
# the 5 bps model, same-session mid-day fills vs the simulator's next-open fills,
# partial fills) surfaces as a REAL unexplained residual to investigate.
#
# Declared framings (METHODOLOGY §9 — named, not silent):
#   * SESSION KEY. A fill's session is its UTC calendar date; daily lake bars carry
#     their session date in the index the same way. RTH fills (13:30–21:00 UTC)
#     always share the session's UTC date, and ``trade_daily`` runs ~12:30 UTC.
#   * SIGNAL ALIGNMENT. ``signal[t] = sign(live position at close of session t)``.
#     ``simulate()`` fills signal[t] at the open of t+1, so the replay holds the
#     same sign the live account held, one transition lagged by the close→next-open
#     gap (the live fill lands mid-session). That timing gap is an execution
#     mechanic DELIBERATELY left in the residual — naming it away in the cost model
#     would defeat the point of the independent replay.
#   * FUNDING BASE. The live per-symbol multiple treats the first fill's notional
#     as the funding base (cash₀). ``simulate()`` starts from ``initial_capital``
#     and leaves a sub-share cash remainder undeployed; the (< 1 share) scale
#     difference is part of the residual, bounded and declared.


# Minimum distinct fill SESSIONS per symbol before a live-replay verdict is
# meaningful — pinned BEFORE any live fill history exists (METHODOLOGY §1).
# Mirrors the G3 ≥5-clean-cycles liveness bar: fewer sessions than a trading week
# reconciles noise, not execution mechanics.
LIVE_MIN_FILL_SESSIONS: int = 5

# CLI exit code for "live accrual insufficient — no verdict produced". Distinct
# from 1 (no symbols reconciled) and 2 (gate FAIL) so cron/CI can tell "not yet
# runnable" from "ran and failed" (an honest refusal, never a silent pass).
LIVE_REPLAY_EXIT_INSUFFICIENT: int = 3


# Alpaca's per-page order-query maximum (the API default is a silently small 50).
_ORDERS_PAGE_LIMIT: int = 500


class InsufficientLiveAccrualError(RuntimeError):
    """Raised when the paper account's fill log has too few sessions to reconcile."""


@dataclass(frozen=True)
class Fill:
    """One executed (fully or partially filled) paper order, normalized.

    ``filled_at`` is tz-aware UTC; ``side`` is ``"BUY"`` or ``"SELL"``; ``qty`` and
    ``price`` are the filled quantity and average fill price the broker reports.
    """

    symbol: str
    filled_at: pd.Timestamp
    side: str
    qty: float
    price: float


def _order_side(side: object) -> str:
    """Normalize an Alpaca ``OrderSide`` (or raw string) to ``"BUY"`` / ``"SELL"``."""
    raw = str(getattr(side, "value", side)).lower()
    if raw.endswith("buy"):
        return "BUY"
    if raw.endswith("sell"):
        return "SELL"
    raise ValueError(f"unrecognized order side: {side!r}")


def _utc_timestamp(ts: object) -> pd.Timestamp:
    """Coerce *ts* to a tz-aware UTC Timestamp (naive input is assumed UTC)."""
    out = pd.Timestamp(ts)  # type: ignore[arg-type]
    return out.tz_localize("UTC") if out.tzinfo is None else out.tz_convert("UTC")


def _session_date(ts: object) -> object:
    """The session key for a timestamp: its UTC calendar date (declared framing)."""
    return _utc_timestamp(ts).date()


def fetch_paper_fills(
    client: object,
    symbols: Sequence[str],
    *,
    window: tuple[str, str] | None = None,
) -> list[Fill]:
    """Fetch the paper account's executed fills for *symbols*, oldest first.

    Thin adapter over ``TradingClient.get_orders`` (closed orders): orders that
    never filled (``filled_qty`` 0/None, no ``filled_at``) are dropped — a
    cancelled order has no execution to replay. *window* optionally bounds the
    query (``after``/``until``). The client is injected so the adapter is
    unit-testable against a fake, exactly like ``AlpacaPaperBridge``.

    The query requests Alpaca's per-page maximum (500 orders; the API default is
    only 50). A full 500-order page is flagged loudly as possible truncation — a
    silently incomplete fill log would corrupt the live multiple (METHODOLOGY §9).
    Pagination lands with the verdict follow-up if accrual ever exceeds a page.
    """
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    kwargs: dict[str, object] = {
        "status": QueryOrderStatus.CLOSED,
        "symbols": list(symbols),
        "limit": _ORDERS_PAGE_LIMIT,
    }
    if window is not None:
        kwargs["after"] = _utc_timestamp(window[0]).to_pydatetime()
        kwargs["until"] = _utc_timestamp(window[1]).to_pydatetime()
    orders = client.get_orders(GetOrdersRequest(**kwargs))  # type: ignore[attr-defined]
    if len(orders) >= _ORDERS_PAGE_LIMIT:
        logger.warning(
            "get_orders returned a full page (%d) — the fill log may be TRUNCATED; "
            "bound the query with --live-window or add pagination before trusting "
            "the live multiple",
            len(orders),
        )

    fills: list[Fill] = []
    for order in orders:
        qty = float(getattr(order, "filled_qty", 0) or 0)
        price = float(getattr(order, "filled_avg_price", 0) or 0)
        filled_at = getattr(order, "filled_at", None)
        if qty <= 0 or price <= 0 or filled_at is None:
            continue  # never executed — nothing to replay
        fills.append(
            Fill(
                symbol=str(order.symbol),
                filled_at=_utc_timestamp(filled_at),
                side=_order_side(order.side),
                qty=qty,
                price=price,
            )
        )
    return sorted(fills, key=lambda f: f.filled_at)


def fills_to_position_series(fills: Sequence[Fill], sessions: pd.DatetimeIndex) -> pd.Series:
    """Signed share position held at the close of each session in *sessions*.

    Fills accumulate chronologically (BUY adds, SELL subtracts); fills dated
    before the first session establish the opening position; fills after the last
    session are ignored. Sessions with no fill carry the prior position forward.
    """
    session_dates = [_session_date(ts) for ts in sessions]
    deltas = sorted(
        (_session_date(f.filled_at), f.qty if f.side == "BUY" else -f.qty) for f in fills
    )
    position = 0.0
    out: list[float] = []
    i = 0
    for date in session_dates:
        while i < len(deltas) and deltas[i][0] <= date:
            position += deltas[i][1]
            i += 1
        out.append(position)
    return pd.Series(out, index=sessions, dtype=float)


def signals_from_positions(positions: pd.Series) -> pd.Series:
    """Map a signed position series to the {-1, 0, +1} signal ``simulate()`` takes.

    ``signal[t] = sign(position at close of session t)`` — the pinned alignment:
    the replay holds the live account's sign on the live account's sessions, with
    the close→next-open transition lag declared above left in the residual.
    """
    return pd.Series(np.sign(positions).astype(int), index=positions.index)


def live_growth_multiple(fills: Sequence[Fill], closes: pd.Series) -> float:
    """Realized growth multiple of the live fill log, valued at the last close.

    A per-symbol sub-ledger: the funding base is the first fill's notional
    (``cash₀ = qty × price``); each BUY debits cash, each SELL credits it; the
    terminal equity is ``cash + position × last close``. Returns
    ``equity_end / cash₀`` — the live-engine analog of :func:`growth_multiple`.
    NaN if there are no fills or the base is non-positive.
    """
    ordered = sorted(fills, key=lambda f: f.filled_at)
    if not ordered or len(closes) == 0:
        return float("nan")
    base = ordered[0].qty * ordered[0].price
    if base <= 0:
        return float("nan")
    cash = base
    position = 0.0
    for f in ordered:
        if f.side == "BUY":
            cash -= f.qty * f.price
            position += f.qty
        else:
            cash += f.qty * f.price
            position -= f.qty
    equity_end = cash + position * float(closes.iloc[-1])
    return equity_end / base


def run_live_replay(
    client: object,
    symbols: Sequence[str] = RECON_UNIVERSE,
    *,
    window: tuple[str, str] | None = None,
    min_sessions: int = LIVE_MIN_FILL_SESSIONS,
    prices_loader: Callable[[Sequence[str], tuple[str, str]], dict[str, pd.DataFrame]]
    | None = None,
) -> dict[str, ReconciliationResult]:
    """Reconcile the live fill log against the backtest replay (the real G2 seam).

    Per symbol with ≥ *min_sessions* distinct fill sessions: rebuild the held
    position per session, derive the signal series, replay it through the
    backtest engine, and gate with ``paper_multiple_override`` set to the live
    fill-log multiple — the ``unexplained`` residual is now load-bearing.

    Symbols below the session floor are skipped (logged); if NO symbol qualifies,
    raises :class:`InsufficientLiveAccrualError` naming the accrual prerequisites
    (an honest refusal — METHODOLOGY §9 — never a vacuous verdict). *window*
    defaults to first-fill session → today.
    """
    fills = fetch_paper_fills(client, symbols, window=window)
    by_symbol: dict[str, list[Fill]] = {}
    for f in fills:
        by_symbol.setdefault(f.symbol, []).append(f)

    qualified = {
        sym: sym_fills
        for sym, sym_fills in by_symbol.items()
        if len({_session_date(f.filled_at) for f in sym_fills}) >= min_sessions
    }
    for sym in sorted(set(by_symbol) - set(qualified)):
        n = len({_session_date(f.filled_at) for f in by_symbol[sym]})
        logger.warning(
            "symbol=%s skipped — %d fill session(s) < LIVE_MIN_FILL_SESSIONS=%d",
            sym,
            n,
            min_sessions,
        )
    if not qualified:
        total = len({_session_date(f.filled_at) for f in fills})
        raise InsufficientLiveAccrualError(
            f"live paper fill log has too few sessions to reconcile "
            f"({total} distinct fill session(s) across {sorted(by_symbol) or 'no symbols'}; "
            f"need ≥ {min_sessions} per symbol). Accrue live sessions first — "
            "see PRIORITIES tasks C6-M2-LIVE-ACCRUAL (run the daily paper loop "
            "≥5 sessions) and MISC-SCHED-INSTALL (install the scheduler)."
        )

    if window is None:
        first = min(f.filled_at for f in fills)
        window = (str(_session_date(first)), str(pd.Timestamp.now("UTC").date()))

    loader = prices_loader if prices_loader is not None else _load_window_prices
    prices_by_symbol = loader(sorted(qualified), window)
    results: dict[str, ReconciliationResult] = {}
    for sym, sym_fills in qualified.items():
        prices = prices_by_symbol.get(sym)
        if prices is None or prices.empty:
            logger.warning("symbol=%s skipped — no lake bars over %s", sym, window)
            continue
        positions = fills_to_position_series(sym_fills, prices.index)
        signals = signals_from_positions(positions)
        live_mult = live_growth_multiple(sym_fills, prices["close"])
        results[sym] = g2_reconciliation_gate_report(
            prices, signals, paper_multiple_override=live_mult
        )
    return results


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
        artifacts=[
            "data/c2/reconciliation/reconciliation_report.md"
        ],  # repo-relative (audit trail)
        notes="C2-M3 backtest↔paper reconciliation (G2). Audit-only; no edge claim.",
    )


def _live_config_hash(window: tuple[str, str] | None) -> str:
    """Deterministic hash of the live-replay config (audit trail; mode-tagged)."""
    payload = json.dumps(
        {
            "mode": "live_replay",
            "window": window,
            "universe": RECON_UNIVERSE,
            "backtest_cost": BACKTEST_COST_MODEL,
            "g2_tol": G2_MAX_RELATIVE_DELTA,
            "min_sessions": LIVE_MIN_FILL_SESSIONS,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _run_live_replay_cli(args: argparse.Namespace) -> int:
    """The ``--live-replay`` CLI path: fill-log-sourced G2 (unexplained load-bearing).

    Writes ``live_replay_report.md`` + ``live_metadata.json`` alongside — never
    over — the deterministic replay's shipped artifacts. Exit codes: 0 PASS,
    2 gate FAIL, 1 nothing reconciled, ``LIVE_REPLAY_EXIT_INSUFFICIENT`` (3) when
    the account has not accrued enough fill sessions for a verdict.
    """
    from alpaca.trading.client import TradingClient

    from quant.config import settings

    started_at = pd.Timestamp.now("UTC").isoformat()
    window = tuple(args.live_window) if args.live_window else None
    client = TradingClient(settings.alpaca_api_key, settings.alpaca_secret_key, paper=True)
    try:
        results = run_live_replay(client, window=window)  # type: ignore[arg-type]
    except InsufficientLiveAccrualError as exc:
        logger.error("live replay refused: %s", exc)
        return LIVE_REPLAY_EXIT_INSUFFICIENT
    if not results:
        logger.error("no symbols reconciled from the live fill log")
        return 1

    display_window = window or (
        "first live fill session",
        str(pd.Timestamp.now("UTC").date()),
    )
    report = format_reconciliation_report(
        results, window=display_window, paper_source=LIVE_PAPER_SOURCE
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "live_replay_report.md").write_text(report)
    finished_at = pd.Timestamp.now("UTC").isoformat()
    metadata = {
        "mode": "live_replay",
        "config_hash": _live_config_hash(window),
        "started_at": started_at,
        "finished_at": finished_at,
        "window": list(display_window),
        "universe": list(results),
        "g2_tolerance": G2_MAX_RELATIVE_DELTA,
        "min_sessions": LIVE_MIN_FILL_SESSIONS,
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
    (args.output / "live_metadata.json").write_text(json.dumps(metadata, indent=2))

    print(report)
    if not args.no_ledger:
        from quant.ledger import record_run

        overall = all(r.passed for r in results.values())
        record_run(
            {
                "config_hash": _live_config_hash(window),
                "started_at": started_at,
                "finished_at": finished_at,
            },
            prd="c2",
            milestone="C2-M3-LIVE-REPLAY",
            preregistration=".claude/prds/c2-lean-paper.prd.md#pre-committed-gate",
            n_comparisons=0,  # infrastructure — no deflation contribution
            verdict="gate_passed" if overall else "gate_failed",
            agent="human",
            artifacts=["data/c2/reconciliation/live_replay_report.md"],
            notes=(
                "C2-M3-LIVE-REPLAY: G2 against the live Alpaca paper fill log "
                "(unexplained residual load-bearing). Audit-only; no edge claim."
            ),
        )
    return 0 if all(r.passed for r in results.values()) else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C2-M3 backtest↔paper reconciliation (G2).")
    parser.add_argument("--no-ledger", action="store_true", help="skip the audit-only ledger entry")
    parser.add_argument(
        "--output", type=Path, default=RECON_OUTPUT_DIR, help="output directory for the report"
    )
    parser.add_argument(
        "--live-replay",
        action="store_true",
        help="reconcile against the LIVE Alpaca paper fill log (independent engine; "
        "the unexplained residual is load-bearing)",
    )
    parser.add_argument(
        "--live-window",
        nargs=2,
        metavar=("START", "END"),
        default=None,
        help="bound the live fill query (ISO dates); default: first fill → today",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.live_replay:
        return _run_live_replay_cli(args)

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
