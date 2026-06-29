"""Unit tests for the C2-M2 execution bridge (``quant.execution.lean_bridge``).

Coverage mirrors the C2-M1 hello-world test split (METHODOLOGY §15): the pure
logic — the shared sign mapping, the G1 parity gate, order planning, order
building, and position-state round-trip — is asserted directly; the network
adapter (``AlpacaPaperBridge``) is exercised against a fake client so the
order-path logic is covered without touching the live paper API. ``daily_signal``
is driven through a monkeypatched ``get_pit_panel`` so it fits a real ARIMA on a
synthetic panel with no lake or network access.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from quant.execution import lean_bridge as lb


# ─── derive_target_position: the shared parity rule ───────────────────────────


def test_derive_target_position_signs():
    assert lb.derive_target_position(0.01) == 1
    assert lb.derive_target_position(-0.01) == -1
    assert lb.derive_target_position(0.0) == 0


def test_derive_target_position_nonfinite_is_flat():
    # Hardening guard — never fires inside the G1 window (ARIMA forecasts are
    # finite), so it cannot perturb parity.
    assert lb.derive_target_position(float("nan")) == 0
    assert lb.derive_target_position(float("inf")) == 0


def test_bridge_and_backtest_mappings_agree_on_finite_forecasts():
    # G1's structural basis: the bridge mapping equals the independently-coded
    # backtest mapping for every finite forecast.
    rng = np.random.default_rng(0)
    forecasts = np.concatenate([rng.normal(0, 0.02, 500), np.array([0.0, 1e-12, -1e-12])])
    for f in forecasts:
        assert lb.derive_target_position(float(f)) == lb.backtest_path_target_position(float(f))


# ─── G1 signal-parity gate ─────────────────────────────────────────────────────


def test_signal_parity_gate_passes_when_all_match():
    checks = [(1, 1), (-1, -1), (0, 0), (1, 1)]
    result = lb.signal_parity_gate_report(checks)
    assert result.n_checked == 4
    assert result.n_mismatches == 0
    assert result.passed is True


def test_signal_parity_gate_fails_on_any_mismatch():
    checks = [(1, 1), (1, -1), (0, 0)]
    result = lb.signal_parity_gate_report(checks)
    assert result.n_mismatches == 1
    assert result.passed is False  # pinned threshold is 0


def test_signal_parity_gate_empty_cannot_pass():
    result = lb.signal_parity_gate_report([])
    assert result.n_checked == 0
    assert result.passed is False


def test_signal_parity_gate_threshold_is_pinned_to_zero():
    # Drift contract (METHODOLOGY §6): the gate default is the pinned constant.
    assert lb.G1_MAX_MISMATCHES == 0
    assert lb.signal_parity_gate_report([(1, -1)]).passed is False


# ─── G1 reconciled against the ACTUAL harness signal path (C2-M2-G1-HARNESS-EXACT) ─


class _PinnedForecastModel:
    """Fixture model whose ``predict`` returns the first feature column verbatim.

    The harness derives a per-symbol signal as ``np.sign(raw_pred).astype(int)``
    where ``raw_pred = model.predict(X_test)``. Echoing column 0 lets the test
    inject a *pinned* forecast spread at known dates and read back the exact
    signal the running harness produced from it — no reimplementation of the
    sign step in the test.
    """

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:  # noqa: D401 - stub
        pass

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.asarray(X, dtype=float)[:, 0]


def test_bridge_mapping_matches_actual_harness_signal_derivation(monkeypatch):
    """G1, reconciled against the REAL engine (C2-M2-G1-HARNESS-EXACT).

    Instead of comparing :func:`derive_target_position` against the in-module
    reimplementation :func:`backtest_path_target_position`, this feeds a pinned
    forecast spread through ``run_portfolio_backtest`` and captures the signals
    the harness ACTUALLY hands to ``simulate`` (``harness.py`` ``np.sign(raw_pred)
    .astype(int)``). Each captured harness signal is then reconciled against the
    bridge mapping via the live :func:`signal_parity_gate_report` gate. A drift
    between the bridge and the running harness sign step now fails this test —
    which the prior reimplementation-vs-reimplementation check could not catch.
    """
    from quant.backtest import harness as hb

    # Shared, identical forecast spread across symbols so a captured signal at a
    # given date maps to one known forecast regardless of which symbol produced
    # it. The [+, -, 0] cycle guarantees all three signs (+1, -1, 0) appear.
    n = 300
    dates = pd.bdate_range("2018-01-02", periods=n)
    spread = np.tile([0.02, -0.02, 0.0], n // 3 + 1)[:n].astype(float)
    forecast_by_date = pd.Series(spread, index=dates)

    symbols = ["AAPL", "MSFT"]
    rng = np.random.default_rng(0)
    features_by_symbol: dict[str, pd.DataFrame] = {}
    labels_by_symbol: dict[str, pd.Series] = {}
    prices_by_symbol: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        # Column 0 is the pinned forecast the model echoes; the rest is noise.
        feats = pd.DataFrame(
            {"f0": spread, "f1": rng.standard_normal(n)},
            index=dates,
        )
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n)))
        prices = pd.DataFrame(
            {
                "open": close,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": np.full(n, 1_000_000.0),
            },
            index=dates,
        )
        features_by_symbol[sym] = feats
        labels_by_symbol[sym] = prices["close"].shift(-1) / prices["close"] - 1.0
        prices_by_symbol[sym] = prices

    # Capture the signals the harness actually feeds to simulate(), tagged by the
    # forecast that produced each one (looked up by date from the shared spread).
    captured_pairs: list[tuple[int, int]] = []

    def _fake_simulate(prices, signals, **kwargs):
        for ts, harness_signal in signals.items():
            forecast = float(forecast_by_date.loc[ts])
            # (bridge_target, actual_harness_signal) — exactly the G1 check shape.
            captured_pairs.append((lb.derive_target_position(forecast), int(harness_signal)))
        eq = pd.Series(
            100.0 * (1.0 + 0.001 * np.arange(len(signals))), index=signals.index
        )
        tlog = pd.DataFrame(
            columns=["date", "entry_price", "exit_price", "shares",
                     "gross_pnl", "commission", "net_pnl"]
        )
        return eq, tlog

    monkeypatch.setattr(hb, "simulate", _fake_simulate)

    hb.run_portfolio_backtest(
        model=_PinnedForecastModel(),
        features_by_symbol=features_by_symbol,
        labels_by_symbol=labels_by_symbol,
        prices_by_symbol=prices_by_symbol,
        train_window=150,
        test_window=50,
        step=50,
        label_horizon=1,
        embargo=3,
    )

    # The test must be meaningful: all three signal values must have been
    # exercised through the real harness path, else parity is trivially true.
    harness_signals = {pair[1] for pair in captured_pairs}
    assert harness_signals == {-1, 0, 1}, (
        f"expected all three signals from the harness path, got {harness_signals}"
    )

    # Reconcile via the live G1 gate: bridge target == actual harness signal,
    # pinned 0-mismatch threshold.
    result = lb.signal_parity_gate_report(captured_pairs)
    assert result.n_checked == len(captured_pairs)
    assert result.n_checked > 0
    assert result.n_mismatches == 0
    assert result.passed is True


# ─── plan_order: target position → signed-delta order ──────────────────────────


def test_plan_order_long_from_flat():
    intent = lb.plan_order("SPY", 1, 0.0)
    assert intent == lb.OrderIntent(symbol="SPY", side="BUY", qty=lb.PLACEHOLDER_QTY)


def test_plan_order_short_from_flat():
    intent = lb.plan_order("SPY", -1, 0.0)
    assert intent == lb.OrderIntent(symbol="SPY", side="SELL", qty=lb.PLACEHOLDER_QTY)


def test_plan_order_flat_from_long_closes():
    intent = lb.plan_order("SPY", 0, 1.0)
    assert intent == lb.OrderIntent(symbol="SPY", side="SELL", qty=1.0)


def test_plan_order_long_from_short_crosses():
    # current -1 share, target +1 share → BUY 2 to cross.
    intent = lb.plan_order("SPY", 1, -1.0)
    assert intent == lb.OrderIntent(symbol="SPY", side="BUY", qty=2.0)


def test_plan_order_already_at_target_is_noop():
    assert lb.plan_order("SPY", 1, lb.PLACEHOLDER_QTY) is None
    assert lb.plan_order("SPY", 0, 0.0) is None


# ─── build_market_order ────────────────────────────────────────────────────────


def test_build_market_order_buy():
    req = lb.build_market_order(lb.OrderIntent("AAPL", "BUY", 3.0))
    assert isinstance(req, MarketOrderRequest)
    assert req.symbol == "AAPL"
    assert req.qty == 3.0
    assert req.side == OrderSide.BUY
    assert req.time_in_force == TimeInForce.DAY


def test_build_market_order_sell():
    req = lb.build_market_order(lb.OrderIntent("AAPL", "SELL", 2.0))
    assert req.side == OrderSide.SELL


# ─── PositionState persistence ─────────────────────────────────────────────────


def test_position_state_round_trips(tmp_path):
    state = lb.PositionState(asof="2026-06-28T00:00:00+00:00", holdings={"SPY": 1.0, "AAPL": -2.0})
    path = tmp_path / "nested" / "state.json"
    lb.save_position_state(state, path)
    loaded = lb.load_position_state(path)
    assert loaded == state


def test_load_position_state_missing_returns_none(tmp_path):
    assert lb.load_position_state(tmp_path / "absent.json") is None


# ─── AlpacaPaperBridge against a fake client ───────────────────────────────────


class _FakeAccount:
    account_number = "PA_TEST_0002"
    status = "AccountStatus.ACTIVE"
    cash = "1000000"
    buying_power = "4000000"
    equity = "1000000"


class _FakePosition:
    def __init__(self, symbol: str, qty: str) -> None:
        self.symbol = symbol
        self.qty = qty


class _FakeOrder:
    id = "ord_xyz789"
    status = "accepted"


class _FakeClient:
    """Records submitted orders; never hits the network."""

    def __init__(self, positions: list[_FakePosition] | None = None) -> None:
        self._positions = positions or []
        self.submitted: list[MarketOrderRequest] = []

    def get_account(self) -> _FakeAccount:
        return _FakeAccount()

    def get_all_positions(self) -> list[_FakePosition]:
        return self._positions

    def submit_order(self, order: MarketOrderRequest) -> _FakeOrder:
        self.submitted.append(order)
        return _FakeOrder()


def test_bridge_account_summary_projects_fields():
    bridge = lb.AlpacaPaperBridge(_FakeClient())
    summary = bridge.account_summary()
    assert summary.account_number == "PA_TEST_0002"
    assert summary.equity == "1000000"


def test_bridge_current_positions_preserves_short_sign():
    client = _FakeClient([_FakePosition("SPY", "5"), _FakePosition("AAPL", "-3")])
    positions = lb.AlpacaPaperBridge(client).current_positions()
    assert positions == {"SPY": 5.0, "AAPL": -3.0}


def test_bridge_place_target_submits_when_off_target():
    client = _FakeClient()  # flat
    bridge = lb.AlpacaPaperBridge(client)
    result = bridge.place_target(lb.TargetOrder("SPY", target_position=1))
    assert result["submitted"] is True
    assert result["side"] == "BUY"
    assert len(client.submitted) == 1
    assert client.submitted[0].symbol == "SPY"
    assert client.submitted[0].side == OrderSide.BUY
    assert result["order_id"] == "ord_xyz789"


def test_bridge_place_target_noop_when_already_on_target():
    client = _FakeClient([_FakePosition("SPY", str(lb.PLACEHOLDER_QTY))])
    bridge = lb.AlpacaPaperBridge(client)
    result = bridge.place_target(lb.TargetOrder("SPY", target_position=1))
    assert result["submitted"] is False
    assert result["reason"] == "already at target"
    assert client.submitted == []


def test_bridge_place_target_flat_closes_existing_long():
    client = _FakeClient([_FakePosition("SPY", "1")])
    bridge = lb.AlpacaPaperBridge(client)
    result = bridge.place_target(lb.TargetOrder("SPY", target_position=0))
    assert result["submitted"] is True
    assert result["side"] == "SELL"
    assert client.submitted[0].side == OrderSide.SELL


# ─── LeanBridge is a documented, unimplemented swap ───────────────────────────


def test_lean_bridge_is_not_implemented():
    with pytest.raises(NotImplementedError, match="deferred future swap"):
        lb.LeanBridge()


# ─── daily_signal orchestration (monkeypatched reader; real ARIMA fit) ─────────


def _synthetic_panel(symbols, n=80):
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    panel = {}
    for i, sym in enumerate(symbols):
        # Distinct gently-trending closes per symbol so ARIMA fits cleanly and
        # the forecast is finite. No zeros, no NaN, monotonic index.
        close = 100.0 + np.cumsum(np.full(n, 0.1 + 0.01 * i)) + np.sin(np.arange(n) / 5.0)
        panel[sym] = pd.DataFrame(
            {
                "open": close,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": np.full(n, 1_000_000.0),
            },
            index=idx,
        )
    return panel


def test_daily_signal_emits_target_consistent_with_mapping(monkeypatch):
    symbols = ["SPY", "AAPL"]
    monkeypatch.setattr(lb, "get_pit_panel", lambda syms, asof, dataset: _synthetic_panel(syms))
    signals = lb.daily_signal("2024-03-21", symbols)
    assert set(signals) == {"SPY", "AAPL"}
    for sym, sig in signals.items():
        assert isinstance(sig, lb.TargetSignal)
        assert np.isfinite(sig.forecast)
        # The emitted target must equal the shared sign mapping of its forecast
        # (the in-process analog of the G1 parity contract).
        assert sig.target_position == lb.derive_target_position(sig.forecast)
        assert sig.target_position == lb.backtest_path_target_position(sig.forecast)


def test_daily_signal_skips_symbols_absent_from_lake(monkeypatch):
    # Reader returns only SPY; AAPL is requested but absent → omitted.
    monkeypatch.setattr(lb, "get_pit_panel", lambda syms, asof, dataset: _synthetic_panel(["SPY"]))
    signals = lb.daily_signal("2024-03-21", ["SPY", "AAPL"])
    assert set(signals) == {"SPY"}


def test_daily_signal_skips_insufficient_history(monkeypatch):
    # Only 20 bars → fewer than MIN_LABEL_OBS non-NaN labels → skipped.
    monkeypatch.setattr(
        lb, "get_pit_panel", lambda syms, asof, dataset: _synthetic_panel(syms, n=20)
    )
    signals = lb.daily_signal("2024-03-21", ["SPY"], min_label_obs=30)
    assert signals == {}


def test_daily_signal_rejects_empty_symbols(monkeypatch):
    monkeypatch.setattr(lb, "get_pit_panel", lambda syms, asof, dataset: {})
    with pytest.raises(ValueError, match="symbols must not be empty"):
        lb.daily_signal("2024-03-21", [])


def test_daily_signal_defaults_to_universe(monkeypatch):
    # symbols=None pulls settings.equity_universe; assert it is consulted.
    captured = {}

    def fake_panel(syms, asof, dataset):
        captured["syms"] = list(syms)
        return _synthetic_panel(["SPY"])

    monkeypatch.setattr(lb, "get_pit_panel", fake_panel)
    signals = lb.daily_signal("2024-03-21")
    from quant.config import settings

    assert captured["syms"] == list(settings.equity_universe)
    assert set(signals) == {"SPY"}


# ─── build_feature_row + from_settings seams ──────────────────────────────────


def test_build_feature_row_wraps_reader_and_features(monkeypatch):
    panel = _synthetic_panel(["SPY", "AAPL"])
    monkeypatch.setattr(lb, "get_pit_panel", lambda syms, asof, dataset: {"SPY": panel["SPY"]})
    captured = {}

    def fake_build_features(present, panel_arg, *, asof):
        captured["present"] = list(present)
        captured["asof"] = asof
        return {s: pd.DataFrame({"f": [1.0]}) for s in present}

    monkeypatch.setattr("quant.features.engineering.build_features", fake_build_features)
    feats = lb.build_feature_row(["SPY", "AAPL"], "2024-03-21")
    # Only SPY is present in the (mocked) lake → only SPY is featurised.
    assert captured["present"] == ["SPY"]
    assert set(feats) == {"SPY"}


def test_build_feature_row_empty_when_no_symbols_present(monkeypatch):
    monkeypatch.setattr(lb, "get_pit_panel", lambda syms, asof, dataset: {})
    assert lb.build_feature_row(["SPY"], "2024-03-21") == {}


def test_from_settings_pins_paper_endpoint(monkeypatch):
    captured = {}

    def fake_ctor(api_key, secret_key, paper):
        captured.update(api_key=api_key, secret_key=secret_key, paper=paper)
        return _FakeClient()

    monkeypatch.setattr("alpaca.trading.client.TradingClient", fake_ctor)
    bridge = lb.AlpacaPaperBridge.from_settings()
    assert isinstance(bridge, lb.AlpacaPaperBridge)
    assert captured["paper"] is True  # never live in C2


# ─── C2-M2-SIZING-PARITY: cash-fraction sizing matches the simulator ──────────


def _flat_price_frame(n=5, open_px=100.0, volume=1e12):
    """OHLCV frame with constant opens and constant volume for a single entry."""
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "open": np.full(n, open_px),
            "high": np.full(n, open_px + 1.0),
            "low": np.full(n, open_px - 1.0),
            "close": np.full(n, open_px),
            "volume": np.full(n, float(volume)),
        },
        index=idx,
    )


def test_sizing_constants_match_simulator_defaults():
    # §6 drift contract: the bridge's pinned cost constants must equal the
    # Phase 1 simulator's signature defaults, else cash-fraction sizing would
    # silently diverge from the engine G2 reconciles against.
    import inspect

    from quant.backtest.simulator import simulate

    params = inspect.signature(simulate).parameters
    assert lb.SIM_SLIPPAGE_BPS == params["slippage_bps"].default
    assert lb.SIM_LIQUIDITY_CAP == params["liquidity_cap"].default


def test_simulator_position_qty_flat_is_zero():
    assert lb.simulator_position_qty(1_000_000.0, 100.0, 0) == 0


def test_simulator_position_qty_matches_simulator_cash_cap():
    # Cash-bound: liquidity not binding (huge volume). The bridge sizing must
    # equal the shares the simulator actually opens.
    from quant.backtest.simulator import simulate

    cash = 100_000.0
    prices = _flat_price_frame(volume=1e12)
    signals = pd.Series([1, 1, 1, 1, 1], index=prices.index)
    _, trade_log = simulate(prices, signals, initial_capital=cash)
    opened = int(trade_log.iloc[0]["shares"])
    assert opened > 1  # full-notional deployment, not the 1-share placeholder
    assert (
        lb.simulator_position_qty(cash, 100.0, 1, volume=1e12) == opened
    )


def test_simulator_position_qty_matches_simulator_liquidity_cap():
    # Liquidity-bound: huge cash, tiny volume so the 10% cap binds.
    from quant.backtest.simulator import simulate

    cash = 1e12
    prices = _flat_price_frame(volume=1000.0)
    signals = pd.Series([1, 1, 1, 1, 1], index=prices.index)
    _, trade_log = simulate(prices, signals, initial_capital=cash)
    opened = int(trade_log.iloc[0]["shares"])
    assert opened == 100  # int(1000 * 0.10)
    assert lb.simulator_position_qty(cash, 100.0, 1, volume=1000.0) == opened


def test_simulator_position_qty_short_uses_bid_slippage():
    # Long buys at the ask (open*(1+slip)); short sells at the bid
    # (open*(1-slip)), so a short deploys at least as many shares as a long.
    long_qty = lb.simulator_position_qty(100_000.0, 100.0, 1, volume=1e12)
    short_qty = lb.simulator_position_qty(100_000.0, 100.0, -1, volume=1e12)
    assert short_qty >= long_qty
    slip = lb.SIM_SLIPPAGE_BPS / 10_000.0
    assert short_qty == int(100_000.0 / (100.0 * (1.0 - slip)))
    assert long_qty == int(100_000.0 / (100.0 * (1.0 + slip)))


def test_simulator_position_qty_no_volume_skips_liquidity_cap():
    # volume=None → only the cash cap applies (paper account may lack a volume
    # read at order time); never silently caps at the placeholder.
    qty = lb.simulator_position_qty(100_000.0, 100.0, 1)
    slip = lb.SIM_SLIPPAGE_BPS / 10_000.0
    assert qty == int(100_000.0 / (100.0 * (1.0 + slip)))


def test_simulator_position_qty_nonpositive_inputs_are_zero():
    assert lb.simulator_position_qty(0.0, 100.0, 1) == 0
    assert lb.simulator_position_qty(100_000.0, 0.0, 1) == 0
    assert lb.simulator_position_qty(-1.0, 100.0, 1) == 0


def test_sized_target_order_uses_simulator_qty():
    order = lb.sized_target_order("SPY", 1, cash=100_000.0, ref_price=100.0, volume=1e12)
    assert isinstance(order, lb.TargetOrder)
    assert order.symbol == "SPY"
    assert order.target_position == 1
    assert order.qty == lb.simulator_position_qty(100_000.0, 100.0, 1, volume=1e12)


def test_bridge_place_sized_target_sizes_from_account_cash():
    # The fake account reports cash="1000000"; the bridge must size from it,
    # not from the 1-share placeholder.
    client = _FakeClient()  # flat
    bridge = lb.AlpacaPaperBridge(client)
    result = bridge.place_sized_target("SPY", target_position=1, ref_price=100.0)
    expected = lb.simulator_position_qty(1_000_000.0, 100.0, 1)
    assert expected > 1
    assert result["submitted"] is True
    assert result["side"] == "BUY"
    assert result["qty"] == expected
    assert client.submitted[0].qty == expected


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
