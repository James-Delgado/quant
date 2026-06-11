"""Label-scheme ablation orchestrator (Phase 4A Milestone 2).

Iterates over label schemes (signed return / vol-scaled / triple-barrier)
and runs each through ``run_portfolio_backtest`` with identical
hyperparameters. The result is a ``dict[scheme_name, BacktestResult]``
ready for per-regime ranking via the reporter functions in ``report.py``.

Discipline matches ``evaluate_panel``:
  * One ``copy.deepcopy(model)`` per scheme so model state cannot leak
    across runs.
  * train_window / test_window / step / embargo and ``sim_kwargs`` are
    identical across schemes — only the label scheme varies.
  * ``label_horizon`` is *derived* from each scheme's ``LabelResult``
    rather than caller-supplied, so the purge logic stays coupled to the
    label definition (see ``backtest/CLAUDE.md`` invariant 1).
"""
from __future__ import annotations

import copy
from typing import Callable

import pandas as pd

from quant.backtest.harness import BacktestResult, run_portfolio_backtest
from quant.features.labels import LabelResult


LabelSchemeFn = Callable[[pd.Series], LabelResult]
"""Signature for a label scheme: takes a close-price series, returns a LabelResult."""


def run_label_ablation(
    label_schemes: dict[str, LabelSchemeFn],
    model: object,
    features_by_symbol: dict[str, pd.DataFrame],
    prices_by_symbol: dict[str, pd.DataFrame],
    train_window: int = 504,
    test_window: int = 63,
    step: int = 63,
    embargo: int = 3,
    **sim_kwargs: object,
) -> dict[str, BacktestResult]:
    """Run a single model under multiple label schemes, returning one result per scheme.

    Each scheme is a callable ``LabelSchemeFn`` that takes a symbol's close
    price series and returns a ``LabelResult``. The orchestrator rebuilds
    ``labels_by_symbol`` per scheme and forwards every other kwarg verbatim
    to ``run_portfolio_backtest``.

    Parameters
    ----------
    label_schemes:
        ``{scheme_name: scheme_callable}`` mapping. Must be non-empty.
        Names appear as keys in the returned dict.
    model:
        Anything with ``.fit(X, y)`` and ``.predict(X)``. Deep-copied per
        scheme so internal state cannot leak across runs.
    features_by_symbol, prices_by_symbol:
        Per-symbol panel inputs forwarded to ``run_portfolio_backtest``.
    train_window, test_window, step, embargo, **sim_kwargs:
        Walk-forward + simulator parameters. Held constant across schemes
        (the kwargs-discipline that makes this a valid ablation).

    Returns
    -------
    ``{scheme_name: BacktestResult}`` populated for every scheme.
    """
    if not label_schemes:
        raise ValueError("run_label_ablation needs at least one label scheme")

    results: dict[str, BacktestResult] = {}
    for name, scheme in label_schemes.items():
        labels_by_symbol: dict[str, pd.Series] = {}
        horizon_bars: int | None = None
        for sym, prices_df in prices_by_symbol.items():
            close = prices_df["close"]
            label = scheme(close)
            labels_by_symbol[sym] = label.series
            if horizon_bars is None:
                horizon_bars = label.horizon_bars
            elif horizon_bars != label.horizon_bars:
                raise ValueError(
                    f"scheme {name!r} produced inconsistent horizon_bars "
                    f"across symbols ({horizon_bars} vs {label.horizon_bars}); "
                    "every symbol must use the same horizon for purge to stay correct"
                )

        assert horizon_bars is not None  # non-empty prices_by_symbol enforced by harness

        results[name] = run_portfolio_backtest(
            model=copy.deepcopy(model),
            features_by_symbol=features_by_symbol,
            labels_by_symbol=labels_by_symbol,
            prices_by_symbol=prices_by_symbol,
            train_window=train_window,
            test_window=test_window,
            step=step,
            label_horizon=horizon_bars,
            embargo=embargo,
            **sim_kwargs,
        )

    return results
