"""Per-regime aggregation of backtest metrics, DM tests, and gate reports.

The existing harness aggregates OOS metrics across the full evaluation
period uniformly. For Phase 4A we need to slice those metrics by regime
(VIX-volatility axis, macro-era axis, or any other axis supplied via the
``regime_labels`` Series) so the researcher can tell whether the model has
edge *in some regime* even when the full-period Sharpe is uninformative.

This module is consumed by notebooks and the Phase 4A exit-gate report;
it has no side effects and never touches splits or purge/embargo logic.

Public API
----------
* ``compute_regime_metrics(returns, regime_labels)`` — per-regime metric dicts
* ``regime_dm_test(errors_a, errors_b, regime_labels)`` — per-regime DM tests
* ``phase4a_gate_report(gbm, arima, regime_labels, ...)`` — the Phase 4A
  success-gate verdict matching the PRD's metric exactly:
  "GBM beats ARIMA Sharpe in ≥ 2 of 3 recent regimes, with DM p < 0.05 in
  ≥ 1 of those regimes."
* ``dsr_aware_gate_report(gbm, arima, regime_labels, ...)`` — the §13 two-stage
  gate: ``phase4a_gate_report`` AND a deflated-Sharpe (Bailey & López de Prado
  2014) second stage on the aggregate Sharpe, with the deflation N read from
  ``quant.ledger.cumulative_trial_count``.

DM-test semantics
-----------------
A regime with fewer than ``MIN_DM_OBS`` (= 4) observations cannot be
DM-tested and is returned as ``None``. The gate report counts those regimes
as "insufficient evidence" rather than "fail" — see ``phase4a_gate_report``
for the exact rule.
"""
from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from quant.features.targets import MaterialityCriterion, TargetSpec

from quant.backtest.harness import BacktestResult
from quant.backtest.metrics import compute_metrics
from quant.backtest.statistics import (
    DEFAULT_SHARPE_STD,
    DSR_THRESHOLD,
    DMResult,
    DSRResult,
    deflated_sharpe_ratio,
    diebold_mariano,
)
from quant.ledger import cumulative_trial_count

MIN_DM_OBS = 4

# Default regime set referenced by the Phase 4A PRD success metric.
DEFAULT_REGIMES_REQUIRED: tuple[str, ...] = ("qe_bull", "covid", "rate_cycle")


# ─── compute_regime_metrics ──────────────────────────────────────────────────


def compute_regime_metrics(
    returns: pd.Series,
    regime_labels: pd.Series,
) -> dict[str, dict[str, float]]:
    """Group ``returns`` by ``regime_labels`` and compute the full metric set per regime.

    Both arguments must share an identical index — there is no implicit
    re-alignment. Mismatched indices raise ``ValueError``.

    Returns a mapping ``{regime_label: metric_dict}`` where each
    ``metric_dict`` has the same keys as ``compute_metrics`` (sharpe,
    sortino, calmar, max_drawdown, total_return, annualized_return, hit_rate,
    profit_factor). Regimes with zero observations are omitted.
    """
    if not returns.index.equals(regime_labels.index):
        raise ValueError(
            "returns and regime_labels must share an identical index — "
            "align them before calling compute_regime_metrics"
        )

    return {
        regime: compute_metrics(returns.loc[regime_labels == regime])
        for regime in regime_labels.unique()
        if (regime_labels == regime).any()
    }


# ─── regime_dm_test ──────────────────────────────────────────────────────────


def regime_dm_test(
    errors_a: pd.Series,
    errors_b: pd.Series,
    regime_labels: pd.Series,
    *,
    alternative: str = "less",
) -> dict[str, DMResult | None]:
    """Run a Diebold-Mariano test per regime, comparing two error series.

    For each regime in ``regime_labels``, slice both error series down to
    that regime's bars and run ``diebold_mariano(errors_a, errors_b)``.
    Regimes with fewer than ``MIN_DM_OBS`` observations return ``None``
    — the DM test is undefined at thin samples and crashing the whole
    report on a 3-bar regime is the wrong response.

    Returns a mapping ``{regime_label: DMResult | None}``.
    """
    if not (
        errors_a.index.equals(errors_b.index)
        and errors_a.index.equals(regime_labels.index)
    ):
        raise ValueError(
            "errors_a, errors_b, and regime_labels must share an identical index"
        )

    results: dict[str, DMResult | None] = {}
    for regime in regime_labels.unique():
        mask = (regime_labels == regime).to_numpy()
        n_obs = int(mask.sum())
        if n_obs < MIN_DM_OBS:
            warnings.warn(
                f"regime {regime!r} has only {n_obs} observations — "
                f"skipping DM test (need at least {MIN_DM_OBS})",
                stacklevel=2,
            )
            results[regime] = None
            continue
        try:
            results[regime] = diebold_mariano(
                errors_a.to_numpy()[mask],
                errors_b.to_numpy()[mask],
                alternative=alternative,
            )
        except ValueError as exc:
            # Identical error series in a regime → zero variance → DM undefined.
            warnings.warn(
                f"regime {regime!r} DM test failed: {exc}",
                stacklevel=2,
            )
            results[regime] = None
    return results


# ─── phase4a_gate_report ─────────────────────────────────────────────────────


def phase4a_gate_report(
    gbm_result: BacktestResult,
    arima_result: BacktestResult,
    regime_labels: pd.Series,
    *,
    regimes_required: tuple[str, ...] = DEFAULT_REGIMES_REQUIRED,
    min_pass: int = 2,
    dm_alpha: float = 0.05,
) -> dict[str, Any]:
    """Evaluate the Phase 4A success gate for a GBM vs ARIMA comparison.

    The gate (from the Phase 4A PRD success metric) requires:

    * GBM Sharpe > ARIMA Sharpe in at least ``min_pass`` of the regimes in
      ``regimes_required`` (default 2 of 3).
    * Diebold-Mariano p-value < ``dm_alpha`` in at least one of those regimes
      (default p < 0.05 in ≥ 1).

    Parameters
    ----------
    gbm_result, arima_result:
        ``BacktestResult`` objects from ``run_portfolio_backtest`` containing
        ``oos_returns`` and ``oos_forecast_errors`` series.
    regime_labels:
        Per-bar regime labels aligned with ``gbm_result.oos_returns.index``.
        Typically produced by ``tag_regimes(result.oos_returns.index, detector)``.
    regimes_required:
        The regimes the gate evaluates. Regimes outside this tuple are still
        reported in ``per_regime`` but do not count toward ``pass_count``.
    min_pass:
        Minimum count of Sharpe wins required to pass.
    dm_alpha:
        Maximum DM p-value to count as statistically significant.

    Returns
    -------
    dict with keys:

    * ``per_regime``     — ``{regime: {gbm_sharpe, arima_sharpe, dm_p_value, ...}}``
    * ``gate_passed``    — bool — overall gate verdict
    * ``pass_count``     — int  — number of ``regimes_required`` where GBM > ARIMA
    * ``dm_p_values``    — ``{regime: float | None}`` — DM p-values for the
                           required regimes only
    * ``regimes_required`` — echo of input, included so downstream consumers
                             don't need to re-thread the argument.
    """
    gbm_per_regime = compute_regime_metrics(gbm_result.oos_returns, regime_labels)
    arima_per_regime = compute_regime_metrics(arima_result.oos_returns, regime_labels)

    dm = regime_dm_test(
        gbm_result.oos_forecast_errors,
        arima_result.oos_forecast_errors,
        regime_labels,
        alternative="less",  # H1: GBM errors smaller than ARIMA errors
    )

    per_regime: dict[str, dict[str, Any]] = {}
    for regime in regime_labels.unique():
        gbm_sharpe = gbm_per_regime.get(regime, {}).get("sharpe", np.nan)
        arima_sharpe = arima_per_regime.get(regime, {}).get("sharpe", np.nan)
        dm_res = dm.get(regime)
        per_regime[regime] = {
            "gbm_sharpe": float(gbm_sharpe),
            "arima_sharpe": float(arima_sharpe),
            "gbm_beats_arima": bool(gbm_sharpe > arima_sharpe),
            "dm_p_value": (dm_res.p_value if dm_res is not None else None),
            "n_bars": int((regime_labels == regime).sum()),
        }

    pass_count = sum(
        1
        for r in regimes_required
        if per_regime.get(r, {}).get("gbm_beats_arima", False)
    )
    dm_p_values = {
        r: per_regime.get(r, {}).get("dm_p_value")
        for r in regimes_required
    }
    significant_dm_count = sum(
        1
        for p in dm_p_values.values()
        if p is not None and p < dm_alpha
    )

    gate_passed = pass_count >= min_pass and significant_dm_count >= 1

    return {
        "per_regime": per_regime,
        "gate_passed": gate_passed,
        "pass_count": pass_count,
        "dm_p_values": dm_p_values,
        "regimes_required": regimes_required,
    }


# ─── dsr_aware_gate_report ───────────────────────────────────────────────────


def dsr_aware_gate_report(
    gbm_result: BacktestResult,
    arima_result: BacktestResult,
    regime_labels: pd.Series,
    *,
    n_trials: int | None = None,
    sharpe_std: float = DEFAULT_SHARPE_STD,
    dsr_threshold: float = DSR_THRESHOLD,
    regimes_required: tuple[str, ...] = DEFAULT_REGIMES_REQUIRED,
    min_pass: int = 2,
    dm_alpha: float = 0.05,
) -> dict[str, Any]:
    """Two-stage gate: the regime Sharpe/DM gate AND a deflated-Sharpe second stage.

    METHODOLOGY §13 ("DSR-aware gates"): a gate-pass must clear both the
    pre-committed regime gate **and** the deflated Sharpe (Bailey & López de
    Prado, 2014). The deflation trial count ``N`` is read from the ledger
    (``quant.ledger.cumulative_trial_count``) rather than hand-counted, so future
    PRD gates deflate against the project's *current* cumulative N automatically.

    Stage 1 is ``phase4a_gate_report`` verbatim (unchanged — existing callers and
    its verdict are untouched). Stage 2 computes the DSR on the GBM **aggregate**
    OOS return series (``gbm_result.oos_returns``); the headline Sharpe is what
    multiple-testing deflation applies to. The combined gate passes iff
    ``stage1.gate_passed and dsr > dsr_threshold``.

    Parameters
    ----------
    gbm_result, arima_result, regime_labels, regimes_required, min_pass, dm_alpha:
        Passed straight through to ``phase4a_gate_report``.
    n_trials:
        Deflation N. ``None`` (default) reads ``cumulative_trial_count()`` from
        the ledger; pass an int to deflate against a fixed N (used in tests).
    sharpe_std:
        Annualised cross-trial Sharpe dispersion for the expected-max benchmark
        (default ``DEFAULT_SHARPE_STD``).
    dsr_threshold:
        DSR pass threshold (default ``DSR_THRESHOLD`` = 0.5).

    Returns
    -------
    dict — every key from ``phase4a_gate_report`` plus:

    * ``stage1_passed`` — bool — the regime gate verdict on its own
    * ``dsr``           — float — the deflated Sharpe ratio (probability)
    * ``dsr_passed``    — bool — ``dsr > dsr_threshold``
    * ``dsr_result``    — ``DSRResult`` — full DSR detail (SR_obs, SR_benchmark, …)
    * ``n_trials``      — int — the deflation N actually used
    * ``sr_observed``   — float — GBM aggregate annualised Sharpe
    * ``sr_benchmark``  — float — expected best-of-N annualised Sharpe under null

    The top-level ``gate_passed`` is **overwritten** with the combined verdict;
    the stage-1-only verdict is preserved under ``stage1_passed``.
    """
    stage1 = phase4a_gate_report(
        gbm_result,
        arima_result,
        regime_labels,
        regimes_required=regimes_required,
        min_pass=min_pass,
        dm_alpha=dm_alpha,
    )

    if n_trials is None:
        n_trials = cumulative_trial_count()

    dsr_result: DSRResult = deflated_sharpe_ratio(
        gbm_result.oos_returns,
        n_trials,
        sharpe_std=sharpe_std,
        threshold=dsr_threshold,
    )

    combined_passed = bool(stage1["gate_passed"] and dsr_result.passed)

    return {
        **stage1,
        "stage1_passed": stage1["gate_passed"],
        "dsr": dsr_result.dsr,
        "dsr_passed": dsr_result.passed,
        "dsr_result": dsr_result,
        "n_trials": int(n_trials),
        "sr_observed": dsr_result.sr_observed,
        "sr_benchmark": dsr_result.sr_benchmark,
        "gate_passed": combined_passed,
    }


# ─── b1_gate_report ──────────────────────────────────────────────────────────


def _criterion_met(
    criterion: "MaterialityCriterion",
    variant: float | None,
    baseline: float | None,
) -> tuple[bool, float | None]:
    """Evaluate one ``MaterialityCriterion`` against a (variant, baseline) pair.

    Returns ``(met, value)`` where ``value`` is the computed delta/reduction (or
    ``None`` when an input is missing or the reduction denominator is zero).
    """
    if variant is None or baseline is None:
        return False, None
    if criterion.kind == "delta_higher":
        delta = float(variant) - float(baseline)
        return delta >= criterion.threshold, delta
    if criterion.kind == "rel_reduction":
        if baseline == 0:
            return False, None
        reduction = (float(baseline) - float(variant)) / float(baseline)
        return reduction >= criterion.threshold, reduction
    raise ValueError(f"unknown materiality kind {criterion.kind!r}")


def b1_gate_report(
    spec: "TargetSpec",
    per_regime_metrics: dict[str, dict[str, dict[str, float]]],
    significance_ci: dict[str, tuple[float, float]],
    deflation_passed: bool,
    *,
    regimes_required: tuple[str, ...] = DEFAULT_REGIMES_REQUIRED,
    min_pass: int = 2,
    deflation_detail: Any = None,
) -> dict[str, Any]:
    """Pre-committed B1 target-reframing gate (PRD ``b1-target-reframing.prd.md``).

    This is the source of truth for the B1 verdict (METHODOLOGY §2): the PRD's
    "Pre-committed gate" prose describes this function; the function decides. It
    generalises ``phase4a_gate_report`` from a fixed GBM-vs-ARIMA Sharpe test to
    an arbitrary per-target metric supplied via ``spec.materiality``, and renders
    a pure verdict over already-computed per-regime metrics, significance CIs, and
    a deflation result — so it is deterministically unit-testable and the heavy
    metric/bootstrap/DSR machinery lives in its callers (B1-M2).

    For a single ``(target, arm)`` result the gate passes iff the conjunction of:

    1. **Materiality** — *every* criterion in ``spec.materiality`` is met in a
       regime (directional targets carry both an AUC and a Sharpe criterion), in
       at least ``min_pass`` of the ``regimes_required``.
    2. **Significance** — the paired stationary-block-bootstrap CI of the gated
       metric delta (see ``statistics.bootstrap_metric_delta_ci``) **excludes 0**
       in at least one required regime.
    3. **Deflation** — ``deflation_passed`` is ``True`` (deflated Sharpe > 0 for
       directional targets, or the vol/drawdown skill-z analog > 0), with the
       deflation N read from ``quant.ledger.cumulative_trial_count`` by the caller
       (the A-DSR-GATE deliverable; ``spec.deflation`` selects the method).

    Parameters
    ----------
    spec:
        The target's pre-committed ``TargetSpec`` (carries the metric thresholds).
    per_regime_metrics:
        ``{regime: {metric: {"variant": float, "baseline": float}}}`` — the
        variant (model) and better-baseline value of each gated metric per regime.
        Regimes outside ``regimes_required`` are ignored in the verdict.
    significance_ci:
        ``{regime: (ci_low, ci_high)}`` — the bootstrap CI of the gated metric
        delta per regime. The significance test is "the interval excludes 0".
    deflation_passed:
        Stage-3 verdict, pre-computed by the caller per ``spec.deflation``.
    regimes_required:
        The regimes the gate evaluates (default ``qe_bull, covid, rate_cycle``).
    min_pass:
        Minimum count of materiality-passing required regimes (default 2).
    deflation_detail:
        Optional opaque detail (e.g. a ``DSRResult``) echoed in the output.

    Returns
    -------
    dict with keys:

    * ``target``              — ``spec.name``
    * ``per_regime``          — per-required-regime materiality + significance detail
    * ``materiality_passed``  — bool
    * ``material_pass_count`` — int
    * ``significance_passed`` — bool
    * ``deflation_passed``    — bool
    * ``deflation_detail``    — echo of input
    * ``gate_passed``         — bool — the three-stage conjunction
    * ``regimes_required`` / ``min_pass`` — echo of inputs
    """
    per_regime: dict[str, dict[str, Any]] = {}
    for regime in regimes_required:
        metrics = per_regime_metrics.get(regime, {})
        criteria_detail: list[dict[str, Any]] = []
        all_met = True
        for crit in spec.materiality:
            pair = metrics.get(crit.metric, {})
            met, value = _criterion_met(
                crit, pair.get("variant"), pair.get("baseline")
            )
            criteria_detail.append(
                {
                    "metric": crit.metric,
                    "kind": crit.kind,
                    "threshold": crit.threshold,
                    "value": value,
                    "met": met,
                }
            )
            all_met = all_met and met

        ci = significance_ci.get(regime)
        ci_excludes_zero = ci is not None and (ci[0] > 0 or ci[1] < 0)

        per_regime[regime] = {
            "materiality_met": all_met,
            "criteria": criteria_detail,
            "significance_ci": ci,
            "ci_excludes_zero": ci_excludes_zero,
        }

    material_pass_count = sum(
        1 for r in regimes_required if per_regime[r]["materiality_met"]
    )
    materiality_passed = material_pass_count >= min_pass
    significance_passed = any(
        per_regime[r]["ci_excludes_zero"] for r in regimes_required
    )
    deflation_passed = bool(deflation_passed)
    gate_passed = materiality_passed and significance_passed and deflation_passed

    return {
        "target": spec.name,
        "per_regime": per_regime,
        "materiality_passed": materiality_passed,
        "material_pass_count": material_pass_count,
        "significance_passed": significance_passed,
        "deflation_passed": deflation_passed,
        "deflation_detail": deflation_detail,
        "gate_passed": gate_passed,
        "regimes_required": regimes_required,
        "min_pass": min_pass,
    }
