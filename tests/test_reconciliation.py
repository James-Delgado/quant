"""Unit tests for the C2-M3 backtest↔paper reconciliation harness.

Coverage mirrors the C2-M2 split (METHODOLOGY §15): the pure reconciliation
arithmetic — equity-curve construction, growth multiples, the relative-delta
metric, the residual decomposition, and the G2 gate — is asserted directly on
synthetic OHLCV with no lake or network access; the G3 daily-loop primitive is
driven through a fake bridge so the position-state round-trip is covered without
the live paper API.

The arithmetic CORE was lifted to the package module
``quant.execution.reconciliation`` (C2-M3-RECON-CORE-LIFT) so the E3 console and
the CLI runner share one tested implementation; those tests import the module
directly as ``recon``. The script ``scripts/reconcile_paper_backtest.py`` remains
the C2-M3 deliverable and a thin CLI consumer (signal generation, report
rendering, the G3 paper-loop primitive); those tests import it by path as ``rpb``,
exactly as ``tests/test_c2_hello_world.py`` does for ``scripts/c2_hello_world.py``.
A drift test asserts the script re-exports the SAME core objects (METHODOLOGY §6).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant.execution import reconciliation as recon

# Import the deliverable script as a module (it is not on the package path).
# Register in sys.modules before exec so dataclass annotation resolution can find
# the module by name (dataclasses looks up cls.__module__ in sys.modules).
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reconcile_paper_backtest.py"
_spec = importlib.util.spec_from_file_location("reconcile_paper_backtest", _SCRIPT)
rpb = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules[_spec.name] = rpb
_spec.loader.exec_module(rpb)


# ─── Fixtures ──────────────────────────────────────────────────────────────────


def _ohlcv(n: int = 120, seed: int = 0) -> pd.DataFrame:
    """A gently-trending synthetic OHLCV frame with finite, positive prices."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    close = 100.0 + np.cumsum(rng.normal(0.05, 1.0, n))
    close = np.maximum(close, 1.0)  # keep prices positive for the simulator
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.full(n, 5_000_000.0),
        },
        index=idx,
    )


def _alternating_signals(idx: pd.DatetimeIndex) -> pd.Series:
    """Flip long/flat every few bars so the simulator actually trades (and pays costs)."""
    vals = np.where((np.arange(len(idx)) // 5) % 2 == 0, 1, 0)
    return pd.Series(vals, index=idx, dtype=int)


# ─── Pinned constants (METHODOLOGY §1/§2 — drift contract, §6) ──────────────────


def test_pinned_constants():
    # Core reconciliation constants live in the lifted package module.
    assert recon.G2_MAX_RELATIVE_DELTA == 0.01
    assert recon.UNEXPLAINED_EPS == 1e-9
    # Backtest model is the Phase-1 pinned cost model; paper is commission-free.
    assert recon.BACKTEST_COST_MODEL["commission_per_share"] == 0.005
    assert recon.PAPER_COST_MODEL["commission_per_share"] == 0.0
    # Slippage + liquidity cap are matched between the two engines.
    assert recon.PAPER_COST_MODEL["slippage_bps"] == recon.BACKTEST_COST_MODEL["slippage_bps"]
    assert recon.PAPER_COST_MODEL["liquidity_cap"] == recon.BACKTEST_COST_MODEL["liquidity_cap"]
    # G3 loop liveness is a script-orchestration constant (stays in the runner).
    assert rpb.G3_MIN_CYCLES == 5


def test_script_reexports_the_lifted_core():
    # The thin CLI consumer must re-export the SAME core objects it imports, so a
    # reader of ``rpb.*`` (and the trade_daily drift test) sees no behaviour drift
    # after the lift (METHODOLOGY §6 — code-vs-code contract in both directions).
    assert rpb.G2_MAX_RELATIVE_DELTA is recon.G2_MAX_RELATIVE_DELTA
    assert rpb.UNEXPLAINED_EPS is recon.UNEXPLAINED_EPS
    assert rpb.BACKTEST_COST_MODEL is recon.BACKTEST_COST_MODEL
    assert rpb.PAPER_COST_MODEL is recon.PAPER_COST_MODEL
    assert rpb.ReconciliationResult is recon.ReconciliationResult
    assert rpb.equity_curve is recon.equity_curve
    assert rpb.growth_multiple is recon.growth_multiple
    assert rpb.relative_delta is recon.relative_delta
    assert rpb.decompose_residual is recon.decompose_residual
    assert rpb.g2_reconciliation_gate_report is recon.g2_reconciliation_gate_report


def test_recon_window_spans_at_least_two_regimes():
    # The pinned reconciliation window must cover ≥2 macro-era regimes (PRD G2).
    from quant.backtest.regimes import DateRangeDetector

    start, end = rpb.RECON_WINDOW
    idx = pd.date_range(start, end, freq="MS")
    labels = DateRangeDetector().label(idx)
    assert labels.nunique() >= 2


# ─── equity_curve / growth_multiple / relative_delta ───────────────────────────


def test_equity_curve_matches_simulate():
    from quant.backtest.simulator import simulate

    prices = _ohlcv()
    signals = _alternating_signals(prices.index)
    eq = recon.equity_curve(prices, signals, recon.BACKTEST_COST_MODEL)
    expected, _ = simulate(prices, signals, **recon.BACKTEST_COST_MODEL)
    pd.testing.assert_series_equal(eq, expected)


def test_equity_curve_requires_aligned_indexes():
    prices = _ohlcv()
    signals = _alternating_signals(prices.index)[:-1]  # shorter
    with pytest.raises(ValueError, match="aligned"):
        recon.equity_curve(prices, signals, recon.BACKTEST_COST_MODEL)


def test_growth_multiple():
    eq = pd.Series([100.0, 110.0, 121.0])
    assert recon.growth_multiple(eq) == pytest.approx(1.21)


def test_growth_multiple_empty_is_one():
    assert recon.growth_multiple(pd.Series(dtype=float)) == 1.0


def test_relative_delta_sign_and_zero():
    assert recon.relative_delta(1.10, 1.10) == pytest.approx(0.0)
    # paper multiple above backtest → positive relative delta.
    assert recon.relative_delta(1.00, 1.01) == pytest.approx(0.01)
    assert recon.relative_delta(1.20, 1.20 * 0.99) == pytest.approx(-0.01)


# ─── decompose_residual ────────────────────────────────────────────────────────


def test_decompose_residual_only_commission_differs():
    prices = _ohlcv()
    signals = _alternating_signals(prices.index)
    components = recon.decompose_residual(
        prices, signals, backtest_cost=recon.BACKTEST_COST_MODEL, paper_cost=recon.PAPER_COST_MODEL
    )
    # Only commission_per_share differs between the two pinned models.
    assert set(components) == {"commission_per_share"}
    # Dropping commission (backtest 0.005 → paper 0.0) can only help paper P&L.
    assert components["commission_per_share"] >= 0.0


def test_decompose_residual_components_close_the_gap():
    # The named components must reconstruct the full backtest→paper multiple gap
    # with no unexplained remainder (the drift contract behind G2's "fully
    # decomposed residual"). Use a cost model that differs in TWO params so the
    # sequential composition is non-trivial.
    prices = _ohlcv()
    signals = _alternating_signals(prices.index)
    backtest = {"commission_per_share": 0.01, "slippage_bps": 10.0, "liquidity_cap": 0.10}
    paper = {"commission_per_share": 0.0, "slippage_bps": 2.0, "liquidity_cap": 0.10}
    components = recon.decompose_residual(prices, signals, backtest_cost=backtest, paper_cost=paper)
    assert set(components) == {"commission_per_share", "slippage_bps"}

    bt_mult = recon.growth_multiple(recon.equity_curve(prices, signals, backtest))
    paper_mult = recon.growth_multiple(recon.equity_curve(prices, signals, paper))
    reconstructed = bt_mult
    for c in components.values():
        reconstructed *= 1.0 + c
    assert reconstructed == pytest.approx(paper_mult, rel=1e-12)


def test_decompose_residual_handles_short_side():
    # Long/short/flat signals exercise the short-side fill path in the simulator;
    # the commission component must still be non-negative and the gap close exactly.
    prices = _ohlcv()
    vals = np.select(
        [(np.arange(len(prices)) // 4) % 3 == 0, (np.arange(len(prices)) // 4) % 3 == 1],
        [1, -1],
        default=0,
    )
    signals = pd.Series(vals, index=prices.index, dtype=int)
    result = recon.g2_reconciliation_gate_report(prices, signals)
    assert result.n_trades > 0
    assert abs(result.unexplained) <= recon.UNEXPLAINED_EPS
    assert result.components["commission_per_share"] >= 0.0


def test_decompose_residual_identical_models_is_empty():
    prices = _ohlcv()
    signals = _alternating_signals(prices.index)
    components = recon.decompose_residual(
        prices,
        signals,
        backtest_cost=recon.BACKTEST_COST_MODEL,
        paper_cost=recon.BACKTEST_COST_MODEL,
    )
    assert components == {}


# ─── g2_reconciliation_gate_report ─────────────────────────────────────────────


def test_g2_gate_passes_under_matched_assumptions():
    # Commission-free paper vs IBKR-cost backtest on a liquid name: the only
    # delta is the (tiny) per-share commission, well under 1% → PASS, residual
    # fully decomposed, nothing unexplained.
    prices = _ohlcv()
    signals = _alternating_signals(prices.index)
    result = recon.g2_reconciliation_gate_report(prices, signals)
    assert result.passed is True
    assert abs(result.relative_delta) <= recon.G2_MAX_RELATIVE_DELTA
    assert abs(result.unexplained) <= recon.UNEXPLAINED_EPS
    assert set(result.components) == {"commission_per_share"}


def test_g2_gate_fails_when_delta_exceeds_tolerance():
    # An absurd commission (way beyond IBKR) drives the backtest↔paper gap past
    # 1% → a pre-committed negative ("execution skew present"), still fully
    # decomposed (the gap is all commission, nothing unexplained).
    prices = _ohlcv()
    signals = _alternating_signals(prices.index)
    fat_commission = {"commission_per_share": 5.0, "slippage_bps": 5.0, "liquidity_cap": 0.10}
    result = recon.g2_reconciliation_gate_report(
        prices, signals, backtest_cost=fat_commission, paper_cost=recon.PAPER_COST_MODEL
    )
    assert result.passed is False
    assert abs(result.relative_delta) > recon.G2_MAX_RELATIVE_DELTA
    assert abs(result.unexplained) <= recon.UNEXPLAINED_EPS  # still fully explained


def test_g2_gate_fails_on_unexplained_residual():
    # If the paper curve is sourced independently of the toggled-config
    # decomposition (the forward drift contract: a real live-broker replay),
    # an unexplained residual fails the gate even when the delta is tiny.
    prices = _ohlcv()
    signals = _alternating_signals(prices.index)
    result = recon.g2_reconciliation_gate_report(
        prices,
        signals,
        paper_multiple_override=recon.growth_multiple(
            recon.equity_curve(prices, signals, recon.PAPER_COST_MODEL)
        )
        + 1e-6,  # a sliver the decomposition cannot account for
    )
    assert result.passed is False
    assert abs(result.unexplained) > recon.UNEXPLAINED_EPS


def test_g2_gate_empty_signals_cannot_pass():
    prices = _ohlcv()
    flat = pd.Series(0, index=prices.index, dtype=int)  # never trades → no reconciliation surface
    result = recon.g2_reconciliation_gate_report(prices, flat)
    assert result.n_trades == 0
    assert result.passed is False


# ─── generate_daily_signals (leak-free expanding ARIMA) ────────────────────────


def test_generate_daily_signals_are_valid_positions():
    prices = _ohlcv(n=200)
    sig = rpb.generate_daily_signals(prices["close"])
    assert set(sig.unique()).issubset({-1, 0, 1})
    assert len(sig) > 0
    # Signals are indexed within the price index (no fabricated dates).
    assert sig.index.isin(prices.index).all()


def test_generate_daily_signals_is_leak_free():
    # A signal at bar t must not change when future bars are appended — only
    # past-and-present data may inform it (expanding window).
    prices = _ohlcv(n=200)
    full = rpb.generate_daily_signals(prices["close"], refit_step=20)
    truncated = rpb.generate_daily_signals(prices["close"].iloc[:150], refit_step=20)
    common = full.index.intersection(truncated.index)
    assert len(common) > 0
    pd.testing.assert_series_equal(full.loc[common], truncated.loc[common])


def test_generate_daily_signals_too_short_is_empty():
    short = _ohlcv(n=10)["close"]
    assert rpb.generate_daily_signals(short, min_obs=30).empty


# ─── G3 daily-loop primitive: position-state round-trip ────────────────────────


class _FakeBridge:
    """Records placed targets; reports holdings consistent with them. No network."""

    def __init__(self) -> None:
        self._holdings: dict[str, float] = {}
        self.placed: list[tuple[str, int]] = []

    def current_positions(self) -> dict[str, float]:
        return dict(self._holdings)

    def place_target(self, order) -> dict:
        self.placed.append((order.symbol, order.target_position))
        self._holdings[order.symbol] = float(order.target_position) * order.qty
        return {"symbol": order.symbol, "submitted": True}


def test_run_paper_loop_state_round_trips(tmp_path):
    # Five cycles with deterministic signals; run N's persisted holdings must be
    # run N+1's opening holdings (the gateable half of G3).
    bridge = _FakeBridge()
    state_path = tmp_path / "state.json"
    asofs = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]

    # Signal alternates long/flat across cycles so holdings actually change.
    def fake_signal(asof, symbols=None):
        pos = 1 if asofs.index(str(asof)) % 2 == 0 else 0
        return {"SPY": rpb.TargetSignal("SPY", pd.Timestamp(asof), 0.01, pos)}

    states = rpb.run_paper_loop(
        asofs, bridge, state_path, daily_signal_fn=fake_signal, symbols=["SPY"]
    )
    assert len(states) == rpb.G3_MIN_CYCLES
    # Each cycle's persisted state is reloaded at the next cycle's open: the loop
    # never errored and the final on-disk state matches the last returned state.
    from quant.execution.lean_bridge import load_position_state

    assert load_position_state(state_path) == states[-1]
    # Holdings reflect the last cycle's target (cycle 5 → index 4 → long).
    assert states[-1].holdings["SPY"] == pytest.approx(1.0)


def test_run_daily_cycle_persists_every_transition(tmp_path):
    # Drive cycles individually: after EACH cycle the on-disk state must equal that
    # cycle's returned state (so run N's persisted holdings are run N+1's opening
    # holdings — the full G3 round-trip, not just the terminal one).
    from quant.execution.lean_bridge import load_position_state

    bridge = _FakeBridge()
    state_path = tmp_path / "state.json"
    asofs = ["2024-01-02", "2024-01-03", "2024-01-04"]

    def fake_signal(asof, symbols=None):
        pos = 1 if asofs.index(str(asof)) % 2 == 0 else 0
        return {"SPY": rpb.TargetSignal("SPY", pd.Timestamp(asof), 0.01, pos)}

    for asof in asofs:
        state = rpb.run_daily_cycle(
            asof, bridge, state_path, daily_signal_fn=fake_signal, symbols=["SPY"]
        )
        assert load_position_state(state_path) == state  # this cycle's write is readable


def test_reconciliation_result_components_are_read_only():
    # The verdict object is frozen; its decomposition must not be mutable either.
    prices = _ohlcv()
    signals = _alternating_signals(prices.index)
    result = recon.g2_reconciliation_gate_report(prices, signals)
    with pytest.raises(TypeError):
        result.components["commission_per_share"] = 99.0  # type: ignore[index]


def test_run_paper_loop_first_cycle_has_no_prior_state(tmp_path):
    bridge = _FakeBridge()
    state_path = tmp_path / "state.json"

    def fake_signal(asof, symbols=None):
        return {"SPY": rpb.TargetSignal("SPY", pd.Timestamp(asof), 0.01, 1)}

    states = rpb.run_paper_loop(
        ["2024-01-02"], bridge, state_path, daily_signal_fn=fake_signal, symbols=["SPY"]
    )
    assert len(states) == 1
    assert states[0].holdings["SPY"] == pytest.approx(1.0)


# ─── report rendering ──────────────────────────────────────────────────────────


def test_format_reconciliation_report_quotes_verdict():
    prices = _ohlcv()
    signals = _alternating_signals(prices.index)
    result = recon.g2_reconciliation_gate_report(prices, signals)
    report = rpb.format_reconciliation_report({"SPY": result})
    assert "SPY" in report
    # The rendered verdict must match the gate result, not merely contain a verdict word.
    assert ("PASS" if result.passed else "FAIL") in report
    assert "commission_per_share" in report  # the decomposed residual is named


# ─── Live-replay reconciliation (C2-M3-LIVE-REPLAY) ────────────────────────────
# The deterministic replay's `unexplained` guard is structurally satisfied (both
# curves share one simulate()); these tests cover the machinery that makes it
# load-bearing — sourcing the paper multiple from the live Alpaca fill log via
# `paper_multiple_override`. All broker I/O is a fake client; no network.

from types import SimpleNamespace  # noqa: E402


def _fill(day: str, side: str, qty: float, price: float, symbol: str = "SPY"):
    """A Fill with a mid-session (15:00 UTC) execution timestamp."""
    return rpb.Fill(
        symbol=symbol,
        filled_at=pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=15),
        side=side,
        qty=qty,
        price=price,
    )


def _fake_order(day: str, side: object, qty, price, symbol: str = "SPY"):
    """An Alpaca-shaped order object (only the fields the adapter reads)."""
    return SimpleNamespace(
        symbol=symbol,
        filled_at=pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=15),
        side=side,
        filled_qty=qty,
        filled_avg_price=price,
    )


class _FakeTradingClient:
    """Returns a canned order list; records the request it was queried with."""

    def __init__(self, orders) -> None:
        self._orders = list(orders)
        self.last_request = None

    def get_orders(self, request):
        self.last_request = request
        return self._orders


def test_live_pinned_constants():
    # Pinned BEFORE any live fill history exists (METHODOLOGY §1); the session
    # floor mirrors the G3 ≥5-clean-cycles liveness bar.
    assert rpb.LIVE_MIN_FILL_SESSIONS == 5
    assert rpb.LIVE_REPLAY_EXIT_INSUFFICIENT == 3


def test_fetch_paper_fills_normalizes_and_filters():
    orders = [
        # Later fill listed first: the adapter must sort oldest-first.
        _fake_order("2024-01-05", "sell", "4", "101.5"),
        # Enum-shaped side + string numerics (Alpaca returns strings).
        _fake_order("2024-01-03", SimpleNamespace(value="buy"), "10", "100.0"),
        # Never executed (cancelled): no filled qty/price/timestamp → dropped.
        SimpleNamespace(
            symbol="SPY", filled_at=None, side="buy", filled_qty=None, filled_avg_price=None
        ),
    ]
    fills = rpb.fetch_paper_fills(_FakeTradingClient(orders), ["SPY"])
    assert [f.side for f in fills] == ["BUY", "SELL"]  # sorted oldest-first, normalized
    assert fills[0].qty == pytest.approx(10.0)
    assert fills[1].price == pytest.approx(101.5)
    assert all(f.filled_at.tzinfo is not None for f in fills)


def test_fetch_paper_fills_window_bounds_the_query():
    client = _FakeTradingClient([])
    rpb.fetch_paper_fills(client, ["SPY"], window=("2024-01-01", "2024-02-01"))
    assert client.last_request is not None
    assert client.last_request.after is not None
    assert client.last_request.until is not None
    # The query must request the per-page maximum (the API default is only 50 —
    # a silently short page would truncate the fill log).
    assert client.last_request.limit == rpb._ORDERS_PAGE_LIMIT == 500


def test_fetch_paper_fills_full_page_warns_of_truncation(caplog):
    # A full 500-order page may mean the fill log was cut off — that must be
    # flagged loudly, never silently folded into the live multiple (§9).
    orders = [_fake_order("2024-01-03", "buy", "1", "100.0") for _ in range(500)]
    with caplog.at_level("WARNING"):
        fills = rpb.fetch_paper_fills(_FakeTradingClient(orders), ["SPY"])
    assert len(fills) == 500
    assert any("TRUNCATED" in rec.message for rec in caplog.records)


def test_fills_to_position_series_carries_forward_and_opening_position():
    sessions = pd.DatetimeIndex(
        ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
    )
    fills = [
        _fill("2024-01-01", "BUY", 10, 100.0),  # before first session → opening position
        _fill("2024-01-03", "SELL", 4, 101.0),
        _fill("2024-01-05", "BUY", 1, 102.0),  # same-session net: +1 −1 = 0
        _fill("2024-01-05", "SELL", 1, 102.5),
        _fill("2024-01-09", "BUY", 99, 103.0),  # after last session → ignored
    ]
    positions = rpb.fills_to_position_series(fills, sessions)
    assert positions.tolist() == [10.0, 6.0, 6.0, 6.0, 6.0]
    assert positions.index.equals(sessions)


def test_signals_from_positions_signs():
    positions = pd.Series([10.0, -3.0, 0.0], index=pd.RangeIndex(3))
    signals = rpb.signals_from_positions(positions)
    assert signals.tolist() == [1, -1, 0]
    assert set(signals.unique()).issubset({-1, 0, 1})


def test_live_growth_multiple_round_trip():
    # Buy 10 @ 100 (base 1000), sell 10 @ 110 → terminal cash 1100, flat → 1.1.
    fills = [_fill("2024-01-02", "BUY", 10, 100.0), _fill("2024-01-04", "SELL", 10, 110.0)]
    closes = pd.Series([100.0, 105.0, 110.0])
    assert rpb.live_growth_multiple(fills, closes) == pytest.approx(1.1)


def test_live_growth_multiple_values_open_position_at_last_close():
    fills = [_fill("2024-01-02", "BUY", 10, 100.0)]
    closes = pd.Series([100.0, 120.0])
    assert rpb.live_growth_multiple(fills, closes) == pytest.approx(1.2)


def test_live_growth_multiple_no_fills_is_nan():
    assert np.isnan(rpb.live_growth_multiple([], pd.Series([100.0])))


def test_run_live_replay_insufficient_accrual_raises():
    # Two fill sessions < the pinned 5-session floor → an honest refusal that
    # names the accrual prerequisites, never a vacuous verdict.
    orders = [
        _fake_order("2024-01-03", "buy", "10", "100.0"),
        _fake_order("2024-01-04", "sell", "10", "101.0"),
    ]
    with pytest.raises(rpb.InsufficientLiveAccrualError, match="C6-M2-LIVE-ACCRUAL"):
        rpb.run_live_replay(
            _FakeTradingClient(orders),
            ["SPY"],
            window=("2024-01-01", "2024-02-01"),
            prices_loader=lambda syms, window: {},
        )


def test_run_live_replay_unexplained_residual_is_load_bearing():
    # Fills on 5 distinct sessions whose realized multiple is INDEPENDENT of the
    # simulate() paper config: the decomposition can no longer close the gap, so
    # a genuine unexplained residual surfaces and fails the gate — exactly the
    # forward drift contract this task makes real. A below-floor second symbol is
    # skipped without blocking the qualifying one.
    prices = _ohlcv(n=60)
    days = [str(prices.index[i].date()) for i in (5, 10, 15, 20, 25)]
    orders = [
        _fake_order(days[0], "buy", "10", "100.0"),
        _fake_order(days[1], "sell", "10", "104.0"),
        _fake_order(days[2], "buy", "10", "99.0"),
        _fake_order(days[3], "sell", "10", "103.0"),
        _fake_order(days[4], "buy", "10", "101.0"),
        _fake_order(days[0], "buy", "5", "100.0", symbol="AAPL"),  # 1 session → skipped
    ]
    client = _FakeTradingClient(orders)
    window = (str(prices.index[0].date()), str(prices.index[-1].date()))
    results = rpb.run_live_replay(
        client,
        ["SPY", "AAPL"],
        window=window,
        prices_loader=lambda syms, w: {"SPY": prices},
    )
    assert set(results) == {"SPY"}
    r = results["SPY"]
    fills = [f for f in rpb.fetch_paper_fills(client, ["SPY"]) if f.symbol == "SPY"]
    # The paper multiple is the live fill-log multiple, not a simulate() output.
    assert r.paper_multiple == pytest.approx(rpb.live_growth_multiple(fills, prices["close"]))
    # The config-toggle decomposition still names only commission…
    assert set(r.components) == {"commission_per_share"}
    # …so the independent live multiple leaves a REAL unexplained residual → FAIL.
    assert abs(r.unexplained) > recon.UNEXPLAINED_EPS
    assert r.passed is False
    assert r.n_trades > 0


def test_format_report_labels_the_paper_source():
    prices = _ohlcv()
    signals = _alternating_signals(prices.index)
    result = recon.g2_reconciliation_gate_report(prices, signals)
    default_report = rpb.format_reconciliation_report({"SPY": result})
    assert rpb.DEFAULT_PAPER_SOURCE in default_report
    live_report = rpb.format_reconciliation_report(
        {"SPY": result}, paper_source=rpb.LIVE_PAPER_SOURCE
    )
    assert "LIVE Alpaca paper account fill log" in live_report


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
