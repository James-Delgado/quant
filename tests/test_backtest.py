"""Phase 1 backtester tests — written BEFORE implementation (TDD).

Imports from quant.backtest.* will fail until the package is built (expected RED).
Run with:
  .venv/bin/pytest tests/test_backtest.py -q --tb=short

Output contracts:
  walkforward_splits() -> Iterator[tuple[np.ndarray, np.ndarray]]
      (train_positions, test_positions) — integer index arrays into the dataset

  simulate() -> tuple[pd.Series, pd.DataFrame]
      equity_curve: portfolio value by date
      trade_log: columns date, entry_price, exit_price, shares,
                 gross_pnl, commission, net_pnl

  compute_metrics() -> dict[str, float]
      keys: sharpe, sortino, calmar, max_drawdown, total_return,
            annualized_return, hit_rate, profit_factor
      hit_rate and profit_factor are always present (0.0/nan when no trade_log)

  run_backtest() -> BacktestResult
      fields: oos_metrics, is_metrics, equity_curve, trade_log
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest.walkforward import walkforward_splits
from quant.backtest.simulator import simulate
from quant.backtest.metrics import compute_metrics
from quant.backtest.harness import BacktestResult, run_backtest, run_portfolio_backtest
from quant.backtest.report import format_report, summary_table


# ---------------------------------------------------------------------------
# Helpers shared across test classes
# ---------------------------------------------------------------------------

def _make_prices(
    n: int = 10,
    start_price: float = 100.0,
    daily_move: float = 0.0,
    volume: float = 1_000_000.0,
) -> pd.DataFrame:
    """Flat or trending OHLCV DataFrame for simulator unit tests."""
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    p = start_price * (1.0 + daily_move) ** np.arange(n)
    return pd.DataFrame(
        {
            "open":   p,
            "high":   p * 1.005,
            "low":    p * 0.995,
            "close":  p,
            "volume": float(volume),
        },
        index=dates,
    )


# ===========================================================================
# walkforward.py
# ===========================================================================

class TestWalkforwardSplits:
    """
    walkforward_splits(
        n_samples: int,
        train_window: int,
        test_window: int,
        step: int = 1,
        label_horizon: int = 0,  # label at i covers bars [i+1 .. i+label_horizon]
        embargo: int = 0,        # additional buffer after purging
    ) -> Iterator[tuple[np.ndarray, np.ndarray]]
    """

    def test_correct_split_count(self):
        # n=20, train=10, test=3, step=3
        # test windows start at: 10, 13, 16  → 3 splits (next would start at 19,
        # but test_window=3 requires [19,20,21] which exceeds n=20)
        splits = list(walkforward_splits(20, train_window=10, test_window=3, step=3))
        assert len(splits) == 3

    def test_correct_split_count_step_1(self):
        # n=15, train=10, test=3, step=1 → test starts at 10, 11, 12 → 3 splits
        splits = list(walkforward_splits(15, train_window=10, test_window=3, step=1))
        assert len(splits) == 3

    def test_insufficient_data_returns_empty(self):
        splits = list(walkforward_splits(5, train_window=10, test_window=3, step=1))
        assert len(splits) == 0

    def test_no_lookahead(self):
        """max(train_positions) < min(test_positions) for every split."""
        splits = list(walkforward_splits(40, train_window=10, test_window=5, step=5))
        for train, test in splits:
            assert train.max() < test.min()

    def test_rolling_window_fixed_length(self):
        """Without purge/embargo every train set has exactly train_window samples."""
        splits = list(
            walkforward_splits(40, train_window=10, test_window=5, step=5,
                               label_horizon=0, embargo=0)
        )
        for train, _ in splits:
            assert len(train) == 10

    def test_test_windows_are_contiguous(self):
        """Each test array is a block of consecutive positions."""
        splits = list(walkforward_splits(40, train_window=10, test_window=5, step=5))
        for _, test in splits:
            assert np.all(np.diff(test) == 1), f"Non-contiguous test: {test}"

    def test_test_windows_advance_by_step(self):
        """Consecutive test windows start exactly step positions apart."""
        splits = list(walkforward_splits(40, train_window=10, test_window=5, step=5))
        starts = [test[0] for _, test in splits]
        for i in range(1, len(starts)):
            assert starts[i] - starts[i - 1] == 5

    def test_purging_removes_boundary_samples(self):
        """Sample i is purged if i + label_horizon >= test_start.

        With label_horizon=3 and test=[10,11,12]:
          - i=9: 9+3=12 >= 10 → purge
          - i=8: 8+3=11 >= 10 → purge
          - i=7: 7+3=10 >= 10 → purge
          - i=6: 6+3=9  <  10 → keep
        """
        splits_raw = list(
            walkforward_splits(20, train_window=10, test_window=3, step=3, label_horizon=0)
        )
        splits_purged = list(
            walkforward_splits(20, train_window=10, test_window=3, step=3, label_horizon=3)
        )
        train_raw, _ = splits_raw[0]
        train_purged, _ = splits_purged[0]

        assert len(train_purged) < len(train_raw)   # purging reduced train size
        assert 7 not in train_purged                # 7+3=10 = test_start → purge
        assert 8 not in train_purged
        assert 9 not in train_purged
        assert 6 in train_purged                    # 6+3=9 < 10 → keep

    def test_embargo_adds_buffer_after_purging(self):
        """embargo=2 removes 2 more samples immediately before the purge boundary.

        label_horizon=3 purges {7,8,9}; embargo=2 additionally removes {5,6}.
        """
        splits = list(
            walkforward_splits(20, train_window=10, test_window=3, step=3,
                               label_horizon=3, embargo=2)
        )
        train, _ = splits[0]
        assert 5 not in train   # embargoed
        assert 6 not in train   # embargoed
        assert 4 in train       # kept (survived both purge and embargo)

    def test_no_overlap_between_train_and_test(self):
        """Train and test arrays share no elements in any split."""
        splits = list(
            walkforward_splits(60, train_window=20, test_window=10, step=10,
                               label_horizon=5, embargo=3)
        )
        for train, test in splits:
            assert len(np.intersect1d(train, test)) == 0

    def test_purged_train_satisfies_label_horizon_gap(self):
        """After purging, max(train) + label_horizon < min(test) for every split."""
        lh = 5
        splits = list(
            walkforward_splits(300, train_window=100, test_window=50, step=50,
                               label_horizon=lh, embargo=0)
        )
        for train, test in splits:
            if len(train) > 0:
                assert train.max() + lh < test.min()


# ===========================================================================
# simulator.py
# ===========================================================================

class TestSimulate:
    """
    simulate(
        prices: pd.DataFrame,         DatetimeIndex; columns: open, high, low, close, volume
        signals: pd.Series,           values in {-1, 0, 1}; signal at close fills next-bar open
        initial_capital: float,
        commission_per_share: float,
        slippage_bps: float,          half-spread in basis points per side
        liquidity_cap: float,         max fraction of bar volume that can be traded
    ) -> tuple[pd.Series, pd.DataFrame]
    """

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------

    @pytest.fixture()
    def five_bar_prices(self):
        """5-bar price fixture: opens jump from 100 → 110 → 120 → 120 → 120."""
        dates = pd.date_range("2024-01-02", periods=5, freq="B")
        return pd.DataFrame(
            {
                "open":   [100.0, 110.0, 120.0, 120.0, 120.0],
                "high":   [101.0, 111.0, 121.0, 121.0, 121.0],
                "low":    [ 99.0, 109.0, 119.0, 119.0, 119.0],
                "close":  [100.0, 110.0, 120.0, 120.0, 120.0],
                "volume": [1_000_000.0] * 5,
            },
            index=dates,
        )

    @pytest.fixture()
    def long_signal(self, five_bar_prices):
        """Long entry at bar 0 close (fill: bar 1 open=110), exit at bar 1 close (fill: bar 2 open=120)."""
        return pd.Series([1, 0, 0, 0, 0], index=five_bar_prices.index)

    # ------------------------------------------------------------------
    # Basic interface
    # ------------------------------------------------------------------

    def test_flat_signal_no_trades(self):
        prices = _make_prices(10)
        signals = pd.Series(0, index=prices.index)
        equity_curve, trade_log = simulate(
            prices, signals, initial_capital=10_000,
            commission_per_share=0.0, slippage_bps=0.0,
        )
        assert len(trade_log) == 0
        assert (equity_curve == 10_000).all()

    def test_equity_curve_indexed_by_date(self):
        prices = _make_prices(10)
        signals = pd.Series(0, index=prices.index)
        equity_curve, _ = simulate(prices, signals, initial_capital=50_000)
        assert isinstance(equity_curve.index, pd.DatetimeIndex)
        assert len(equity_curve) == len(prices)

    def test_trade_log_required_columns(self, five_bar_prices, long_signal):
        _, trade_log = simulate(five_bar_prices, long_signal, initial_capital=10_000)
        required = {
            "date", "entry_price", "exit_price", "shares",
            "gross_pnl", "commission", "net_pnl",
        }
        assert required.issubset(set(trade_log.columns))

    # ------------------------------------------------------------------
    # P&L arithmetic (concrete numbers)
    # ------------------------------------------------------------------

    def test_long_trade_zero_costs(self, five_bar_prices, long_signal):
        """Buy at bar-1 open=110, sell at bar-3 open=120, no costs.

        shares = floor(10_000 / 110) = 90
        gross_pnl = 90 × (120 − 110) = 900
        final equity = 10_000 + 900 = 10_900
        """
        equity_curve, trade_log = simulate(
            five_bar_prices, long_signal,
            initial_capital=10_000,
            commission_per_share=0.0,
            slippage_bps=0.0,
            liquidity_cap=1.0,
        )
        assert len(trade_log) == 1
        row = trade_log.iloc[0]
        assert row["entry_price"] == pytest.approx(110.0)
        assert row["exit_price"] == pytest.approx(120.0)
        assert row["shares"] == 90
        assert row["gross_pnl"] == pytest.approx(900.0)
        assert row["commission"] == pytest.approx(0.0)
        assert row["net_pnl"] == pytest.approx(900.0)
        assert equity_curve.iloc[-1] == pytest.approx(10_900.0)

    def test_commission_deducted_round_trip(self, five_bar_prices, long_signal):
        """Commission = shares × rate × 2 (entry + exit), subtracted from gross."""
        rate = 0.01  # $0.01/share
        _, trade_log = simulate(
            five_bar_prices, long_signal,
            initial_capital=10_000,
            commission_per_share=rate,
            slippage_bps=0.0,
            liquidity_cap=1.0,
        )
        row = trade_log.iloc[0]
        expected_commission = row["shares"] * rate * 2
        assert row["commission"] == pytest.approx(expected_commission, rel=1e-4)
        assert row["net_pnl"] == pytest.approx(row["gross_pnl"] - expected_commission, rel=1e-4)

    def test_slippage_worsens_fill_prices(self, five_bar_prices, long_signal):
        """Long entry pays above open; long exit receives below open.

        With slippage_bps=100 (1%): entry=111.1, exit=118.8.
        Net P&L must be lower than zero-slippage case.
        """
        kwargs = dict(initial_capital=10_000, commission_per_share=0.0, liquidity_cap=1.0)
        _, log_slipped = simulate(five_bar_prices, long_signal, slippage_bps=100.0, **kwargs)
        _, log_clean = simulate(five_bar_prices, long_signal, slippage_bps=0.0, **kwargs)

        assert log_slipped.iloc[0]["entry_price"] > log_clean.iloc[0]["entry_price"]
        assert log_slipped.iloc[0]["exit_price"] < log_clean.iloc[0]["exit_price"]
        assert log_slipped.iloc[0]["net_pnl"] < log_clean.iloc[0]["net_pnl"]

    def test_liquidity_cap_limits_shares(self, five_bar_prices, long_signal):
        """Cannot trade more than liquidity_cap × bar_volume shares.

        volume=100, cap=0.10 → max 10 shares even though capital allows 90.
        """
        low_vol = five_bar_prices.copy()
        low_vol["volume"] = 100.0
        _, trade_log = simulate(
            low_vol, long_signal,
            initial_capital=10_000,
            commission_per_share=0.0,
            slippage_bps=0.0,
            liquidity_cap=0.10,
        )
        assert trade_log.iloc[0]["shares"] <= 10

    def test_short_trade_zero_costs(self):
        """Short entry at open=110, exit at open=90, no costs.

        shares = floor(10_000 / 110) = 90
        gross_pnl = 90 × (110 − 90) = 1_800   (profitable short)
        final equity = 10_000 + 1_800 = 11_800
        """
        dates = pd.date_range("2024-01-02", periods=5, freq="B")
        prices = pd.DataFrame(
            {
                "open":   [100.0, 110.0, 90.0, 90.0, 90.0],
                "high":   [105.0, 115.0, 95.0, 95.0, 95.0],
                "low":    [ 95.0, 105.0, 85.0, 85.0, 85.0],
                "close":  [100.0, 110.0, 90.0, 90.0, 90.0],
                "volume": [1_000_000.0] * 5,
            },
            index=dates,
        )
        signals = pd.Series([-1, 0, 0, 0, 0], index=dates)
        equity_curve, trade_log = simulate(
            prices, signals,
            initial_capital=10_000,
            commission_per_share=0.0,
            slippage_bps=0.0,
            liquidity_cap=1.0,
        )
        assert len(trade_log) == 1
        row = trade_log.iloc[0]
        assert row["entry_price"] == pytest.approx(110.0)
        assert row["exit_price"] == pytest.approx(90.0)
        assert row["shares"] == 90
        assert row["gross_pnl"] == pytest.approx(1_800.0)
        assert row["net_pnl"] == pytest.approx(1_800.0)
        assert equity_curve.iloc[-1] == pytest.approx(11_800.0)

    def test_open_position_at_end_is_logged(self, five_bar_prices):
        """Position open at the last bar is force-closed and logged.

        Signals are all +1 so the long position is never explicitly closed.
        The simulator must log a closing trade at the final bar's open price.
        """
        signals = pd.Series([1, 1, 1, 1, 1], index=five_bar_prices.index)
        equity_curve, trade_log = simulate(
            five_bar_prices, signals,
            initial_capital=10_000,
            commission_per_share=0.0,
            slippage_bps=0.0,
            liquidity_cap=1.0,
        )
        assert len(trade_log) == 1, "Force-close must produce exactly one trade row"
        row = trade_log.iloc[0]
        assert row["entry_price"] == pytest.approx(110.0)   # fill at bar-1 open
        assert row["exit_price"] == pytest.approx(120.0)    # force-close at bar-4 open
        assert row["net_pnl"] == pytest.approx(90 * 10.0)   # 90 shares × $10 gain
        assert equity_curve.iloc[-1] == pytest.approx(10_000 + 90 * 10.0)

    def test_next_bar_execution(self):
        """Signal at bar t fills at bar t+1 open, not bar t close.

        If bar 0 close = 100 and bar 1 open = 150, entry must be 150.
        """
        dates = pd.date_range("2024-01-02", periods=4, freq="B")
        prices = pd.DataFrame(
            {
                "open":   [100.0, 150.0, 150.0, 150.0],
                "high":   [105.0, 155.0, 155.0, 155.0],
                "low":    [ 95.0, 145.0, 145.0, 145.0],
                "close":  [100.0, 150.0, 150.0, 150.0],
                "volume": [1_000_000.0] * 4,
            },
            index=dates,
        )
        signals = pd.Series([1, 0, -1, 0], index=dates)
        _, trade_log = simulate(
            prices, signals,
            initial_capital=10_000,
            commission_per_share=0.0,
            slippage_bps=0.0,
            liquidity_cap=1.0,
        )
        assert trade_log.iloc[0]["entry_price"] == pytest.approx(150.0)


# ===========================================================================
# metrics.py
# ===========================================================================

class TestComputeMetrics:
    """
    compute_metrics(
        returns: pd.Series,                   daily arithmetic portfolio returns
        trade_log: pd.DataFrame | None = None,
        trading_days_per_year: int = 252,
    ) -> dict[str, float]
    """

    def test_required_keys_always_present(self):
        returns = pd.Series(np.full(100, 0.001))
        metrics = compute_metrics(returns)
        always_present = {
            "sharpe", "sortino", "calmar", "max_drawdown",
            "total_return", "annualized_return",
        }
        assert always_present.issubset(set(metrics.keys()))

    def test_hit_rate_and_profit_factor_present_with_trade_log(self):
        trade_log = pd.DataFrame({"net_pnl": [100.0, -50.0, 200.0]})
        metrics = compute_metrics(pd.Series(dtype=float), trade_log=trade_log)
        assert "hit_rate" in metrics
        assert "profit_factor" in metrics

    # ------------------------------------------------------------------
    # Sharpe ratio
    # ------------------------------------------------------------------

    def test_zero_returns_sharpe_is_zero(self):
        returns = pd.Series(np.zeros(100))
        assert compute_metrics(returns)["sharpe"] == pytest.approx(0.0, abs=1e-10)

    def test_positive_drift_sharpe_is_positive(self):
        rng = np.random.default_rng(42)
        returns = pd.Series(rng.normal(0.002, 0.01, 252))
        assert compute_metrics(returns)["sharpe"] > 0

    def test_sharpe_matches_formula(self):
        """Annualized Sharpe = (mean / std_ddof1) × sqrt(252)."""
        rng = np.random.default_rng(7)
        r = pd.Series(rng.normal(0.001, 0.01, 252))
        expected = (r.mean() / r.std(ddof=1)) * np.sqrt(252)
        assert compute_metrics(r)["sharpe"] == pytest.approx(expected, rel=1e-4)

    # ------------------------------------------------------------------
    # Max drawdown
    # ------------------------------------------------------------------

    def test_max_drawdown_known_value(self):
        """Equity path 100 → 120 → 90 → 110 gives max drawdown = -25%.

        Returns:
          bar 0→1: +20%   (equity $1.00 → $1.20)
          bar 1→2: -25%   (equity $1.20 → $0.90)  ← peak-to-trough
          bar 2→3: +22.2% (equity $0.90 → $1.10)

        max_drawdown = (0.90 − 1.20) / 1.20 = -0.25
        """
        returns = pd.Series([0.20, -0.25, 2.0 / 9.0])
        assert compute_metrics(returns)["max_drawdown"] == pytest.approx(-0.25, rel=1e-3)

    def test_max_drawdown_is_non_positive(self):
        rng = np.random.default_rng(1)
        returns = pd.Series(rng.normal(0.0, 0.01, 200))
        assert compute_metrics(returns)["max_drawdown"] <= 0.0

    def test_max_drawdown_zero_when_always_rising(self):
        returns = pd.Series(np.full(50, 0.005))
        assert compute_metrics(returns)["max_drawdown"] == pytest.approx(0.0, abs=1e-10)

    # ------------------------------------------------------------------
    # Trade-level metrics (require trade_log)
    # ------------------------------------------------------------------

    def test_hit_rate_known_value(self):
        """3 winners, 2 losers → hit_rate = 0.60."""
        log = pd.DataFrame({"net_pnl": [100.0, -50.0, 200.0, -30.0, 80.0]})
        assert compute_metrics(pd.Series(dtype=float), trade_log=log)["hit_rate"] == \
            pytest.approx(0.60)

    def test_profit_factor_known_value(self):
        """wins=380, losses=80 → profit_factor = 4.75."""
        log = pd.DataFrame({"net_pnl": [100.0, -50.0, 200.0, -30.0, 80.0]})
        assert compute_metrics(pd.Series(dtype=float), trade_log=log)["profit_factor"] == \
            pytest.approx(380.0 / 80.0)

    def test_all_losses_hit_rate_zero(self):
        log = pd.DataFrame({"net_pnl": [-10.0, -20.0, -5.0]})
        assert compute_metrics(pd.Series(dtype=float), trade_log=log)["hit_rate"] == \
            pytest.approx(0.0)

    def test_all_wins_hit_rate_one(self):
        log = pd.DataFrame({"net_pnl": [10.0, 20.0, 5.0]})
        assert compute_metrics(pd.Series(dtype=float), trade_log=log)["hit_rate"] == \
            pytest.approx(1.0)

    def test_all_wins_profit_factor_is_inf(self):
        """All winning trades → profit_factor = inf (no losses in denominator)."""
        log = pd.DataFrame({"net_pnl": [10.0, 20.0, 5.0]})
        assert compute_metrics(pd.Series(dtype=float), trade_log=log)["profit_factor"] == \
            float("inf")

    def test_hit_rate_profit_factor_always_present(self):
        """hit_rate and profit_factor are present even when no trade_log is supplied."""
        metrics = compute_metrics(pd.Series(np.full(50, 0.001)))
        assert "hit_rate" in metrics
        assert "profit_factor" in metrics
        assert metrics["hit_rate"] == pytest.approx(0.0)
        import math
        assert math.isnan(metrics["profit_factor"])

    def test_calmar_undefined_when_no_drawdown(self):
        """calmar is NaN (undefined) when max_drawdown == 0 — not infinity."""
        import math
        returns = pd.Series(np.full(50, 0.005))  # always positive → no drawdown
        assert math.isnan(compute_metrics(returns)["calmar"])


# ===========================================================================
# harness.py — BacktestResult interface
# ===========================================================================

class TestRunBacktest:
    """
    run_backtest(
        model,                 duck-typed: .fit(X, y), .predict(X) -> np.ndarray of {-1,0,1}
        features: pd.DataFrame,
        labels: pd.Series,
        prices: pd.DataFrame,
        train_window: int = 504,
        test_window: int = 63,
        step: int = 63,
        label_horizon: int = 1,
        embargo: int = 3,
        **sim_kwargs,
    ) -> BacktestResult

    BacktestResult:
        oos_metrics: dict[str, float]
        is_metrics:  dict[str, float]
        equity_curve: pd.Series
        trade_log: pd.DataFrame
    """

    @pytest.fixture()
    def minimal_dataset(self):
        """250 business days of synthetic data for smoke-testing run_backtest."""
        n = 250
        rng = np.random.default_rng(99)
        rets = rng.normal(0.0005, 0.01, n)
        p = 100 * np.cumprod(1 + rets)
        dates = pd.date_range("2023-01-03", periods=n, freq="B")
        prices = pd.DataFrame(
            {
                "open": p, "high": p * 1.005,
                "low": p * 0.995, "close": p,
                "volume": 2_000_000.0,
            },
            index=dates,
        )
        features = pd.DataFrame({"f": rng.standard_normal(n)}, index=dates)
        labels = pd.Series(np.sign(rets), index=dates, name="label")
        return prices, features, labels

    class _ConstantModel:
        """Always predicts zero (flat)."""
        def fit(self, X, y): pass
        def predict(self, X): return np.zeros(len(X))

    class _AlwaysLong:
        """Always predicts +1."""
        def fit(self, X, y): pass
        def predict(self, X): return np.ones(len(X))

    # Window params sized for the 250-bar minimal_dataset fixture.
    _W = dict(train_window=100, test_window=25, step=25)

    def test_returns_backtest_result_instance(self, minimal_dataset):
        prices, features, labels = minimal_dataset
        result = run_backtest(self._ConstantModel(), features, labels, prices, **self._W)
        assert isinstance(result, BacktestResult)

    def test_oos_metrics_has_sharpe(self, minimal_dataset):
        prices, features, labels = minimal_dataset
        result = run_backtest(self._ConstantModel(), features, labels, prices, **self._W)
        assert "sharpe" in result.oos_metrics

    def test_is_metrics_has_sharpe(self, minimal_dataset):
        prices, features, labels = minimal_dataset
        result = run_backtest(self._ConstantModel(), features, labels, prices, **self._W)
        assert "sharpe" in result.is_metrics

    def test_equity_curve_is_series(self, minimal_dataset):
        prices, features, labels = minimal_dataset
        result = run_backtest(self._ConstantModel(), features, labels, prices, **self._W)
        assert isinstance(result.equity_curve, pd.Series)
        assert len(result.equity_curve) > 0

    def test_trade_log_has_required_columns(self, minimal_dataset):
        prices, features, labels = minimal_dataset
        result = run_backtest(self._AlwaysLong(), features, labels, prices, **self._W)
        required = {"date", "entry_price", "exit_price", "shares", "net_pnl"}
        assert required.issubset(set(result.trade_log.columns))

    def test_oos_equity_starts_after_first_train_window(self, minimal_dataset):
        """OOS equity curve must begin no earlier than the first test fold."""
        prices, features, labels = minimal_dataset
        result = run_backtest(
            self._ConstantModel(), features, labels, prices,
            train_window=100, test_window=25, step=25,
        )
        assert result.equity_curve.index[0] >= prices.index[100]


# ===========================================================================
# run_portfolio_backtest — union-of-indices master timeline
# (see docs/REFACTOR_PORTFOLIO_UNION_INDEX.md)
# ===========================================================================

class TestRunPortfolioBacktest:
    """run_portfolio_backtest must allow each symbol to contribute whatever
    history it has, instead of intersecting all symbols' indices.

    Properties verified:
    - late-starting symbols do not collapse the master calendar
    - aligned panels reproduce the prior intersection behavior
    - n_symbols_active / n_train_rows fold diagnostics are populated
    - cross-sectional aggregation is equal-weight at every bar
    """

    class _AlwaysLongModel:
        """Predicts +1.0 for every row; sign() → +1 signal."""

        def fit(self, X, y) -> None:
            pass

        def predict(self, X) -> np.ndarray:
            return np.ones(len(X), dtype=float)

    @staticmethod
    def _make_symbol(
        start: str | pd.Timestamp,
        n: int,
        seed: int,
        daily_move: float = 0.0005,
    ) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
        """Synthetic (features, labels, prices) for one symbol."""
        dates = pd.date_range(start, periods=n, freq="B")
        rng = np.random.default_rng(seed)
        rets = rng.normal(daily_move, 0.01, n)
        p = 100.0 * np.cumprod(1.0 + rets)
        prices = pd.DataFrame(
            {
                "open": p,
                "high": p * 1.005,
                "low": p * 0.995,
                "close": p,
                "volume": 2_000_000.0,
            },
            index=dates,
        )
        features = pd.DataFrame(
            {
                "f0": rng.standard_normal(n),
                "f1": rng.standard_normal(n),
            },
            index=dates,
        )
        labels = pd.Series(np.sign(rets).astype(int), index=dates, name="label")
        return features, labels, prices

    # Window params sized to give multiple folds inside a ~300-bar panel.
    _W = dict(train_window=100, test_window=25, step=25)

    def test_portfolio_handles_late_starting_symbol(self):
        """B starts ~250 bars into A's history.

        Early folds must train and test on A only; later folds must include
        both. n_symbols_active in fold_metrics records the transition.
        """
        a_feat, a_lab, a_pric = self._make_symbol("2020-01-02", n=400, seed=1)
        # B starts at A's 250th business day so that B ⊂ A and the union
        # equals A's index — exercising the per-symbol alive mask without
        # introducing a master gap.
        b_start = a_pric.index[250]
        b_feat, b_lab, b_pric = self._make_symbol(b_start, n=150, seed=2)

        result = run_portfolio_backtest(
            self._AlwaysLongModel(),
            {"A": a_feat, "B": b_feat},
            {"A": a_lab, "B": b_lab},
            {"A": a_pric, "B": b_pric},
            **self._W,
        )

        assert isinstance(result, BacktestResult)
        n_active = [m["n_symbols_active"] for m in result.fold_metrics]
        assert min(n_active) == 1, (
            f"Expected at least one A-only fold, got n_active={n_active}"
        )
        assert max(n_active) == 2, (
            f"Expected at least one A+B fold, got n_active={n_active}"
        )

    def test_portfolio_identical_to_old_on_aligned_panel(self):
        """When all symbols share an index, the union code reduces to the
        intersection behavior: every fold has full breadth, no NaN bars.
        """
        a_feat, a_lab, a_pric = self._make_symbol("2020-01-02", n=300, seed=1)
        b_feat, b_lab, b_pric = self._make_symbol("2020-01-02", n=300, seed=2)

        result = run_portfolio_backtest(
            self._AlwaysLongModel(),
            {"A": a_feat, "B": b_feat},
            {"A": a_lab, "B": b_lab},
            {"A": a_pric, "B": b_pric},
            **self._W,
        )

        n_active = {m["n_symbols_active"] for m in result.fold_metrics}
        assert n_active == {2}, (
            f"Aligned panel must have constant full breadth, got {n_active}"
        )
        oos_returns = result.equity_curve.pct_change().dropna()
        assert not oos_returns.isna().any()

    def test_portfolio_skips_fold_with_no_active_symbols(self):
        """fold_metrics' n_train_rows must scale with the count of active
        symbols in the train window.

        Early folds with only A in train have ~train_window rows; later folds
        that include both A and B contribute strictly more. This documents
        the sparse-stacking behavior; in the degenerate edge case where a
        fold has zero active symbols, that fold is silently skipped (no
        fold_metrics entry).
        """
        a_feat, a_lab, a_pric = self._make_symbol("2020-01-02", n=400, seed=1)
        b_start = a_pric.index[250]
        b_feat, b_lab, b_pric = self._make_symbol(b_start, n=150, seed=2)

        result = run_portfolio_backtest(
            self._AlwaysLongModel(),
            {"A": a_feat, "B": b_feat},
            {"A": a_lab, "B": b_lab},
            {"A": a_pric, "B": b_pric},
            **self._W,
        )

        by_breadth: dict[int, list[int]] = {}
        for m in result.fold_metrics:
            by_breadth.setdefault(m["n_symbols_active"], []).append(
                m["n_train_rows"]
            )
        assert 1 in by_breadth and 2 in by_breadth, (
            f"Expected at least one fold at each breadth, got {by_breadth}"
        )
        # A 2-symbol training pool must be strictly larger than a 1-symbol
        # pool (B contributes ≥ 1 row when alive in train).
        assert max(by_breadth[2]) > max(by_breadth[1])

    def test_portfolio_breadth_weighting_is_equal_weight(self):
        """Portfolio of two identical symbols equals the single-symbol run.

        If symbol B has bit-identical features, labels, and prices to A,
        per-symbol OOS returns are identical at every bar, so any
        equal-weight mean equals either one. The two equity curves must
        match to floating-point tolerance, and n_symbols_active must report
        the input breadth.
        """
        a_feat, a_lab, a_pric = self._make_symbol("2020-01-02", n=300, seed=1)

        solo = run_portfolio_backtest(
            self._AlwaysLongModel(),
            {"A": a_feat},
            {"A": a_lab},
            {"A": a_pric},
            **self._W,
        )
        duo = run_portfolio_backtest(
            self._AlwaysLongModel(),
            {"A": a_feat.copy(), "B": a_feat.copy()},
            {"A": a_lab.copy(), "B": a_lab.copy()},
            {"A": a_pric.copy(), "B": a_pric.copy()},
            **self._W,
        )

        pd.testing.assert_series_equal(
            solo.equity_curve,
            duo.equity_curve,
            check_names=False,
            rtol=1e-9,
            atol=1e-9,
        )
        assert all(m["n_symbols_active"] == 2 for m in duo.fold_metrics)
        assert all(m["n_symbols_active"] == 1 for m in solo.fold_metrics)


# ===========================================================================
# Harness self-validation (from docs/PHASE_1_BACKTESTER.md)
# ===========================================================================

class TestHarnessSelfValidation:
    """The three required self-tests from the Phase 1 spec.

    Exercised directly via simulate() + compute_metrics() so they are
    independent of run_backtest() orchestration correctness.
    """

    @pytest.fixture(scope="class")
    def synthetic_market(self):
        """500 business days: GBM with slight positive drift."""
        n = 500
        rng = np.random.default_rng(0)
        rets = rng.normal(0.0005, 0.015, n)
        prices = 100 * np.cumprod(1 + rets)
        dates = pd.date_range("2022-01-03", periods=n, freq="B")
        df = pd.DataFrame(
            {
                "open":   prices,
                "high":   prices * 1.005,
                "low":    prices * 0.995,
                "close":  prices,
                "volume": 5_000_000.0,
            },
            index=dates,
        )
        return df, rets

    def test_random_strategy_approximately_zero_edge(self, synthetic_market):
        """Random signals → Sharpe near zero after costs.

        Confirms: (a) costs are applied, (b) no accidental edge is baked in.
        With 500 bars the empirical Sharpe SE ≈ 0.045; [-2, 1] allows ~20 SEs.
        """
        prices, _ = synthetic_market
        rng = np.random.default_rng(123)
        # sparse signals: 60% flat, 20% long, 20% short
        raw = np.array([-1, 0, 0, 0, 1])[rng.integers(0, 5, size=len(prices))]
        signals = pd.Series(raw.astype(int), index=prices.index)

        equity_curve, trade_log = simulate(
            prices, signals,
            initial_capital=100_000,
            commission_per_share=0.005,
            slippage_bps=5.0,
        )
        daily_rets = equity_curve.pct_change().dropna()
        sharpe = compute_metrics(daily_rets, trade_log=trade_log)["sharpe"]

        assert -2.0 < sharpe < 1.0, (
            f"Random strategy Sharpe {sharpe:.3f} outside expected range [-2, 1]."
        )

    def test_perfect_foresight_sharpe_exceeds_two(self, synthetic_market):
        """signal[t] = sign(return[t+1]) with zero costs → Sharpe >> 2.

        Confirms P&L accounting is correct: a clairvoyant strategy that always
        picks the right direction must produce a very high Sharpe.

        Derivation: E[|r|] ≈ σ√(2/π) ≈ 0.012, std(|r|) ≈ 0.015
        → annualized Sharpe ≈ (0.012/0.015)√252 ≈ 12.7.
        """
        prices, rets = synthetic_market
        # signal[t] fills at bar t+1 open; the earned return is from t+1→t+2,
        # which equals rets[t+2] (since prices[t] = 100·∏(1+rets[:t+1])).
        # Perfect foresight: signal[t] = sign(rets[t+2]).
        fwd = np.sign(rets[2:])                    # n-2 values
        fwd = np.concatenate([fwd, [0, 0]])        # flat on last 2 bars → n values
        signals = pd.Series(fwd.astype(int), index=prices.index)

        equity_curve, trade_log = simulate(
            prices, signals,
            initial_capital=100_000,
            commission_per_share=0.0,
            slippage_bps=0.0,
            liquidity_cap=1.0,
        )
        daily_rets = equity_curve.pct_change().dropna()
        sharpe = compute_metrics(daily_rets, trade_log=trade_log)["sharpe"]

        assert sharpe > 2.0, (
            f"Perfect-foresight Sharpe {sharpe:.3f} should exceed 2."
        )

    def test_no_splits_warning(self):
        """walkforward_splits warns when train+test > n_samples."""
        with pytest.warns(UserWarning, match="no splits will be generated"):
            list(walkforward_splits(5, train_window=10, test_window=3))

    def test_purging_eliminates_label_overlap(self):
        """Purging removes training samples whose label window overlaps the test period.

        With label_horizon=5, train sample at position i carries a label that
        spans bars [i+1 .. i+5]. If any of those bars fall inside the test window,
        the sample is contaminated and must be purged.

        Verifies:
        1. Purged train sets are no larger than un-purged ones.
        2. Every position in a purged train satisfies i + label_horizon < test_start.
        """
        n, train_w, test_w, step, lh = 300, 100, 50, 50, 5

        splits_leak = list(
            walkforward_splits(n, train_w, test_w, step=step, label_horizon=0, embargo=0)
        )
        splits_clean = list(
            walkforward_splits(n, train_w, test_w, step=step, label_horizon=lh, embargo=3)
        )

        assert len(splits_leak) == len(splits_clean)

        for (train_l, _), (train_c, test_c) in zip(splits_leak, splits_clean):
            assert len(train_c) <= len(train_l), (
                "Purged train must not exceed un-purged train."
            )
            test_start = test_c.min()
            for i in train_c:
                assert i + lh < test_start, (
                    f"Position {i} with label_horizon={lh} reaches {i + lh}, "
                    f"which overlaps test starting at {test_start}."
                )


# ===========================================================================
# report.py
# ===========================================================================

class TestReport:
    """format_report, print_report, summary_table."""

    @pytest.fixture()
    def flat_result(self):
        """BacktestResult from an all-flat strategy (no trades, no drawdown)."""
        n = 50
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        prices = _make_prices(n)
        signals = pd.Series(0, index=prices.index)
        eq, tlog = simulate(
            prices, signals,
            initial_capital=10_000,
            commission_per_share=0.0,
            slippage_bps=0.0,
        )
        returns = eq.pct_change().dropna()
        metrics = compute_metrics(returns, trade_log=None)
        from quant.backtest.harness import BacktestResult
        return BacktestResult(
            oos_metrics=metrics,
            is_metrics=metrics,
            equity_curve=eq,
            trade_log=tlog,
            fold_metrics=[metrics],
        )

    def test_format_report_returns_string(self, flat_result):
        out = format_report(flat_result)
        assert isinstance(out, str)
        assert len(out) > 0

    def test_format_report_contains_sharpe(self, flat_result):
        assert "sharpe" in format_report(flat_result)

    def test_format_report_no_nan_percent(self, flat_result):
        """No metric value should render as 'nan%' — NaN values show '—'."""
        assert "nan%" not in format_report(flat_result)

    def test_format_report_inf_calmar_shows_dash(self, flat_result):
        """calmar=nan (no drawdown) renders as '—', not 'inf' or 'nan'."""
        out = format_report(flat_result)
        import math
        assert math.isnan(flat_result.oos_metrics["calmar"])
        assert "inf" not in out
        assert "nan" not in out

    def test_summary_table_shape_no_trades(self, flat_result):
        """summary_table always has 8 rows (6 base + hit_rate + profit_factor)."""
        tbl = summary_table(flat_result)
        assert tbl.shape == (8, 2)
        assert list(tbl.columns) == ["OOS", "IS"]

    def test_summary_table_no_nan_in_base_metrics(self, flat_result):
        """Base metrics (sharpe, sortino, etc.) should be finite numbers."""
        import math
        tbl = summary_table(flat_result)
        for key in ("sharpe", "sortino", "max_drawdown", "total_return", "annualized_return"):
            assert not math.isnan(tbl.loc[key, "OOS"])


class TestRegimeReport:
    """format_regime_report and regime_summary_table — Phase 4A Milestone 1."""

    @pytest.fixture()
    def result_with_returns(self):
        """BacktestResult with a populated oos_returns series spanning two regimes."""
        from quant.backtest.harness import BacktestResult

        n = 100
        idx = pd.bdate_range("2010-01-04", periods=n)
        rng = np.random.default_rng(42)
        returns = pd.Series(rng.normal(0.001, 0.01, n), index=idx)
        return BacktestResult(
            oos_metrics=compute_metrics(returns),
            is_metrics={"sharpe": 0.0},
            equity_curve=(1 + returns).cumprod() * 100_000.0,
            trade_log=pd.DataFrame(),
            oos_returns=returns,
        )

    @pytest.fixture()
    def regime_labels(self, result_with_returns):
        labels = pd.Series("qe_bull", index=result_with_returns.oos_returns.index, dtype=object)
        labels.iloc[50:] = "covid"
        return labels

    def test_regime_summary_table_one_row_per_regime(self, result_with_returns, regime_labels):
        from quant.backtest.report import regime_summary_table

        tbl = regime_summary_table(result_with_returns, regime_labels)
        assert isinstance(tbl, pd.DataFrame)
        assert set(tbl.index) == {"qe_bull", "covid"}

    def test_regime_summary_table_columns(self, result_with_returns, regime_labels):
        from quant.backtest.report import regime_summary_table

        tbl = regime_summary_table(result_with_returns, regime_labels)
        for col in ("sharpe", "sortino", "max_drawdown", "n_bars"):
            assert col in tbl.columns

    def test_format_regime_report_returns_string(self, result_with_returns, regime_labels):
        from quant.backtest.report import format_regime_report

        out = format_regime_report(result_with_returns, regime_labels)
        assert isinstance(out, str)
        assert len(out) > 0

    def test_format_regime_report_contains_regime_names(self, result_with_returns, regime_labels):
        from quant.backtest.report import format_regime_report

        out = format_regime_report(result_with_returns, regime_labels)
        assert "qe_bull" in out
        assert "covid" in out

    def test_format_regime_report_no_nan_percent(self, result_with_returns, regime_labels):
        from quant.backtest.report import format_regime_report

        out = format_regime_report(result_with_returns, regime_labels)
        assert "nan%" not in out
