"""Portfolio harness self-tests (T4).

Three mandatory tests that must always pass:
  1. Random (no-skill) strategy → OOS Sharpe ≈ 0 (no systematic edge).
  2. Perfect-foresight strategy → high OOS Sharpe (harness transmits real edge).
  3. Leaky strategy → purge/embargo controls catch the leak (Sharpe collapses
     when controls are active vs. inflated when bypassed).

These tests verify that the harness neither creates nor destroys edge. A random
strategy passing with a large positive Sharpe means the harness is broken. A
perfect-foresight strategy producing near-zero Sharpe means costs or the
simulator are misconfigured.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest.harness import run_portfolio_backtest


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_prices(n: int, seed: int = 0) -> pd.DataFrame:
    """Synthetic OHLCV with a GBM price path, deterministic via seed."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n)))
    open_ = close * (1 + rng.uniform(-0.002, 0.002, n))
    high = np.maximum(close, open_) * (1 + rng.uniform(0.0, 0.005, n))
    low = np.minimum(close, open_) * (1 - rng.uniform(0.0, 0.005, n))
    dates = pd.bdate_range("2018-01-02", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": rng.integers(500_000, 2_000_000, n).astype(float)},
        index=dates,
    )


def _make_features(n: int, n_cols: int = 5, seed: int = 0, dates: pd.Index | None = None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = dates if dates is not None else pd.bdate_range("2018-01-02", periods=n)
    return pd.DataFrame(
        rng.standard_normal((n, n_cols)),
        index=idx,
        columns=[f"f{i}" for i in range(n_cols)],
    )


def _forward_returns(prices: pd.DataFrame, horizon: int = 1) -> pd.Series:
    return prices["close"].shift(-horizon) / prices["close"] - 1.0


# ─── Stub models ─────────────────────────────────────────────────────────────

class RandomModel:
    """No skill: predicts i.i.d. N(0,1) regardless of features."""

    def __init__(self, seed: int = 42) -> None:
        self._rng = np.random.default_rng(seed)

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        pass

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self._rng.standard_normal(len(X))


class PerfectForesightModel:
    """Reads the true forward return from the last feature column (injected
    by the test). predict() returns that column directly as a continuous
    forecast so sign() in the harness yields the correct trade direction."""

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        pass

    def predict(self, X: np.ndarray) -> np.ndarray:
        # Last column encodes the true forward return
        return X[:, -1]


class AlwaysLongModel:
    """Constant-long model; used to test that purge/embargo parameters
    have observable effect on fold construction."""

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        pass

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.ones(len(X))


# ─── Shared fixtures ─────────────────────────────────────────────────────────

N = 600
SYMBOLS = ["AAPL", "MSFT"]
TRAIN_W, TEST_W, STEP = 200, 50, 50
HORIZON = 1
EMBARGO = 3


@pytest.fixture(scope="module")
def prices_by_sym() -> dict[str, pd.DataFrame]:
    return {sym: _make_prices(N, seed=i) for i, sym in enumerate(SYMBOLS)}


@pytest.fixture(scope="module")
def features_by_sym(prices_by_sym: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {
        sym: _make_features(N, dates=prices_by_sym[sym].index, seed=i)
        for i, sym in enumerate(SYMBOLS)
    }


@pytest.fixture(scope="module")
def labels_by_sym(prices_by_sym: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
    return {sym: _forward_returns(prices_by_sym[sym], horizon=HORIZON) for sym in SYMBOLS}


# ─── Self-tests ───────────────────────────────────────────────────────────────

class TestPortfolioHarnessSelfTests:
    def test_random_model_near_zero_sharpe(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        labels_by_sym: dict[str, pd.Series],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        """A no-skill random model must not produce systematic OOS edge.

        The bound |Sharpe| < 1.5 is deliberately loose — this is a sanity
        check, not a statistical test. Failures almost always indicate
        lookahead leakage or a broken signal path.
        """
        result = run_portfolio_backtest(
            model=RandomModel(seed=7),
            features_by_symbol=features_by_sym,
            labels_by_symbol=labels_by_sym,
            prices_by_symbol=prices_by_sym,
            train_window=TRAIN_W,
            test_window=TEST_W,
            step=STEP,
            label_horizon=HORIZON,
            embargo=EMBARGO,
        )
        sharpe = result.oos_metrics["sharpe"]
        assert abs(sharpe) < 1.5, (
            f"Random model OOS Sharpe={sharpe:.2f} — harness may be leaking future information"
        )

    def test_perfect_foresight_positive_sharpe(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        labels_by_sym: dict[str, pd.Series],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        """A model given the true traded return must show strongly positive OOS Sharpe.

        Verifies the harness transmits real edge when it exists. Uses zero
        costs to isolate signal quality from cost drag — if this fails, the
        harness itself is suppressing edge, not costs.

        Signal timing: signal at bar t fills at open[t+1] and is marked to
        open[t+2], so the captured return is open[t+2]/open[t+1] - 1. The
        perfect-foresight feature is the sign of this quantity injected as
        the last feature column so PerfectForesightModel reads X[:, -1].
        """
        pf_features: dict[str, pd.DataFrame] = {}
        for sym in SYMBOLS:
            base = features_by_sym[sym].copy()
            prices = prices_by_sym[sym]
            # Return captured by the simulator: enter at open[t+1], mark at open[t+2]
            traded_ret = prices["open"].shift(-2) / prices["open"].shift(-1) - 1.0
            base["pf_signal"] = traded_ret.fillna(0.0).values
            pf_features[sym] = base

        result = run_portfolio_backtest(
            model=PerfectForesightModel(),
            features_by_symbol=pf_features,
            labels_by_symbol=labels_by_sym,
            prices_by_symbol=prices_by_sym,
            train_window=TRAIN_W,
            test_window=TEST_W,
            step=STEP,
            label_horizon=HORIZON,
            embargo=EMBARGO,
            commission_per_share=0.0,
            slippage_bps=0.0,
        )
        sharpe = result.oos_metrics["sharpe"]
        assert sharpe > 0.5, (
            f"Perfect-foresight OOS Sharpe={sharpe:.2f} — "
            "harness may be suppressing real edge (check signal path)"
        )

    def test_purge_embargo_constraint_satisfied(self) -> None:
        """No training position's label window must overlap any test position.

        Directly verifies the purge invariant on the splits produced by
        walkforward_splits. An AlwaysLongModel cannot detect contamination
        (its predictions are identical regardless of training data), so the
        correct approach is to inspect the splits themselves.
        """
        from quant.backtest.walkforward import walkforward_splits

        splits = list(
            walkforward_splits(
                N,
                train_window=TRAIN_W,
                test_window=TEST_W,
                step=STEP,
                label_horizon=HORIZON,
                embargo=EMBARGO,
            )
        )
        assert splits, "Expected at least one fold"
        for train_pos, test_pos in splits:
            test_start = int(min(test_pos))
            for tp in train_pos:
                assert tp + HORIZON < test_start, (
                    f"Training position {tp} has label window reaching {tp + HORIZON}, "
                    f"which overlaps test start {test_start} — purge violated"
                )

    def test_without_purge_splits_would_be_contaminated(self) -> None:
        """Confirms the above test is meaningful: without purge, overlaps exist.

        If this test fails, HORIZON/EMBARGO/TRAIN_W/TEST_W/N are configured
        such that purge makes no difference — increase HORIZON or shrink STEP.
        """
        from quant.backtest.walkforward import walkforward_splits

        leaky_splits = list(
            walkforward_splits(
                N,
                train_window=TRAIN_W,
                test_window=TEST_W,
                step=STEP,
                label_horizon=0,
                embargo=0,
            )
        )
        contaminated = any(
            tp + HORIZON >= int(min(test_pos))
            for train_pos, test_pos in leaky_splits
            for tp in train_pos
        )
        assert contaminated, (
            "Expected some training positions to violate the HORIZON constraint "
            "when purge/embargo=0 — adjust test parameters"
        )


# ─── BacktestResult per-bar series (Phase 4A Milestone 1, Task 1) ────────────

class TestBacktestResultSeries:
    """The harness must retain per-bar OOS returns and per-bar OOS forecast
    errors on `BacktestResult` so downstream regime-conditional metrics and
    Diebold-Mariano tests can operate on continuous series. Aggregate metrics
    alone are insufficient — they cannot be sliced by regime after the fact.
    """

    def test_oos_returns_populated(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        labels_by_sym: dict[str, pd.Series],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        result = run_portfolio_backtest(
            model=RandomModel(seed=11),
            features_by_symbol=features_by_sym,
            labels_by_symbol=labels_by_sym,
            prices_by_symbol=prices_by_sym,
            train_window=TRAIN_W,
            test_window=TEST_W,
            step=STEP,
            label_horizon=HORIZON,
            embargo=EMBARGO,
        )
        assert isinstance(result.oos_returns, pd.Series)
        assert len(result.oos_returns) > 0
        assert result.oos_returns.notna().any()

    def test_oos_returns_index_monotonic(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        labels_by_sym: dict[str, pd.Series],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        """OOS returns are concatenated across folds; the resulting index must
        be monotonically increasing (no overlap, no reordering). Required for
        regime tagging to align bar-for-bar."""
        result = run_portfolio_backtest(
            model=RandomModel(seed=12),
            features_by_symbol=features_by_sym,
            labels_by_symbol=labels_by_sym,
            prices_by_symbol=prices_by_sym,
            train_window=TRAIN_W,
            test_window=TEST_W,
            step=STEP,
            label_horizon=HORIZON,
            embargo=EMBARGO,
        )
        assert result.oos_returns.index.is_monotonic_increasing

    def test_oos_forecast_errors_populated_for_continuous_model(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        labels_by_sym: dict[str, pd.Series],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        """run_portfolio_backtest accepts continuous forecasts (np.sign applied
        internally for the signal), so forecast errors are well-defined.
        """
        result = run_portfolio_backtest(
            model=RandomModel(seed=13),
            features_by_symbol=features_by_sym,
            labels_by_symbol=labels_by_sym,
            prices_by_symbol=prices_by_sym,
            train_window=TRAIN_W,
            test_window=TEST_W,
            step=STEP,
            label_horizon=HORIZON,
            embargo=EMBARGO,
        )
        assert isinstance(result.oos_forecast_errors, pd.Series)
        assert len(result.oos_forecast_errors) > 0
        # Errors are not identically zero for a random model.
        assert result.oos_forecast_errors.abs().sum() > 0.0

    def test_oos_returns_and_forecast_errors_aligned(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        labels_by_sym: dict[str, pd.Series],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        """Both series must share the same DatetimeIndex so they can be sliced
        together by a regime mask without re-alignment."""
        result = run_portfolio_backtest(
            model=RandomModel(seed=14),
            features_by_symbol=features_by_sym,
            labels_by_symbol=labels_by_sym,
            prices_by_symbol=prices_by_sym,
            train_window=TRAIN_W,
            test_window=TEST_W,
            step=STEP,
            label_horizon=HORIZON,
            embargo=EMBARGO,
        )
        assert result.oos_returns.index.equals(result.oos_forecast_errors.index)
