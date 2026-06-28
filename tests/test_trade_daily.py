"""Unit tests for the C6-M2 daily executor (``scripts/trade_daily.py``).

Coverage mirrors the C2 module split (METHODOLOGY §15): the pure allocator core —
equal-weight sizing, per-symbol netting + clamp, and the G2a/G2b gate functions —
is asserted directly on synthetic inputs with no lake or network access; the G3
daily-loop is driven through a fake bridge + injected freshness/signal/price/
capital callables so the position-state round-trip and the non-zero-exit-on-stale
behaviour are covered offline.

The harness lives in ``scripts/trade_daily.py`` (the pinned C6-M2 deliverable
path); the repo tests scripts by import, exactly as ``tests/test_reconciliation.py``
does for ``scripts/reconcile_paper_backtest.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Import the deliverable script as a module (it is not on the package path).
# Register in sys.modules before exec so dataclass annotation resolution can find
# the module by name (dataclasses looks up cls.__module__ in sys.modules).
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "trade_daily.py"
_spec = importlib.util.spec_from_file_location("trade_daily", _SCRIPT)
td = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules[_spec.name] = td
_spec.loader.exec_module(td)

from quant.execution.lean_bridge import (  # noqa: E402
    PLACEHOLDER_QTY,
    TargetSignal,
    backtest_path_target_position,
    derive_target_position,
    load_position_state,
)
from quant.execution.strategy_registry import StrategySpec, load_registry  # noqa: E402


# ─── Fixtures / helpers ─────────────────────────────────────────────────────────


def _spec_for(
    sid: str = "s1",
    *,
    universe=("SPY",),
    max_position: float = 1.0,
    enabled: bool = True,
) -> StrategySpec:
    """A valid placeholder StrategySpec (ARIMA, equal-weight, always-pass)."""
    return StrategySpec(
        id=sid,
        display_name=sid.upper(),
        description="a test strategy",
        model_ref="arima_baseline",
        feature_set_ref=[],
        target_ref="next_bar_return",
        universe=list(universe),
        decision_rule="sign",
        risk_limits={"max_position": max_position, "max_drawdown_stop": None},
        cadence="daily",
        broker="alpaca_paper",
        enabled=enabled,
        provenance="placeholder",
        created_at="2026-06-28T00:00:00Z",
    )


def _sig(symbol: str, target_position: int, forecast: float = 0.01) -> TargetSignal:
    return TargetSignal(symbol, pd.Timestamp("2026-06-27"), forecast, target_position)


def _ohlcv(n: int = 120, seed: int = 0, base: float = 100.0) -> pd.DataFrame:
    """A gently-trending synthetic OHLCV frame with finite, positive prices."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    close = base + np.cumsum(rng.normal(0.05, 1.0, n))
    close = np.maximum(close, 1.0)
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


class _FakeBridge:
    """Records placed targets; reports holdings consistent with them. No network."""

    def __init__(self, equity: float = 100_000.0) -> None:
        self._holdings: dict[str, float] = {}
        self.placed: list[tuple[str, int, float]] = []
        self._equity = equity

    def account_summary(self):
        from quant.execution.lean_bridge import AccountSummary

        return AccountSummary("PA123", "ACTIVE", "0", "0", str(self._equity))

    def current_positions(self) -> dict[str, float]:
        return dict(self._holdings)

    def place_target(self, order) -> dict:
        self.placed.append((order.symbol, order.target_position, order.qty))
        self._holdings[order.symbol] = float(order.target_position) * order.qty
        return {"symbol": order.symbol, "submitted": True}


def _all_fresh(now=None):
    """A freshness_fn stub: every feed fresh (no alerts)."""

    class _S:
        is_alert = False
        name = "ok"

    return [_S()]


def _stale(now=None):
    """A freshness_fn stub: one stale feed (an alert)."""

    class _S:
        is_alert = True
        name = "tiingo"

    return [_S()]


# ─── Pinned constants (METHODOLOGY §1/§2) + drift contract with C2-M3 (§6) ──────


def test_pinned_constants():
    assert td.G2A_MAX_MISMATCHES == 0
    assert td.G2B_MAX_RELATIVE_DELTA == 0.01
    assert td.G3_MIN_CYCLES == 5


def test_g2b_tolerance_locked_to_c2m3():
    """The G2b tolerance + paper sizing params must equal C2-M3's (no new constant)."""
    _recon_path = Path(__file__).resolve().parents[1] / "scripts" / "reconcile_paper_backtest.py"
    _s = importlib.util.spec_from_file_location("reconcile_paper_backtest", _recon_path)
    rpb = importlib.util.module_from_spec(_s)
    sys.modules[_s.name] = rpb
    _s.loader.exec_module(rpb)

    assert td.G2B_MAX_RELATIVE_DELTA == rpb.G2_MAX_RELATIVE_DELTA
    assert td.G3_MIN_CYCLES == rpb.G3_MIN_CYCLES
    # Slippage + liquidity (the share-count axes) match the paper cost model.
    assert td.PAPER_SLIPPAGE_BPS == rpb.PAPER_COST_MODEL["slippage_bps"]
    assert td.PAPER_LIQUIDITY_CAP == rpb.PAPER_COST_MODEL["liquidity_cap"]


# ─── equal_weight_shares (the C2-M2-SIZING-PARITY rule) ─────────────────────────


def test_equal_weight_shares_matches_int_cash_over_price():
    assert td.equal_weight_shares(10_000.0, 100.0) == 100
    assert td.equal_weight_shares(10_050.0, 100.0) == 100  # integer truncation
    assert td.equal_weight_shares(99.0, 100.0) == 0


def test_equal_weight_shares_guards_bad_price():
    assert td.equal_weight_shares(10_000.0, 0.0) == 0
    assert td.equal_weight_shares(10_000.0, -5.0) == 0
    assert td.equal_weight_shares(10_000.0, float("nan")) == 0


# ─── size_strategy ──────────────────────────────────────────────────────────────


def test_size_strategy_equal_weight_split_across_universe():
    spec = _spec_for(universe=("SPY", "QQQ"))
    signals = {"SPY": _sig("SPY", 1), "QQQ": _sig("QQQ", 1)}
    prices = {"SPY": 100.0, "QQQ": 200.0}
    sized = td.size_strategy(spec, signals, capital_budget=20_000.0, prices=prices)
    # Budget 20k / 2 symbols = 10k each; SPY @ ~100 → ~99 sh (slippage), QQQ @ 200 → ~49.
    assert sized["SPY"].target_position == 1
    assert sized["SPY"].shares == pytest.approx(99.0)  # int(10000 / (100*1.0005))
    assert sized["QQQ"].shares == pytest.approx(49.0)  # int(10000 / (200*1.0005))
    assert sized["SPY"].notional > 0


def test_size_strategy_short_is_signed_negative():
    spec = _spec_for(universe=("SPY",))
    sized = td.size_strategy(spec, {"SPY": _sig("SPY", -1)}, 10_000.0, {"SPY": 100.0})
    assert sized["SPY"].target_position == -1
    assert sized["SPY"].shares < 0
    assert sized["SPY"].notional < 0


def test_size_strategy_flat_signal_deploys_nothing():
    spec = _spec_for(universe=("SPY",))
    sized = td.size_strategy(spec, {"SPY": _sig("SPY", 0)}, 10_000.0, {"SPY": 100.0})
    assert sized["SPY"].shares == 0.0
    assert sized["SPY"].notional == 0.0


def test_size_strategy_skips_symbol_without_price_or_signal():
    spec = _spec_for(universe=("SPY", "QQQ"))
    sized = td.size_strategy(spec, {"SPY": _sig("SPY", 1)}, 10_000.0, {"SPY": 100.0})
    assert "QQQ" not in sized  # no signal/price → skipped


def test_size_strategy_rejects_non_placeholder_method():
    spec = _spec_for()
    object.__setattr__(spec.sizing_policy, "method", "vol_target")  # simulate a C3 method
    with pytest.raises(NotImplementedError, match="fully_invested_equal_weight"):
        td.size_strategy(spec, {"SPY": _sig("SPY", 1)}, 10_000.0, {"SPY": 100.0})


# ─── net_targets (combination: net + clamp) ─────────────────────────────────────


def test_net_targets_single_strategy_direction_is_signal_sign():
    spec = _spec_for(universe=("SPY",))
    sized = td.size_strategy(spec, {"SPY": _sig("SPY", 1)}, 10_000.0, {"SPY": 100.0})
    nets = td.net_targets([spec], {spec.id: sized})
    assert nets["SPY"].target_position == 1
    assert nets["SPY"].shares == pytest.approx(99.0)  # |net shares|, unsigned


def test_net_targets_opposing_strategies_net_to_residual():
    # Two strategies, equal budget, identical price: one long one short → near-flat.
    a = _spec_for("a", universe=("SPY",))
    b = _spec_for("b", universe=("SPY",))
    budgets = {"a": 0.5, "b": 0.5}
    sized_a = td.size_strategy(a, {"SPY": _sig("SPY", 1)}, 10_000.0, {"SPY": 100.0})
    sized_b = td.size_strategy(b, {"SPY": _sig("SPY", -1)}, 10_000.0, {"SPY": 100.0})
    nets = td.net_targets([a, b], {"a": sized_a, "b": sized_b}, budgets=budgets)
    # Budget-weighted vote: 0.5*(+1) + 0.5*(-1) = 0 → flat direction.
    assert nets["SPY"].target_position == 0
    assert nets["SPY"].shares == 0.0


def test_net_targets_same_direction_shares_sum():
    a = _spec_for("a", universe=("SPY",))
    b = _spec_for("b", universe=("SPY",))
    sized_a = td.size_strategy(a, {"SPY": _sig("SPY", 1)}, 10_000.0, {"SPY": 100.0})
    sized_b = td.size_strategy(b, {"SPY": _sig("SPY", 1)}, 10_000.0, {"SPY": 100.0})
    nets = td.net_targets([a, b], {"a": sized_a, "b": sized_b})
    assert nets["SPY"].target_position == 1
    assert nets["SPY"].shares == pytest.approx(198.0)  # 99 + 99


def test_net_targets_clamp_zeroes_subunit_cap():
    # max_position < 1 forbids the integral unit position → clamped to flat.
    spec = _spec_for(universe=("SPY",), max_position=0.5)
    sized = td.size_strategy(spec, {"SPY": _sig("SPY", 1)}, 10_000.0, {"SPY": 100.0})
    nets = td.net_targets([spec], {spec.id: sized})
    assert nets["SPY"].target_position == 0
    assert nets["SPY"].shares == 0.0


# ─── G2a single-strategy parity gate ────────────────────────────────────────────


def test_g2a_parity_passes_on_real_registry_placeholder():
    spec = load_registry()["arima_placeholder"]
    forecasts = [0.01, -0.02, 0.0, 1e-9, -1e-9, 5.0, -3.0]
    report = td.single_strategy_parity_report(spec, forecasts)
    assert report.passed is True
    assert report.n_mismatches == 0
    assert report.n_checked == len(forecasts)


def test_g2a_direction_matches_backtest_path_per_forecast():
    spec = _spec_for(universe=("SPY",))
    for f in [0.5, -0.5, 0.0]:
        report = td.single_strategy_parity_report(spec, [f])
        assert report.n_mismatches == 0
        # Sanity: the two independent mappings agree on this forecast.
        assert derive_target_position(f) == backtest_path_target_position(f)


def test_g2a_empty_forecasts_cannot_pass():
    spec = _spec_for(universe=("SPY",))
    report = td.single_strategy_parity_report(spec, [])
    assert report.passed is False  # no parity surface to assert


# ─── G2b sizing reconciliation gate ─────────────────────────────────────────────


def test_g2b_sizing_reconciles_within_tolerance():
    prices = {"SPY": _ohlcv(seed=1), "QQQ": _ohlcv(seed=2, base=200.0)}
    results = td.sizing_reconciliation_report(prices, per_symbol_capital=50_000.0)
    assert results  # at least one symbol reconciled
    for sym, r in results.items():
        assert r.passed is True, (sym, r.relative_delta)
        assert abs(r.relative_delta) <= td.G2B_MAX_RELATIVE_DELTA
        assert r.simulator_notional > 0


def test_g2b_allocator_notional_tracks_simulator():
    prices = _ohlcv(seed=3)
    sim = td._simulator_entry_notional(prices, 50_000.0)
    alloc = td._allocator_entry_notional(prices, 50_000.0)
    assert sim > 0 and alloc > 0
    assert abs(alloc / sim - 1.0) <= td.G2B_MAX_RELATIVE_DELTA


def test_g2b_skips_symbol_with_no_trade():
    # A 1-bar frame can never place an entry (fill needs bar t+1) → skipped.
    one_bar = _ohlcv(n=1)
    results = td.sizing_reconciliation_report({"SPY": one_bar}, per_symbol_capital=50_000.0)
    assert results == {}


# ─── G3 daily-loop liveness (round-trip + freshness gate) ───────────────────────


def _signal_fn_long(spec, asof):
    return {sym: _sig(sym, 1) for sym in spec.universe}


def _price_fn_const(symbols, asof):
    return {sym: 100.0 for sym in symbols}


def test_run_trading_cycle_places_orders_and_persists(tmp_path):
    bridge = _FakeBridge()
    registry = {"s1": _spec_for("s1", universe=("SPY", "QQQ"))}
    state_path = tmp_path / "state.json"
    result = td.run_trading_cycle(
        "2026-06-27",
        bridge,
        registry,
        state_path,
        freshness_fn=_all_fresh,
        signal_fn=_signal_fn_long,
        price_fn=_price_fn_const,
    )
    assert set(result.targets) == {"SPY", "QQQ"}
    assert all(t.target_position == 1 for t in result.targets.values())
    # Orders were placed and state persisted (the round-trip file exists + reloads).
    assert len(bridge.placed) == 2
    assert load_position_state(state_path) == result.state


def test_run_trading_loop_state_round_trips_five_cycles(tmp_path):
    bridge = _FakeBridge()
    registry = {"s1": _spec_for("s1", universe=("SPY",))}
    state_path = tmp_path / "state.json"
    asofs = ["2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25", "2026-06-26"]
    results = td.run_trading_loop(
        asofs,
        bridge,
        registry,
        state_path,
        freshness_fn=_all_fresh,
        signal_fn=_signal_fn_long,
        price_fn=_price_fn_const,
    )
    assert len(results) == td.G3_MIN_CYCLES
    # Each cycle's persisted state is reloaded at the next cycle's open: the final
    # on-disk state matches the last returned state (the gateable half of G3).
    assert load_position_state(state_path) == results[-1].state


def test_run_trading_cycle_aborts_on_stale_feed(tmp_path):
    bridge = _FakeBridge()
    registry = {"s1": _spec_for("s1", universe=("SPY",))}
    with pytest.raises(td.FreshnessError, match="tiingo"):
        td.run_trading_cycle(
            "2026-06-27",
            bridge,
            registry,
            tmp_path / "state.json",
            freshness_fn=_stale,
            signal_fn=_signal_fn_long,
            price_fn=_price_fn_const,
        )
    # The cycle aborted before placing any order (never trades on stale data).
    assert bridge.placed == []


def test_run_trading_cycle_uses_bridge_equity_as_capital(tmp_path):
    # A bigger account → bigger equal-weight share count (capital read from bridge).
    big = _FakeBridge(equity=1_000_000.0)
    small = _FakeBridge(equity=10_000.0)
    registry = {"s1": _spec_for("s1", universe=("SPY",))}
    rb = td.run_trading_cycle(
        "2026-06-27", big, registry, tmp_path / "b.json",
        freshness_fn=_all_fresh, signal_fn=_signal_fn_long, price_fn=_price_fn_const,
    )
    rs = td.run_trading_cycle(
        "2026-06-27", small, registry, tmp_path / "s.json",
        freshness_fn=_all_fresh, signal_fn=_signal_fn_long, price_fn=_price_fn_const,
    )
    assert rb.targets["SPY"].shares > rs.targets["SPY"].shares


def test_run_trading_cycle_no_enabled_strategies_is_a_clean_noop(tmp_path):
    bridge = _FakeBridge()
    registry = {"s1": _spec_for("s1", enabled=False)}
    result = td.run_trading_cycle(
        "2026-06-27", bridge, registry, tmp_path / "state.json",
        freshness_fn=_all_fresh, signal_fn=_signal_fn_long, price_fn=_price_fn_const,
    )
    assert result.targets == {}
    assert bridge.placed == []


# ─── capital + monitor wiring helpers ───────────────────────────────────────────


def test_bridge_capital_reads_account_equity():
    assert td._bridge_capital(_FakeBridge(equity=250_000.0)) == pytest.approx(250_000.0)


def test_bridge_capital_falls_back_when_no_account():
    class _NoAccount:
        pass  # no account_summary → fallback to DEFAULT_CAPITAL

    assert td._bridge_capital(_NoAccount()) == td.DEFAULT_CAPITAL


def test_load_monitor_returns_the_real_monitor_callable():
    monitor = td._load_monitor()
    assert callable(monitor)
    assert monitor.__name__ == "monitor"


# ─── _strategy_signal dispatch (the documented extension point) ─────────────────


def test_strategy_signal_rejects_unknown_model():
    spec = _spec_for()
    object.__setattr__(spec, "model_ref", "gbm")  # no signal path wired yet
    with pytest.raises(NotImplementedError, match="model_ref"):
        td._strategy_signal(spec, pd.Timestamp("2026-06-27"))


# ─── Closes C2-M2-SIZING-PARITY: the bridge no longer trades a fixed 1 share ────


def test_sizing_is_no_longer_the_fixed_placeholder_qty(tmp_path):
    # The executor now sizes by capital fraction, not PLACEHOLDER_QTY (=1).
    bridge = _FakeBridge(equity=100_000.0)
    registry = {"s1": _spec_for("s1", universe=("SPY",))}
    result = td.run_trading_cycle(
        "2026-06-27", bridge, registry, tmp_path / "state.json",
        freshness_fn=_all_fresh, signal_fn=_signal_fn_long, price_fn=_price_fn_const,
    )
    assert result.targets["SPY"].shares > PLACEHOLDER_QTY  # ~999 shares, not 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
