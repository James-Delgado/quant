"""Out-of-sample prediction collection + per-regime scoring for B1 target reframing.

Phase 4A's harness is **return → simulator → Sharpe** centric: ``GBMModel`` is an
``XGBRegressor`` and ``run_portfolio_backtest`` only ever emits ``oos_returns``
and ``oos_forecast_errors``. Project B1 evaluates *non-return* prediction objects
(drawdown probability, log realized vol, longer-horizon direction) whose primary
metrics are **ROC-AUC** (T1/T3/T4) and **MAE** (T2). Those need raw OOS
``(y_true, y_pred)`` pairs, which the existing path never exposes.

This module adds exactly that, reusing the *same* purged walk-forward machinery so
the leakage controls are identical to the rest of the system:

* ``collect_oos_predictions`` — purged walk-forward prediction collector. It is a
  prediction-recording sibling of ``run_portfolio_backtest``: the same
  ``walkforward_splits`` generator, the same pooled cross-sectional fit (all alive
  symbols stacked vertically) and per-symbol predict, but it records the raw
  ``(date, symbol, y_true, y_pred)`` rows instead of routing ``sign(pred)`` through
  the simulator. Purge/embargo (``backtest/CLAUDE.md`` invariants 1-4) are
  unchanged — ``label_horizon`` is the purge boundary exactly as in the harness.
* ``simulate_signal_returns`` — the *directional Sharpe arm*: maps a collected
  prediction frame to ``sign(y_pred - threshold)`` signals and routes them through
  the existing ``simulate`` per symbol, averaging across symbols per bar (the same
  equal-weight cross-section as ``run_portfolio_backtest``). A classifier trained
  on a 0/1 label predicts a probability centred at ~0.5, so the trade threshold is
  0.5 (long if P(up) > 0.5), **not** the harness's ``sign(pred)`` at 0 — that would
  go long on every bar.
* ``per_regime_metric`` — group the OOS predictions by regime (via a per-date
  regime-label Series) and score an arbitrary ``metric_fn(y_true, y_pred)`` per
  regime, the per-regime input ``b1_gate_report`` consumes.

This module has no side effects and never touches the split logic itself; it calls
``walkforward_splits``/``simulate`` exactly as the harness does.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from quant.backtest.simulator import simulate
from quant.backtest.walkforward import walkforward_splits

PRED_COLUMNS = ("symbol", "y_true", "y_pred")


def collect_oos_predictions(
    model: object,
    features_by_symbol: dict[str, pd.DataFrame],
    labels_by_symbol: dict[str, pd.Series],
    *,
    train_window: int = 504,
    test_window: int = 63,
    step: int = 63,
    label_horizon: int = 1,
    embargo: int = 3,
) -> pd.DataFrame:
    """Collect raw OOS ``(y_true, y_pred)`` over a purged walk-forward, across symbols.

    The model is fit once per fold on the pooled cross-sectional training data (all
    alive symbols stacked vertically), then predicts per symbol on that symbol's
    slice of the test window — identical to ``run_portfolio_backtest`` — but the raw
    predictions are recorded rather than simulated. The master timeline is the union
    of every symbol's feature index; purge/embargo apply on that master calendar
    with ``label_horizon`` as the purge boundary (``backtest/CLAUDE.md`` inv. 1-2).

    Parameters
    ----------
    model:
        Anything with ``.fit(X, y)`` and ``.predict(X)``. Used as supplied — the
        caller is responsible for passing a fresh/deep-copied model if reusing one
        across targets (mirrors ``run_label_ablation`` discipline).
    features_by_symbol, labels_by_symbol:
        Per-symbol panel inputs with identical keys. Each symbol's feature frame and
        label series must share an index and be NaN-free over that index (build them
        with the single-``dropna`` + intersection discipline the notebooks use); the
        collector does not silently impute.
    train_window, test_window, step, label_horizon, embargo:
        Walk-forward parameters, same semantics as ``run_portfolio_backtest``.

    Returns
    -------
    A DataFrame indexed by date (``DatetimeIndex``, repeated across symbols on a
    shared bar) with columns ``("symbol", "y_true", "y_pred")``, sorted by
    ``(date, symbol)``. Empty (with the right columns/dtype) when no fold produced a
    usable OOS prediction.

    Raises
    ------
    ValueError if the three dicts have mismatched keys, if a symbol's feature/label
    indexes differ, or if any panel is empty.
    """
    symbols = list(features_by_symbol.keys())
    if not symbols:
        raise ValueError("features_by_symbol must contain at least one symbol")
    if set(symbols) != set(labels_by_symbol):
        raise ValueError(
            "features_by_symbol and labels_by_symbol must have identical keys"
        )
    for sym in symbols:
        if not features_by_symbol[sym].index.equals(labels_by_symbol[sym].index):
            raise ValueError(
                f"features_by_symbol[{sym!r}] and labels_by_symbol[{sym!r}] must "
                "share an identical index — align (dropna + intersection) first"
            )

    master_idx = features_by_symbol[symbols[0]].index
    for sym in symbols[1:]:
        master_idx = master_idx.union(features_by_symbol[sym].index)
    master_idx = master_idx.sort_values().unique()
    if len(master_idx) == 0:
        raise ValueError("No bars across any symbol")

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

    rows: list[pd.DataFrame] = []
    for train_pos, test_pos in splits:
        if len(train_pos) == 0:
            continue

        X_train_parts: list[np.ndarray] = []
        y_train_parts: list[np.ndarray] = []
        for sym in symbols:
            train_mask = alive[sym][train_pos]
            if not train_mask.any():
                continue
            sym_train_idx = master_idx[train_pos][train_mask]
            X_train_parts.append(features_by_symbol[sym].loc[sym_train_idx].to_numpy())
            y_train_parts.append(labels_by_symbol[sym].loc[sym_train_idx].to_numpy())

        if not X_train_parts:
            continue

        X_train = np.vstack(X_train_parts)
        y_train = np.concatenate(y_train_parts)
        model.fit(X_train, y_train)  # type: ignore[attr-defined]

        test_master_idx = master_idx[test_pos]
        for sym in symbols:
            test_mask = alive[sym][test_pos]
            if not test_mask.any():
                continue
            sym_test_idx = test_master_idx[test_mask]
            X_test_sym = features_by_symbol[sym].loc[sym_test_idx].to_numpy()
            y_pred = np.asarray(model.predict(X_test_sym), dtype=float)  # type: ignore[attr-defined]
            y_true = labels_by_symbol[sym].loc[sym_test_idx].to_numpy(dtype=float)
            rows.append(
                pd.DataFrame(
                    {"symbol": sym, "y_true": y_true, "y_pred": y_pred},
                    index=sym_test_idx,
                )
            )

    if not rows:
        empty = pd.DataFrame(
            {"symbol": pd.Series(dtype=object),
             "y_true": pd.Series(dtype=float),
             "y_pred": pd.Series(dtype=float)}
        )
        empty.index = pd.DatetimeIndex([], name=None)
        return empty

    frame = pd.concat(rows)
    frame = frame.sort_values("symbol", kind="stable").sort_index(kind="stable")
    return frame[list(PRED_COLUMNS)]


def simulate_signal_returns(
    predictions: pd.DataFrame,
    prices_by_symbol: dict[str, pd.DataFrame],
    *,
    threshold: float = 0.5,
    **sim_kwargs: object,
) -> pd.Series:
    """Map a directional prediction frame to OOS portfolio returns via the simulator.

    The Sharpe arm for the directional targets (T3/T4). For each symbol the signal
    is ``sign(y_pred - threshold)`` in ``{-1, 0, +1}`` (long when the predicted
    probability of up exceeds ``threshold``), routed through the existing
    ``simulate`` on that symbol's OOS prices. Per-symbol within-fold returns are
    averaged across symbols per bar (equal-weight cross-section — the same
    aggregation as ``run_portfolio_backtest``), yielding one OOS return series whose
    Sharpe and regime slices are commensurable with the Phase 4A harness.

    Parameters
    ----------
    predictions:
        A frame as returned by ``collect_oos_predictions`` (date index, ``symbol``
        and ``y_pred`` columns).
    prices_by_symbol:
        ``{symbol: OHLCV DataFrame}`` covering every (symbol, date) in
        ``predictions``.
    threshold:
        Decision boundary for ``sign(y_pred - threshold)``. Defaults to 0.5 (the
        natural boundary for a 0/1-label probability); pass 0.0 to recover the
        harness's ``sign(pred)`` convention for a return forecast.
    **sim_kwargs:
        Forwarded verbatim to ``simulate`` (commission, slippage, etc.).

    Returns
    -------
    A single OOS return ``pd.Series`` indexed by date (cross-sectional mean across
    active symbols per bar), NaN-free. Empty when ``predictions`` is empty.
    """
    if predictions.empty:
        return pd.Series(dtype=float)

    per_symbol_returns: list[pd.Series] = []
    for sym, sym_rows in predictions.groupby("symbol", sort=True):
        sym_rows = sym_rows.sort_index()
        signals = pd.Series(
            np.sign(sym_rows["y_pred"].to_numpy(dtype=float) - threshold).astype(int),
            index=sym_rows.index,
        )
        sym_prices = prices_by_symbol[sym].loc[sym_rows.index]
        eq, _ = simulate(sym_prices, signals, **sim_kwargs)  # type: ignore[arg-type]
        per_symbol_returns.append(eq.pct_change().dropna())

    if not per_symbol_returns:
        return pd.Series(dtype=float)

    oos_returns = pd.concat(per_symbol_returns, axis=1).mean(axis=1, skipna=True)
    return oos_returns.dropna().sort_index()


def per_regime_metric(
    predictions: pd.DataFrame,
    regime_labels: pd.Series,
    metric_fn: Callable[[np.ndarray, np.ndarray], float],
    *,
    regimes: tuple[str, ...] | None = None,
) -> dict[str, float]:
    """Score ``metric_fn(y_true, y_pred)`` per regime over the OOS prediction frame.

    Each prediction row's date is mapped to a regime via ``regime_labels`` (a
    per-date Series, typically ``tag_regimes(predictions.index.unique(), detector)``);
    rows in a regime are pooled across symbols and dates, then ``metric_fn`` scores
    that pool. A regime with no rows is omitted; a regime whose ``metric_fn`` raises
    ``ValueError`` (e.g. ROC-AUC on a single-class pool) is recorded as ``nan`` so a
    thin regime degrades to "no evidence" rather than crashing the whole report.

    Parameters
    ----------
    predictions:
        Frame from ``collect_oos_predictions``.
    regime_labels:
        Per-date regime Series. Reindexed onto ``predictions.index``; dates with no
        label are dropped (they contribute to no regime).
    metric_fn:
        ``(y_true, y_pred) -> float``. AUC, MAE, etc.
    regimes:
        If given, restrict the output to these regimes (still omitting empty ones).
        ``None`` scores every regime present.

    Returns
    -------
    ``{regime: metric_value}``.
    """
    if predictions.empty:
        return {}

    row_regime = regime_labels.reindex(predictions.index)
    mask = row_regime.notna().to_numpy()
    y_true = predictions["y_true"].to_numpy(dtype=float)[mask]
    y_pred = predictions["y_pred"].to_numpy(dtype=float)[mask]
    rr = row_regime.to_numpy()[mask]

    present = list(dict.fromkeys(rr.tolist()))
    targets = [r for r in (regimes or present) if r in present]

    out: dict[str, float] = {}
    for regime in targets:
        sel = rr == regime
        if not sel.any():
            continue
        try:
            out[regime] = float(metric_fn(y_true[sel], y_pred[sel]))
        except ValueError:
            out[regime] = float("nan")
    return out
