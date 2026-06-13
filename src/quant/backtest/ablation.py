"""Label-scheme and feature-set ablation orchestrators (Phase 4A M2/M3).

Milestone 2: ``run_label_ablation`` iterates over label schemes (signed
return / vol-scaled / triple-barrier) and runs each through
``run_portfolio_backtest`` with identical hyperparameters.

Milestone 3: ``run_feature_ablation`` iterates over *feature column sets*
(e.g. baseline-17 vs baseline+1 candidate) with labels held fixed, so each
candidate feature's marginal contribution can be measured per regime.

Both return ``dict[name, BacktestResult]`` ready for per-regime ranking /
gating via the reporter functions in ``report.py``.

Discipline matches ``evaluate_panel``:
  * One ``copy.deepcopy(model)`` per run so model state cannot leak
    across runs.
  * train_window / test_window / step / embargo and ``sim_kwargs`` are
    identical across runs — only the ablated dimension varies.
  * For label ablation, ``label_horizon`` is *derived* from each scheme's
    ``LabelResult`` rather than caller-supplied, so the purge logic stays
    coupled to the label definition (see ``backtest/CLAUDE.md``
    invariant 1). For feature ablation the labels are fixed and
    caller-supplied, so ``label_horizon`` is too.
"""
from __future__ import annotations

import copy
from typing import Callable, Sequence

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


# ─── Feature-set ablation (Phase 4A Milestone 3) ─────────────────────────────


def make_add_one_sets(
    baseline_cols: Sequence[str],
    candidates: Sequence[str],
) -> dict[str, list[str]]:
    """Build add-one feature sets: ``baseline`` plus ``+c`` per candidate.

    Returns exactly ``len(candidates) + 1`` sets. Each ``+c`` set is the
    baseline columns followed by the single candidate ``c``.

    Raises ``ValueError`` on duplicate candidates (the dict keys would
    silently collide) or candidates already present in the baseline (the
    set would not actually add anything).
    """
    base = list(baseline_cols)
    cands = list(candidates)
    if len(set(cands)) != len(cands):
        raise ValueError(f"duplicate candidate columns: {cands}")
    already = [c for c in cands if c in base]
    if already:
        raise ValueError(f"candidates already in baseline_cols: {already}")
    sets: dict[str, list[str]] = {"baseline": list(base)}
    for c in cands:
        sets[f"+{c}"] = [*base, c]
    return sets


def make_leave_one_out_sets(cols: Sequence[str]) -> dict[str, list[str]]:
    """Build leave-one-out feature sets: ``all`` plus ``-c`` per column.

    Returns exactly ``len(cols) + 1`` sets. Raises ``ValueError`` on
    duplicate columns (the dict keys would silently collide).
    """
    all_cols = list(cols)
    if len(set(all_cols)) != len(all_cols):
        raise ValueError(f"duplicate columns: {all_cols}")
    sets: dict[str, list[str]] = {"all": list(all_cols)}
    for c in all_cols:
        sets[f"-{c}"] = [x for x in all_cols if x != c]
    return sets


def run_feature_ablation(
    feature_sets: dict[str, list[str]],
    model: object,
    features_by_symbol: dict[str, pd.DataFrame],
    labels_by_symbol: dict[str, pd.Series],
    prices_by_symbol: dict[str, pd.DataFrame],
    train_window: int = 504,
    test_window: int = 63,
    step: int = 63,
    embargo: int = 3,
    label_horizon: int = 1,
    **sim_kwargs: object,
) -> dict[str, BacktestResult]:
    """Run a single model under multiple feature-column sets, one result per set.

    The mirror image of ``run_label_ablation``: labels are fixed and
    caller-supplied; only the feature columns vary per run. For each set,
    every symbol's feature frame is sliced down to that set's columns (in
    the set's order) and forwarded to ``run_portfolio_backtest`` with all
    other kwargs held verbatim.

    Parameters
    ----------
    feature_sets:
        ``{set_name: [column, ...]}`` mapping, typically built via
        ``make_add_one_sets`` or ``make_leave_one_out_sets``. Must be
        non-empty. Names appear as keys in the returned dict.
    model:
        Anything with ``.fit(X, y)`` and ``.predict(X)``. Deep-copied per
        set so internal state cannot leak across runs.
    features_by_symbol, labels_by_symbol, prices_by_symbol:
        Per-symbol panel inputs forwarded to ``run_portfolio_backtest``.
        Feature frames must contain every column referenced by every set.
    train_window, test_window, step, embargo, label_horizon, **sim_kwargs:
        Walk-forward + simulator parameters. Held constant across sets
        (the kwargs-discipline that makes this a valid ablation).
        ``label_horizon`` is caller-supplied because the labels are fixed.

    Returns
    -------
    ``{set_name: BacktestResult}`` populated for every set.

    Raises
    ------
    ValueError if ``feature_sets`` is empty, or if any set references a
    column missing from any symbol's frame (named, with the symbols).
    Validation runs up front for *all* sets so a typo in the last set
    cannot waste hours of backtest compute on the earlier ones.
    """
    if not feature_sets:
        raise ValueError("run_feature_ablation needs at least one feature set")

    for name, cols in feature_sets.items():
        missing: dict[str, list[str]] = {}  # column -> symbols missing it
        for sym, frame in features_by_symbol.items():
            for col in cols:
                if col not in frame.columns:
                    missing.setdefault(col, []).append(sym)
        if missing:
            details = "; ".join(
                f"column {col!r} missing for symbols {syms}"
                for col, syms in missing.items()
            )
            raise ValueError(
                f"feature set {name!r} references columns absent from "
                f"features_by_symbol: {details}"
            )

    results: dict[str, BacktestResult] = {}
    for name, cols in feature_sets.items():
        sliced = {
            sym: frame[list(cols)] for sym, frame in features_by_symbol.items()
        }
        results[name] = run_portfolio_backtest(
            model=copy.deepcopy(model),
            features_by_symbol=sliced,
            labels_by_symbol=labels_by_symbol,
            prices_by_symbol=prices_by_symbol,
            train_window=train_window,
            test_window=test_window,
            step=step,
            label_horizon=label_horizon,
            embargo=embargo,
            **sim_kwargs,
        )

    return results
