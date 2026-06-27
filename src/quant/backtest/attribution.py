"""Out-of-sample feature attribution (Project B2 — METHODOLOGY §14).

Phase 4A's single largest *methodological* finding is that **in-sample feature
importance does not transfer out-of-sample on this problem** — SHAP (IS) vs
per-fold ablation lift (OOS) scored Spearman ρ = −0.074 across the 7 M3
candidates. METHODOLOGY §14 therefore pins OOS attribution (per-fold ablation,
OOS permutation importance) as the *only* trustworthy signal for keep/drop/propose
feature decisions. §14 names just one method today — per-feature ablation — which
costs ``O(n_features)`` full walk-forward backtests. B2 builds + validates a cheap
proxy.

This module is the B2-M2 implementation. The concept contract (the algorithm,
the pinned thresholds, the validation protocol) is frozen in
``docs/concepts/oos-attribution.md``; this module is its consumer (METHODOLOGY §4).

Public API
----------
* ``per_fold_ablation_attribution`` — a thin, reusable wrapper over
  ``ablation.run_feature_ablation`` that returns a per-feature OOS-lift ranking
  (the **canonical reference** signal G1 is judged against). Leave-one-out: a
  feature's importance is ``Sharpe(all) − Sharpe(all − f)`` on the aggregate OOS
  return series — how much removing the feature degrades OOS performance.
* ``oos_permutation_importance`` — the **cheap proxy under test**. It runs a
  private purged walk-forward that *retains* ``(fold_model, X_test)`` per fold
  (reusing ``walkforward_splits`` + the harness's pooled-fit discipline wholesale
  — no new split logic, ``backtest/CLAUDE.md`` invariants intact), then for each
  feature permutes that feature's column in the **test** matrix ``n_repeats``
  times, re-``predict``s with the already-fit fold model, and measures the OOS
  Sharpe degradation. Two orders of magnitude cheaper than ablation (predicts, no
  re-fits) and a genuine OOS signal (test slice only).
* ``b2_attribution_gate`` — the pre-committed G1–G3 gate (METHODOLOGY §2). It is
  the source of truth; ``docs/concepts/oos-attribution.md`` + the B2 PRD describe
  it. Thresholds are pinned defaults; changing one after a result is visible
  invalidates the run and needs a new ledger entry (METHODOLOGY §1).

Leakage note (hard invariant). Permutation touches **only ``X_test``** and reuses
the fold model fit on the training window; no test-set information ever flows into
a fit. The private walk-forward adds **no new split path** — it calls
``walkforward_splits`` exactly as the harness does, so the six ``backtest/CLAUDE.md``
invariants and the harness self-tests (random → ≈0 edge, leaky → caught) stay
green.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from quant.backtest.ablation import make_leave_one_out_sets, run_feature_ablation
from quant.backtest.harness import BacktestResult
from quant.backtest.metrics import compute_metrics
from quant.backtest.simulator import simulate
from quant.backtest.walkforward import walkforward_splits

# ─── Pinned gate thresholds (METHODOLOGY §1/§2 — do NOT retune to pass) ────────
# Reproduced verbatim from docs/concepts/oos-attribution.md + the B2 PRD. These
# are the defaults of ``b2_attribution_gate``; the function is the source of truth.

RHO_THRESHOLD: float = 0.50
"""G1 agreement materiality — Spearman ρ(permutation, ablation) must clear this.
The explicit inverse of the broken IS signal (ρ = −0.074 → ρ ≥ 0.50)."""

ALPHA: float = 0.05
"""G1 agreement significance — permutation-test p-value bar for the Spearman ρ."""

N_PERMUTATIONS: int = 10_000
"""G1 significance — number of random relabelings in the Spearman-ρ permutation test."""

REPRODUCTION_THRESHOLD: float = 0.90
"""G2 port reproducibility — systematized ablation vs nb08's published lifts."""

G3_EXPECTED_MAX: float = 0.1
"""G3 sanity floor — the SHAP(IS)-vs-ablation(OOS) ρ is *reported*, expected ≤ this
(reproduces ρ ≈ −0.074). Not part of the pass/fail conjunction."""

DEFAULT_N_REPEATS: int = 10
"""Permutation repeats per feature — averages out single-shuffle noise; the
per-feature std-error is reported across these repeats."""

DEFAULT_SIGNAL_THRESHOLD: float = 0.0
"""Decision boundary for ``sign(pred − threshold)``. 0.0 matches the M6 GBM (a
return *forecast*; the harness convention); pass 0.5 for a 0/1-probability model."""

MIN_SPEARMAN_FEATURES: int = 3
"""Spearman ρ over fewer than 3 features is degenerate — the gate refuses it."""


# ─── Result containers ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AblationImportance:
    """Per-feature OOS-lift ranking from leave-one-out ablation (the reference signal).

    ``importance[f] = metric(all) − metric(all − f)`` on the aggregate OOS series:
    a larger value means removing ``f`` degraded OOS performance more, i.e. ``f``
    contributed more. ``ranks`` is the descending rank (1 = most important).
    """

    importance: pd.Series
    ranks: pd.Series
    baseline_metric: float
    metric: str
    results: dict[str, BacktestResult] = field(default_factory=dict)


@dataclass(frozen=True)
class PermutationImportance:
    """Per-feature OOS permutation importance (the cheap proxy under test).

    ``importance[f]`` is the mean OOS-metric degradation when feature ``f``'s test
    column is permuted (baseline − permuted), averaged across folds and repeats;
    ``std_error[f]`` is its standard error across the ``n_repeats`` permutation
    passes. ``ranks`` is the descending rank (1 = most important).
    """

    importance: pd.Series
    std_error: pd.Series
    ranks: pd.Series
    baseline_metric: float
    metric: str
    n_folds: int
    n_repeats: int


# ─── per_fold_ablation_attribution — the canonical OOS reference signal ────────


def per_fold_ablation_attribution(
    model: object,
    features_by_symbol: dict[str, pd.DataFrame],
    labels_by_symbol: dict[str, pd.Series],
    prices_by_symbol: dict[str, pd.DataFrame],
    feature_columns: Sequence[str],
    *,
    metric: str = "sharpe",
    train_window: int = 504,
    test_window: int = 63,
    step: int = 63,
    label_horizon: int = 1,
    embargo: int = 3,
    **sim_kwargs: object,
) -> AblationImportance:
    """Per-feature OOS-lift ranking via leave-one-out ablation (the G1 reference).

    A thin wrapper over ``ablation.run_feature_ablation`` (so the leakage controls
    and kwargs-discipline are exactly the harness's — no new split logic): it runs
    the full feature set plus each leave-one-out set, then scores each feature by
    how much its removal degrades the aggregate OOS ``metric``::

        importance[f] = metric(all) − metric(all − f)

    Parameters
    ----------
    model:
        Anything with ``.fit(X, y)`` / ``.predict(X)``. Deep-copied per set by
        ``run_feature_ablation`` so state cannot leak across runs.
    features_by_symbol, labels_by_symbol, prices_by_symbol:
        Per-symbol panel inputs (identical keys). Feature frames must contain every
        column in ``feature_columns``.
    feature_columns:
        The feature set to attribute (e.g. the M6 25-column set for G1, or the 7
        nb08 candidates for G2). Order is preserved; importances are keyed by name.
    metric:
        The aggregate ``oos_metrics`` key to difference (default ``"sharpe"`` — the
        Phase-4A convention, so permutation and ablation compare like with like).
    train_window, test_window, step, label_horizon, embargo, **sim_kwargs:
        Walk-forward + simulator parameters, forwarded verbatim and held constant
        across sets (the discipline that makes this a valid ablation).

    Returns
    -------
    ``AblationImportance`` — ``.importance`` (Series, per feature), ``.ranks``
    (descending), ``.baseline_metric`` (the all-features metric), and the raw
    ``.results`` dict for inspection.

    Raises
    ------
    ValueError if ``feature_columns`` is empty or has duplicates.
    """
    cols = list(feature_columns)
    if not cols:
        raise ValueError("feature_columns must be non-empty")
    if len(set(cols)) != len(cols):
        raise ValueError(f"feature_columns has duplicates: {cols}")

    sets = make_leave_one_out_sets(cols)  # {"all": cols, "-f": cols\{f}, ...}
    results = run_feature_ablation(
        sets,
        model,
        features_by_symbol=features_by_symbol,
        labels_by_symbol=labels_by_symbol,
        prices_by_symbol=prices_by_symbol,
        train_window=train_window,
        test_window=test_window,
        step=step,
        label_horizon=label_horizon,
        embargo=embargo,
        **sim_kwargs,
    )

    baseline = float(results["all"].oos_metrics[metric])
    importance = pd.Series(
        {c: baseline - float(results[f"-{c}"].oos_metrics[metric]) for c in cols},
        name="ablation_importance",
    )
    ranks = importance.rank(ascending=False)
    return AblationImportance(
        importance=importance,
        ranks=ranks,
        baseline_metric=baseline,
        metric=metric,
        results=results,
    )


# ─── oos_permutation_importance — the cheap proxy under test ───────────────────


@dataclass
class _Fold:
    """One fold's retained artifacts: the fit model + per-symbol test blocks."""

    model: object
    x_test: dict[str, np.ndarray]       # symbol -> (n_test_rows, n_features)
    idx_test: dict[str, pd.DatetimeIndex]
    prices_test: dict[str, pd.DataFrame]
    baseline_sharpe: float


def _aligned_feature_matrix(frame: pd.DataFrame, cols: Sequence[str]) -> np.ndarray:
    """Slice ``frame`` to ``cols`` (in order) as a float ndarray."""
    return frame[list(cols)].to_numpy(dtype=float)


def _fold_return_series(
    fold: _Fold,
    *,
    permuted_col: int | None,
    rng: np.random.Generator | None,
    signal_threshold: float,
    sim_kwargs: Mapping[str, object],
) -> pd.Series:
    """Cross-sectional OOS return series for one fold, optionally permuting a column.

    For each symbol: predict with the (already-fit) fold model on its test matrix
    (a per-symbol-shuffled copy of column ``permuted_col`` when requested), map to
    ``sign(pred − signal_threshold)`` signals, route through ``simulate`` on the
    symbol's OOS prices, and average per-symbol returns across symbols per bar
    (equal-weight cross-section — the harness aggregation).
    """
    per_symbol: list[pd.Series] = []
    for sym, x in fold.x_test.items():
        if permuted_col is not None:
            x = x.copy()
            assert rng is not None
            x[:, permuted_col] = x[rng.permutation(x.shape[0]), permuted_col]
        raw = np.asarray(fold.model.predict(x), dtype=float)  # type: ignore[attr-defined]
        signals = pd.Series(
            np.sign(raw - signal_threshold).astype(int), index=fold.idx_test[sym]
        )
        eq, _ = simulate(fold.prices_test[sym], signals, **sim_kwargs)  # type: ignore[arg-type]
        per_symbol.append(eq.pct_change().dropna())
    if not per_symbol:
        return pd.Series(dtype=float)
    return pd.concat(per_symbol, axis=1).mean(axis=1, skipna=True).dropna()


def _fit_folds(
    model: object,
    features_by_symbol: dict[str, pd.DataFrame],
    labels_by_symbol: dict[str, pd.Series],
    prices_by_symbol: dict[str, pd.DataFrame],
    cols: Sequence[str],
    *,
    train_window: int,
    test_window: int,
    step: int,
    label_horizon: int,
    embargo: int,
    signal_threshold: float,
    sim_kwargs: Mapping[str, object],
) -> list[_Fold]:
    """Run the purged walk-forward, retaining (fit model, test matrices) per fold.

    Reuses ``walkforward_splits`` + the harness's pooled cross-sectional fit
    (all alive symbols stacked vertically) exactly — no re-implementation of split
    logic. A fresh ``copy.deepcopy(model)`` is fit per fold and retained alongside
    each symbol's sliced test matrix so permutation reuses the already-fit model.
    """
    symbols = list(features_by_symbol.keys())
    master_idx = features_by_symbol[symbols[0]].index
    for sym in symbols[1:]:
        master_idx = master_idx.union(features_by_symbol[sym].index)
    master_idx = master_idx.sort_values().unique()

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

    folds: list[_Fold] = []
    for train_pos, test_pos in splits:
        if len(train_pos) == 0:
            continue

        x_train_parts: list[np.ndarray] = []
        y_train_parts: list[np.ndarray] = []
        for sym in symbols:
            train_mask = alive[sym][train_pos]
            if not train_mask.any():
                continue
            sym_train_idx = master_idx[train_pos][train_mask]
            x_train_parts.append(
                _aligned_feature_matrix(features_by_symbol[sym].loc[sym_train_idx], cols)
            )
            y_train_parts.append(labels_by_symbol[sym].loc[sym_train_idx].to_numpy())
        if not x_train_parts:
            continue

        fold_model = copy.deepcopy(model)
        fold_model.fit(np.vstack(x_train_parts), np.concatenate(y_train_parts))  # type: ignore[attr-defined]

        test_master_idx = master_idx[test_pos]
        x_test: dict[str, np.ndarray] = {}
        idx_test: dict[str, pd.DatetimeIndex] = {}
        prices_test: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            test_mask = alive[sym][test_pos]
            if not test_mask.any():
                continue
            sym_test_idx = test_master_idx[test_mask]
            x_test[sym] = _aligned_feature_matrix(
                features_by_symbol[sym].loc[sym_test_idx], cols
            )
            idx_test[sym] = sym_test_idx
            prices_test[sym] = prices_by_symbol[sym].loc[sym_test_idx]
        if not x_test:
            continue

        fold = _Fold(
            model=fold_model,
            x_test=x_test,
            idx_test=idx_test,
            prices_test=prices_test,
            baseline_sharpe=float("nan"),
        )
        base_ret = _fold_return_series(
            fold,
            permuted_col=None,
            rng=None,
            signal_threshold=signal_threshold,
            sim_kwargs=sim_kwargs,
        )
        fold.baseline_sharpe = (
            float(compute_metrics(base_ret)["sharpe"]) if len(base_ret) else float("nan")
        )
        # A fold whose unpermuted Sharpe is undefined (e.g. all-flat signals on a
        # tiny window) carries no usable degradation signal — drop it rather than
        # poison the cross-fold average with NaN.
        if np.isfinite(fold.baseline_sharpe):
            folds.append(fold)

    return folds


def oos_permutation_importance(
    model: object,
    features_by_symbol: dict[str, pd.DataFrame],
    labels_by_symbol: dict[str, pd.Series],
    prices_by_symbol: dict[str, pd.DataFrame],
    feature_columns: Sequence[str],
    *,
    n_repeats: int = DEFAULT_N_REPEATS,
    signal_threshold: float = DEFAULT_SIGNAL_THRESHOLD,
    train_window: int = 504,
    test_window: int = 63,
    step: int = 63,
    label_horizon: int = 1,
    embargo: int = 3,
    seed: int = 0,
    **sim_kwargs: object,
) -> PermutationImportance:
    """OOS permutation importance: the cheap proxy for the ablation reference.

    Implements the algorithm frozen in ``docs/concepts/oos-attribution.md``:

    1. A purged walk-forward (``walkforward_splits``, reused wholesale) that
       *retains* ``(fold_model, X_test)`` per fold — pooled cross-sectional fit,
       identical leakage controls.
    2. Per fold, the baseline OOS Sharpe of the unpermuted ``sign(pred)`` strategy.
    3. For each feature ``f`` and each of ``n_repeats`` passes: shuffle ``f``'s
       column in each symbol's ``X_test`` (preserving its marginal, destroying its
       target relationship), re-``predict`` with the already-fit model, and record
       the per-fold degradation ``baseline − permuted``, averaged across folds.
    4. ``importance[f]`` = mean over repeats; ``std_error[f]`` = std/√n_repeats.

    The degradation metric is **OOS Sharpe of the simulated ``sign(pred)``
    strategy** — the same metric ``per_fold_ablation_attribution`` differences, so
    the G1 agreement gate compares like with like.

    Parameters
    ----------
    model, features_by_symbol, labels_by_symbol, prices_by_symbol, feature_columns:
        As ``per_fold_ablation_attribution``. The model is deep-copied per fold.
    n_repeats:
        Permutation passes per feature (default ``DEFAULT_N_REPEATS``). The
        std-error is reported across these.
    signal_threshold:
        ``sign(pred − threshold)`` boundary (default 0.0 — the return-forecast
        convention; 0.5 for a 0/1-probability model).
    train_window, test_window, step, label_horizon, embargo, **sim_kwargs:
        Walk-forward + simulator parameters, same semantics as the harness.
    seed:
        Seed for the permutation RNG — results are deterministic.

    Returns
    -------
    ``PermutationImportance`` — ``.importance`` / ``.std_error`` / ``.ranks``
    (Series per feature), ``.baseline_metric`` (mean per-fold baseline Sharpe),
    ``.n_folds``, ``.n_repeats``.

    Raises
    ------
    ValueError if ``feature_columns`` is empty / duplicated, the panel keys
    mismatch, or no fold produced a finite baseline Sharpe.
    """
    cols = list(feature_columns)
    if not cols:
        raise ValueError("feature_columns must be non-empty")
    if len(set(cols)) != len(cols):
        raise ValueError(f"feature_columns has duplicates: {cols}")
    symbols = list(features_by_symbol.keys())
    if not symbols:
        raise ValueError("features_by_symbol must contain at least one symbol")
    if set(symbols) != set(labels_by_symbol) or set(symbols) != set(prices_by_symbol):
        raise ValueError(
            "features_by_symbol, labels_by_symbol, prices_by_symbol must share keys"
        )
    if n_repeats < 1:
        raise ValueError(f"n_repeats must be >= 1, got {n_repeats}")

    folds = _fit_folds(
        model,
        features_by_symbol,
        labels_by_symbol,
        prices_by_symbol,
        cols,
        train_window=train_window,
        test_window=test_window,
        step=step,
        label_horizon=label_horizon,
        embargo=embargo,
        signal_threshold=signal_threshold,
        sim_kwargs=sim_kwargs,
    )
    if not folds:
        raise ValueError(
            "no fold produced a finite baseline Sharpe — the walk-forward "
            "configuration yields no usable OOS window for permutation importance"
        )

    rng = np.random.default_rng(seed)
    # per_repeat[r][f] = cross-fold mean degradation for feature f on pass r.
    per_repeat: list[dict[str, float]] = []
    for _ in range(n_repeats):
        repeat_imp: dict[str, float] = {}
        for j, col in enumerate(cols):
            fold_degradations: list[float] = []
            for fold in folds:
                permuted_ret = _fold_return_series(
                    fold,
                    permuted_col=j,
                    rng=rng,
                    signal_threshold=signal_threshold,
                    sim_kwargs=sim_kwargs,
                )
                permuted_sharpe = (
                    float(compute_metrics(permuted_ret)["sharpe"])
                    if len(permuted_ret)
                    else fold.baseline_sharpe
                )
                fold_degradations.append(fold.baseline_sharpe - permuted_sharpe)
            repeat_imp[col] = float(np.mean(fold_degradations))
        per_repeat.append(repeat_imp)

    repeat_frame = pd.DataFrame(per_repeat)[cols]  # rows = repeats, cols = features
    importance = repeat_frame.mean(axis=0)
    importance.name = "permutation_importance"
    if n_repeats > 1:
        std_error = repeat_frame.std(axis=0, ddof=1) / np.sqrt(n_repeats)
    else:
        std_error = pd.Series(0.0, index=cols)
    std_error.name = "std_error"

    return PermutationImportance(
        importance=importance,
        std_error=std_error,
        ranks=importance.rank(ascending=False),
        baseline_metric=float(np.mean([f.baseline_sharpe for f in folds])),
        metric="sharpe",
        n_folds=len(folds),
        n_repeats=n_repeats,
    )


# ─── b2_attribution_gate — the pre-committed G1–G3 verdict (METHODOLOGY §2) ────


def _spearman_rho(a: Mapping[str, float], b: Mapping[str, float]) -> tuple[float, list[str]]:
    """Spearman ρ between two per-feature score maps over their common keys.

    Returns ``(rho, common_keys)``. Raises ValueError if fewer than
    ``MIN_SPEARMAN_FEATURES`` keys are shared (ρ is degenerate below 3 points).
    """
    common = [k for k in a if k in b]
    if len(common) < MIN_SPEARMAN_FEATURES:
        raise ValueError(
            f"need >= {MIN_SPEARMAN_FEATURES} common features for Spearman ρ, "
            f"got {len(common)}: {common}"
        )
    va = np.array([float(a[k]) for k in common], dtype=float)
    vb = np.array([float(b[k]) for k in common], dtype=float)
    rho = float(stats.spearmanr(va, vb).statistic)
    return rho, common


def _spearman_permutation_p(
    a: Mapping[str, float],
    b: Mapping[str, float],
    common: Sequence[str],
    observed_rho: float,
    *,
    n_permutations: int,
    seed: int,
) -> float:
    """One-sided (upper-tail) permutation-test p-value for a Spearman ρ vs the ρ=0 null.

    Shuffles one ranking ``n_permutations`` times and counts how often a permuted ρ
    reaches the observed ρ. Uses the ``(1 + count) / (1 + n)`` add-one estimator so
    a Monte-Carlo p is never exactly zero. One-sided upper because G1 tests *positive
    agreement* (the cheap proxy reproducing the reference), not mere dependence.
    """
    va = np.array([float(a[k]) for k in common], dtype=float)
    vb = np.array([float(b[k]) for k in common], dtype=float)
    rng = np.random.default_rng(seed)
    rank_a = stats.rankdata(va)
    rank_b = stats.rankdata(vb)
    count = 0
    for _ in range(n_permutations):
        perm_rho = float(np.corrcoef(rank_a, rng.permutation(rank_b))[0, 1])
        if perm_rho >= observed_rho:
            count += 1
    return (1 + count) / (1 + n_permutations)


def b2_attribution_gate(
    permutation_importance: Mapping[str, float],
    ablation_importance: Mapping[str, float],
    *,
    reproduction: tuple[Mapping[str, float], Mapping[str, float]] | None = None,
    shap_contrast: tuple[Mapping[str, float], Mapping[str, float]] | None = None,
    rho_threshold: float = RHO_THRESHOLD,
    alpha: float = ALPHA,
    n_permutations: int = N_PERMUTATIONS,
    reproduction_threshold: float = REPRODUCTION_THRESHOLD,
    seed: int = 0,
) -> dict[str, Any]:
    """The pre-committed B2 attribution gate — G1 (materiality + significance) ∧ G2.

    This function is the **source of truth** for the B2 verdict (METHODOLOGY §2);
    ``docs/concepts/oos-attribution.md`` and the B2 PRD describe it. ``gate_passed``
    is the conjunction of:

    1. **G1 materiality** — ``spearman_rho(permutation, ablation) >= rho_threshold``
       (default 0.50). Materiality precedes significance (METHODOLOGY §10).
    2. **G1 significance** — the upper-tail permutation test of that ρ against the
       ρ = 0 null has ``p < alpha`` (default 0.05, ``n_permutations`` ≥ 10,000).
    3. **G2 port reproducibility** — when ``reproduction=(systematized_ablation,
       published_lifts)`` is supplied, ``spearman_rho(...) >= reproduction_threshold``
       (default 0.90). A real verdict requires this — the reference signal must be
       a faithful port of M3 before G1 is meaningful, so ``reproduction=None``
       reports G2 as unverified and forces ``gate_passed=False``.

    **G3** (the SHAP(IS)-vs-ablation(OOS) contrast) is *reported* for context when
    ``shap_contrast`` is supplied — expected ρ ≤ 0.1, reproducing ρ ≈ −0.074 — but
    is **not** part of the conjunction.

    All thresholds are pinned defaults (METHODOLOGY §1) — changing one after a
    result is visible invalidates the run and requires a new ledger entry.

    Parameters
    ----------
    permutation_importance, ablation_importance:
        Per-feature score maps over a common feature set (the M6 25-column set for
        the real G1 run). Higher = more important; the gate scores their *ranks*.
    reproduction:
        Optional ``(systematized_ablation, published_lifts)`` per-feature maps over
        the 7 nb08 candidates, for G2. ``None`` → G2 unverified → gate fails.
    shap_contrast:
        Optional ``(shap_is, ablation_oos)`` per-feature maps for the reported G3.
    rho_threshold, alpha, n_permutations, reproduction_threshold, seed:
        Pinned gate parameters (see module constants) + the permutation-test seed.

    Returns
    -------
    dict with keys: ``g1_rho``, ``g1_p_value``, ``g1_materiality_passed``,
    ``g1_significance_passed``, ``g1_n_features``, ``g2_rho``, ``g2_passed``,
    ``g3_rho``, ``gate_passed``, and an echo of the four thresholds.
    """
    g1_rho, common = _spearman_rho(permutation_importance, ablation_importance)
    g1_materiality_passed = g1_rho >= rho_threshold
    g1_p_value = _spearman_permutation_p(
        permutation_importance,
        ablation_importance,
        common,
        g1_rho,
        n_permutations=n_permutations,
        seed=seed,
    )
    g1_significance_passed = g1_p_value < alpha

    if reproduction is not None:
        g2_rho, _ = _spearman_rho(reproduction[0], reproduction[1])
        g2_passed: bool | None = g2_rho >= reproduction_threshold
    else:
        g2_rho = None
        g2_passed = None  # unverified port — cannot certify the reference signal

    g3_rho: float | None = None
    if shap_contrast is not None:
        g3_rho, _ = _spearman_rho(shap_contrast[0], shap_contrast[1])

    gate_passed = bool(
        g1_materiality_passed and g1_significance_passed and (g2_passed is True)
    )

    return {
        "g1_rho": g1_rho,
        "g1_p_value": g1_p_value,
        "g1_materiality_passed": bool(g1_materiality_passed),
        "g1_significance_passed": bool(g1_significance_passed),
        "g1_n_features": len(common),
        "g2_rho": g2_rho,
        "g2_passed": g2_passed,
        "g3_rho": g3_rho,
        "gate_passed": gate_passed,
        "rho_threshold": rho_threshold,
        "alpha": alpha,
        "n_permutations": n_permutations,
        "reproduction_threshold": reproduction_threshold,
    }


# ─── classify_attribution_status — the B2-M3 catalog-population rule ────────────

ATTRIBUTION_STATUS_VALUES: tuple[str, ...] = (
    "none",
    "ablation_only",
    "oos_permutation",
    "both",
    "agreed",
)
"""The catalog ``attribution_status`` enum (mirrors ``FeatureRecord``). Single
source of truth so ``catalog.py`` and the B2-M3 population/drift test agree."""


def classify_attribution_status(
    ablation: float | None,
    permutation: float | None,
    *,
    gate_passed: bool,
) -> str:
    """Map a feature's two OOS-attribution signals to a catalog ``attribution_status``.

    The B2-M3 catalog-population rule, **pinned before per-feature results were
    inspected** (METHODOLOGY §1). It encodes which evidence backs each feature:

    * ``"agreed"`` — **both** signals computed **and** the OOS-permutation proxy is
      *validated as a method* (the B2 G1 gate passed, ``gate_passed=True``) **and**
      the two signals concur on the **sign** of this feature's OOS contribution.
    * ``"both"`` — both signals computed but not certified to agree: either the
      method-level gate failed (the proxy is untrusted — METHODOLOGY §14, so a
      per-feature sign coincidence is **not** promoted to ``agreed``; that would be
      post-hoc cherry-picking after a failed validation, METHODOLOGY §10) or the
      signs disagree.
    * ``"ablation_only"`` — only the canonical per-fold-ablation lift was computed.
    * ``"oos_permutation"`` — only the (cheap-proxy) permutation importance was
      computed.
    * ``"none"`` — neither signal computed (the catalog default).

    ``gate_passed`` is the ``b2_attribution_gate`` verdict (the source of truth,
    METHODOLOGY §2): ``"agreed"`` is reachable only when the proxy validated, so a
    failed B2 run records co-attributed features as ``"both"`` — both were tried,
    neither feature is certified as agreeing.

    Non-finite (NaN) inputs are treated as "not computed".
    """
    has_abl = ablation is not None and np.isfinite(ablation)
    has_perm = permutation is not None and np.isfinite(permutation)
    if has_abl and has_perm:
        concordant = np.sign(ablation) == np.sign(permutation)
        return "agreed" if (gate_passed and concordant) else "both"
    if has_abl:
        return "ablation_only"
    if has_perm:
        return "oos_permutation"
    return "none"
