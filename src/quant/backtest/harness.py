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

import copy
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from quant.backtest.metrics import compute_metrics
from quant.backtest.simulator import simulate
from quant.backtest.walkforward import walkforward_splits


@dataclass(frozen=True)
class BacktestResult:
    """Container for a completed backtest run.

    Per-bar series (`oos_returns`, `oos_forecast_errors`) are retained for
    downstream regime-conditional metrics and Diebold-Mariano tests; aggregate
    metrics alone cannot be sliced by regime after the fact.

    `oos_forecast_errors` is populated only for paths where the model produces
    continuous return forecasts (currently `run_portfolio_backtest`). The
    single-symbol `run_backtest` path expects models that already emit
    {-1, 0, +1} signals, so forecast errors are not well-defined there and
    the field is left as an empty Series.
    """

    oos_metrics: dict[str, float]
    is_metrics: dict[str, float]
    equity_curve: pd.Series
    trade_log: pd.DataFrame
    fold_metrics: list[dict[str, float]] = field(default_factory=list)
    oos_returns: pd.Series = field(
        default_factory=lambda: pd.Series(dtype=float)
    )
    oos_forecast_errors: pd.Series = field(
        default_factory=lambda: pd.Series(dtype=float)
    )


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
    if not features.index.equals(prices.index):
        raise ValueError(
            "features and prices must have identical DatetimeIndexes — "
            "align them before calling run_backtest"
        )

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
    oos_returns_parts: list[pd.Series] = []
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
        oos_signals_arr = np.asarray(model.predict(X_test), dtype=int)  # type: ignore[attr-defined]

        test_idx = features.index[test_pos]
        oos_signals = pd.Series(oos_signals_arr, index=test_idx)
        oos_prices = prices.loc[test_idx]

        eq, tlog = simulate(oos_prices, oos_signals, **sim_kwargs)  # type: ignore[arg-type]
        fold_ret = eq.pct_change().dropna()
        fold_m = compute_metrics(fold_ret, trade_log=tlog if not tlog.empty else None)
        fold_metrics.append(fold_m)

        oos_equity_parts.append(eq)
        oos_returns_parts.append(fold_ret)  # within-fold returns, no cross-fold boundary
        if not tlog.empty:
            oos_trade_parts.append(tlog)

        # IS: re-predict on training data for IS metric comparison
        is_signals_arr = np.asarray(model.predict(X_train), dtype=int)  # type: ignore[attr-defined]
        train_idx = features.index[train_pos]
        is_signals = pd.Series(is_signals_arr, index=train_idx)
        is_prices = prices.loc[train_idx]
        is_eq, _ = simulate(is_prices, is_signals, **sim_kwargs)  # type: ignore[arg-type]
        is_returns_parts.append(is_eq.pct_change().dropna())

    # ── Aggregate OOS ─────────────────────────────────────────────────────
    # Concatenate within-fold return series, NOT the equity levels, so that
    # pct_change() is never computed across fold boundaries (each fold resets
    # to initial_capital, which would inject a phantom return at every join).
    if oos_returns_parts:
        equity_curve = pd.concat(oos_equity_parts)
        oos_returns = pd.concat(oos_returns_parts)
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
        oos_returns=oos_returns,
        # Single-symbol path: predict() returns signals, not continuous
        # forecasts, so per-bar forecast errors are not well-defined here.
        # Left as the empty default Series.
    )


def run_portfolio_backtest(
    model: object,
    features_by_symbol: dict[str, pd.DataFrame],
    labels_by_symbol: dict[str, pd.Series],
    prices_by_symbol: dict[str, pd.DataFrame],
    train_window: int = 504,
    test_window: int = 63,
    step: int = 63,
    label_horizon: int = 1,
    embargo: int = 3,
    **sim_kwargs: object,
) -> BacktestResult:
    """Run a purged walk-forward backtest across multiple symbols.

    The model is fit once per fold on pooled cross-sectional training data
    (all alive symbols stacked vertically). Signals are derived per-symbol via
    sign(forecast). P&L is aggregated by averaging per-bar returns across
    symbols (equal-weight portfolio).

    Each symbol contributes whatever history it has; the master timeline is
    the union of all symbols' feature indices, so a symbol with a late start
    no longer truncates the panel to a shared intersection. Symbols absent
    from a given fold are silently excluded from that fold's train pool and
    OOS panel. See docs/REFACTOR_PORTFOLIO_UNION_INDEX.md.

    Parameters
    ----------
    model:               Anything with .fit(X, y) and .predict(X).
                         predict() returns continuous return forecasts;
                         sign() is applied here to produce {-1, 0, +1}.
    features_by_symbol:  {symbol: feature DataFrame} keyed by ticker string.
    labels_by_symbol:    {symbol: label Series (forward return)} same keys.
    prices_by_symbol:    {symbol: OHLCV DataFrame} same keys.
    train_window/test_window/step/label_horizon/embargo: same as run_backtest.
                         Purge/embargo apply on the master calendar.
    **sim_kwargs:        Forwarded to simulate() (commission, slippage, etc.).
    """
    symbols = list(features_by_symbol.keys())
    if not symbols:
        raise ValueError("features_by_symbol must contain at least one symbol")
    if set(symbols) != set(labels_by_symbol) or set(symbols) != set(prices_by_symbol):
        raise ValueError(
            "features_by_symbol, labels_by_symbol, and prices_by_symbol must have identical keys"
        )

    # Master timeline: union of all symbols' feature indices.
    master_idx = features_by_symbol[symbols[0]].index
    for sym in symbols[1:]:
        master_idx = master_idx.union(features_by_symbol[sym].index)
    master_idx = master_idx.sort_values().unique()
    if len(master_idx) == 0:
        raise ValueError("No bars across any symbol")

    # Defensive: per-symbol indices must be a subset of master_idx by
    # construction (master is the union). Catches DataFrame-mutation bugs.
    for sym in symbols:
        if not features_by_symbol[sym].index.isin(master_idx).all():
            raise ValueError(
                f"features_by_symbol[{sym!r}].index has bars outside master_idx — "
                "feature/label/price indexes drifted apart"
            )

    # Boolean alive mask per symbol: True at master-bar positions where the
    # symbol has a feature row. O(1) lookup per (symbol, fold) downstream.
    alive: dict[str, np.ndarray] = {
        s: master_idx.isin(features_by_symbol[s].index) for s in symbols
    }

    splits = list(
        walkforward_splits(
            len(master_idx),
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

    # Per-fold accumulators. equity_curve and IS returns are intentionally
    # NOT tracked in the portfolio path: equity_curve is reconstructed from
    # the concatenated oos_returns at the end (so equity and returns align
    # bar-for-bar), and IS metrics are skipped because re-predicting on
    # overlapping rolling training windows inflates IS Sharpe nonlinearly.
    oos_returns_parts: list[pd.Series] = []
    oos_trade_parts: list[pd.DataFrame] = []
    oos_error_parts: list[pd.Series] = []
    fold_metrics: list[dict[str, float]] = []

    for train_pos, test_pos in splits:
        if len(train_pos) == 0:
            continue

        # Sparse-stacked training pool: each symbol contributes the rows it
        # has in train_pos. Symbols absent from this fold are dropped from
        # the fit (their alive mask is all False on these positions).
        X_train_parts: list[np.ndarray] = []
        y_train_parts: list[np.ndarray] = []
        for sym in symbols:
            train_mask = alive[sym][train_pos]
            if not train_mask.any():
                continue
            sym_train_idx = master_idx[train_pos][train_mask]
            X_train_parts.append(
                features_by_symbol[sym].loc[sym_train_idx].to_numpy()
            )
            y_train_parts.append(
                labels_by_symbol[sym].loc[sym_train_idx].to_numpy()
            )

        if not X_train_parts:
            # No symbol has any data in this fold; defensive — unreachable
            # under normal input since every master_idx bar is held by ≥1 symbol.
            continue

        X_train = np.vstack(X_train_parts)
        y_train = np.concatenate(y_train_parts)
        model.fit(X_train, y_train)  # type: ignore[attr-defined]

        # Per-symbol prediction over the symbol's own subset of test_pos.
        test_master_idx = master_idx[test_pos]
        fold_sym_oos_returns: list[pd.Series] = []
        fold_sym_errors: list[pd.Series] = []

        for sym in symbols:
            test_mask = alive[sym][test_pos]
            if not test_mask.any():
                continue
            sym_test_idx = test_master_idx[test_mask]

            X_test_sym = features_by_symbol[sym].loc[sym_test_idx].to_numpy()
            raw_pred = np.asarray(
                model.predict(X_test_sym),  # type: ignore[attr-defined]
                dtype=float,
            )
            signals = pd.Series(np.sign(raw_pred).astype(int), index=sym_test_idx)
            sym_prices = prices_by_symbol[sym].loc[sym_test_idx]
            eq, tlog = simulate(sym_prices, signals, **sim_kwargs)  # type: ignore[arg-type]
            fold_sym_oos_returns.append(eq.pct_change().dropna())

            # Forecast error per bar: realised forward return minus the model's
            # continuous prediction. NaN where the label is undefined at the
            # end of the symbol's history; dropped in the cross-sectional mean.
            sym_labels = labels_by_symbol[sym].loc[sym_test_idx].to_numpy(dtype=float)
            sym_errors = pd.Series(sym_labels - raw_pred, index=sym_test_idx)
            fold_sym_errors.append(sym_errors)

            if not tlog.empty:
                oos_trade_parts.append(tlog)

        if not fold_sym_oos_returns:
            continue  # no active symbols this fold

        # Sparse cross-section: each column carries its own subset index.
        # axis=1 outer-aligns; skipna=True averages over symbols active at
        # each bar. The fold-level alignment guard from the intersection
        # path is intentionally dropped — sparse indices are expected here.
        fold_ret = pd.concat(fold_sym_oos_returns, axis=1).mean(axis=1, skipna=True)

        # Per-bar forecast error is the cross-sectional mean of per-symbol
        # errors at that bar — same aggregation rule as fold_ret so the two
        # series share the same index and DM-test inputs are well-defined.
        fold_err = pd.concat(fold_sym_errors, axis=1).mean(axis=1, skipna=True)

        # Invariant: a bar with ≥1 active symbol must yield a finite mean;
        # an all-NaN fold means a symbol reported alive but simulate()
        # produced no usable returns — a real bug, not silent recovery.
        if fold_ret.isna().all():
            raise RuntimeError(
                "All per-symbol OOS returns NaN this fold — alive mask "
                "claims data but simulate() produced no usable returns"
            )

        fold_m = compute_metrics(fold_ret)
        fold_m["n_symbols_active"] = float(len(fold_sym_oos_returns))
        fold_m["n_train_rows"] = float(len(y_train))
        fold_metrics.append(fold_m)
        oos_returns_parts.append(fold_ret)

        # Align forecast errors with returns so the two series share an index.
        # eq.pct_change().dropna() removes the first bar per fold, so the same
        # bar must be removed from the error series.
        oos_error_parts.append(fold_err.loc[fold_ret.index])

    # IS metrics are not reported for the portfolio path: re-predicting on
    # overlapping rolling training windows inflates IS Sharpe nonlinearly (the
    # same bar contributes to adjacent folds' IS series) and understates the
    # true IS/OOS overfit gap. Use run_backtest() per-symbol if IS diagnostics
    # are needed for a single name.

    if not oos_returns_parts:
        raise RuntimeError(
            "No symbol contributed to any fold — the panel has no test-window "
            "coverage in the requested walk-forward configuration"
        )

    oos_returns = pd.concat(oos_returns_parts) if oos_returns_parts else pd.Series(dtype=float)
    # Build equity curve from the concatenated return series so equity_curve and
    # oos_returns are always aligned bar-for-bar (avoids the per-fold off-by-one
    # that arises from constructing equity from already-shortened return series).
    equity_curve = (1 + oos_returns).cumprod() * 100_000.0 if not oos_returns.empty else pd.Series(dtype=float)
    trade_log = (
        pd.concat(oos_trade_parts, ignore_index=True) if oos_trade_parts else _empty_log.copy()
    )
    oos_metrics = compute_metrics(
        oos_returns,
        trade_log=trade_log if len(trade_log) > 0 else None,
    )

    oos_forecast_errors = (
        pd.concat(oos_error_parts) if oos_error_parts else pd.Series(dtype=float)
    )

    return BacktestResult(
        oos_metrics=oos_metrics,
        is_metrics=_empty_metrics,
        equity_curve=equity_curve,
        trade_log=trade_log,
        fold_metrics=fold_metrics,
        oos_returns=oos_returns,
        oos_forecast_errors=oos_forecast_errors,
    )


def evaluate_panel(
    models: dict[str, Any],
    features: pd.DataFrame,
    labels: pd.Series,
    prices: pd.DataFrame,
    train_window: int = 504,
    test_window: int = 63,
    step: int = 63,
    label_horizon: int = 1,
    embargo: int = 3,
    **sim_kwargs: object,
) -> dict[str, BacktestResult]:
    """Run multiple models through identical walk-forward backtest parameters.

    Guarantees all models in the comparison panel see the same train_window,
    test_window, step, label_horizon, embargo, and sim_kwargs — preventing
    accidental parameter drift between model evaluations.

    Parameters
    ----------
    models:  {name: model} mapping. Each model must have .fit(X, y) and .predict(X).
             predict() must return {-1, 0, +1} signals aligned with X's index.
    """
    return {
        name: run_backtest(
            model=copy.deepcopy(mdl),
            features=features,
            labels=labels,
            prices=prices,
            train_window=train_window,
            test_window=test_window,
            step=step,
            label_horizon=label_horizon,
            embargo=embargo,
            **sim_kwargs,
        )
        for name, mdl in models.items()
    }
