"""Walk-forward backtest harness.

Orchestrates the full evaluation pipeline:
  1. Generate purged walk-forward splits.
  2. For each split: fit model on train, predict on test (OOS) and train (IS).
  3. Simulate each OOS prediction window with the trade simulator.
  4. Concatenate OOS equity + trade logs into one continuous series.
  5. Compute OOS and IS aggregate metrics.

The model is duck-typed: any object with .fit(X, y) and .predict(X) works.
predict() must return an array-like of {-1, 0, +1} signals aligned with X's index.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quant.backtest.metrics import compute_metrics
from quant.backtest.simulator import simulate
from quant.backtest.walkforward import walkforward_splits


@dataclass
class BacktestResult:
    """Container for a completed backtest run."""

    oos_metrics: dict[str, float]
    is_metrics: dict[str, float]
    equity_curve: pd.Series
    trade_log: pd.DataFrame
    fold_metrics: list[dict[str, float]] = field(default_factory=list)


def run_backtest(
    model: object,
    features: pd.DataFrame,
    labels: pd.Series,
    prices: pd.DataFrame,
    train_window: int = 504,
    test_window: int = 63,
    step: int = 63,
    label_horizon: int = 1,
    embargo: int = 3,
    **sim_kwargs: object,
) -> BacktestResult:
    """Run a purged walk-forward backtest.

    Parameters
    ----------
    model:          Anything with .fit(X, y) and .predict(X).
                    predict() must return {-1, 0, +1} signals.
    features:       Feature DataFrame, DatetimeIndex aligned with prices.
    labels:         Target Series (e.g. sign of forward return), same index.
    prices:         OHLCV DataFrame — columns: open, high, low, close, volume.
    train_window:   Rolling train set size (bars, before purge/embargo).
    test_window:    OOS test set size per fold (bars).
    step:           Bars to advance the window each fold.
    label_horizon:  Forward look of each label (bars); purge boundary.
    embargo:        Additional buffer after purging (bars).
    **sim_kwargs:   Forwarded to simulate() (commission, slippage, etc.).

    Returns
    -------
    BacktestResult with oos_metrics, is_metrics, equity_curve, trade_log.
    """
    n = len(features)
    splits = list(
        walkforward_splits(
            n,
            train_window=train_window,
            test_window=test_window,
            step=step,
            label_horizon=label_horizon,
            embargo=embargo,
        )
    )

    _empty_metrics: dict[str, float] = {
        "sharpe": 0.0, "sortino": 0.0, "calmar": 0.0,
        "max_drawdown": 0.0, "total_return": 0.0, "annualized_return": 0.0,
    }
    _empty_log = pd.DataFrame(
        columns=["date", "entry_price", "exit_price", "shares",
                 "gross_pnl", "commission", "net_pnl"]
    )

    if not splits:
        return BacktestResult(
            oos_metrics=_empty_metrics,
            is_metrics=_empty_metrics,
            equity_curve=pd.Series(dtype=float),
            trade_log=_empty_log,
        )

    feat_arr = features.to_numpy()
    label_arr = labels.to_numpy()

    oos_equity_parts: list[pd.Series] = []
    oos_trade_parts: list[pd.DataFrame] = []
    is_returns_parts: list[pd.Series] = []
    fold_metrics: list[dict[str, float]] = []

    for train_pos, test_pos in splits:
        if len(train_pos) == 0:
            continue

        X_train = feat_arr[train_pos]
        y_train = label_arr[train_pos]
        X_test = feat_arr[test_pos]

        model.fit(X_train, y_train)  # type: ignore[attr-defined]
        oos_signals_arr = np.asarray(model.predict(X_test))  # type: ignore[attr-defined]

        test_idx = features.index[test_pos]
        oos_signals = pd.Series(oos_signals_arr.astype(int), index=test_idx)
        oos_prices = prices.loc[test_idx]

        eq, tlog = simulate(oos_prices, oos_signals, **sim_kwargs)  # type: ignore[arg-type]
        daily_ret = eq.pct_change().dropna()
        fold_m = compute_metrics(daily_ret, trade_log=tlog if len(tlog) > 0 else None)
        fold_metrics.append(fold_m)

        oos_equity_parts.append(eq)
        if len(tlog) > 0:
            oos_trade_parts.append(tlog)

        # IS: re-predict on training data for IS metric comparison
        is_signals_arr = np.asarray(model.predict(X_train))  # type: ignore[attr-defined]
        train_idx = features.index[train_pos]
        is_signals = pd.Series(is_signals_arr.astype(int), index=train_idx)
        is_prices = prices.loc[train_idx]
        is_eq, _ = simulate(is_prices, is_signals, **sim_kwargs)  # type: ignore[arg-type]
        is_returns_parts.append(is_eq.pct_change().dropna())

    # ── Aggregate OOS ─────────────────────────────────────────────────────
    if oos_equity_parts:
        equity_curve = pd.concat(oos_equity_parts)
        oos_returns = equity_curve.pct_change().dropna()
    else:
        equity_curve = pd.Series(dtype=float)
        oos_returns = pd.Series(dtype=float)

    trade_log = (
        pd.concat(oos_trade_parts, ignore_index=True)
        if oos_trade_parts
        else _empty_log.copy()
    )

    oos_metrics = compute_metrics(
        oos_returns,
        trade_log=trade_log if len(trade_log) > 0 else None,
    )

    # ── Aggregate IS ──────────────────────────────────────────────────────
    is_returns = (
        pd.concat(is_returns_parts)
        if is_returns_parts
        else pd.Series(dtype=float)
    )
    is_metrics = compute_metrics(is_returns)

    return BacktestResult(
        oos_metrics=oos_metrics,
        is_metrics=is_metrics,
        equity_curve=equity_curve,
        trade_log=trade_log,
        fold_metrics=fold_metrics,
    )
