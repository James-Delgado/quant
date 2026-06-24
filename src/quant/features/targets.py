"""Candidate prediction targets for Project B1 (target reframing).

Phase 4A proved next-bar (and 1-day signed) return is structurally unlearnable
from the current public feature set on the Dow-30+ETF sandbox. B1 isolates the
**target** axis: it asks whether a *different prediction object* — drawdown
risk, realized volatility, or a longer directional horizon — is more learnable
from the same information set. This module produces the four pre-committed B1
targets as point-in-time label series, each carrying the horizon constant the
purge/embargo logic consumes (the ``LabelResult`` coupling contract).

The four targets (PRD ``.claude/prds/b1-target-reframing.prd.md`` §Success Metrics):

* **T1 ``drawdown_21d``** — binary classification: ``P(max drawdown > 5% over
  the next 21 bars)``. Gated on ROC-AUC.
* **T2 ``realized_vol_21d``** — regression: log realized volatility over the
  next 21 bars. Gated on MAE.
* **T3 ``directional_5d``** — binary classification: ``sign(ret_5d)``. Gated on
  ROC-AUC **and** the tradeable Sharpe of ``sign(pred)``.
* **T4 ``directional_21d``** — binary classification: ``sign(ret_21d)``. Gated
  on ROC-AUC **and** tradeable Sharpe.

Point-in-time invariant
-----------------------
Every label at bar ``t`` is a function of prices at bars **> t** only (the
forward window). Features never peek into that window; the horizon constant is
handed to ``run_backtest`` via ``LabelResult.horizon_bars`` so purging/embargo
over-purge by the label horizon, never under-purge (``backtest/CLAUDE.md``
invariants 1-4). The new 5- and 21-bar horizons exceed Phase 4A's 1-bar label —
``backtest/CLAUDE.md`` invariant 4 (test-fold length ≫ ``label_horizon`` +
embargo) must be re-checked against the real slice config in B1-M2 rather than
silently shrinking the training set.

Pinned thresholds (METHODOLOGY §1)
----------------------------------
All materiality cut-offs are pinned here as named constants **before any
compute touches B1**. They are reproduced as ``TargetSpec.materiality`` and
consumed verbatim by ``backtest.regime_metrics.b1_gate_report`` (the source of
truth; this module declares the spec, the gate renders the verdict). Changing
any of them after a result is visible invalidates the run and requires a new
ledger entry (METHODOLOGY §1).

Flat-module vs registered-catalog (the B1-M1 design fork)
---------------------------------------------------------
This is a **flat module with an in-code structured registry** (``TARGET_CATALOG``
of frozen ``TargetSpec`` records), deliberately *not* a ``targets.yaml`` +
loader + drift-test in the shape of ``features/catalog.{py,yaml}``. For B1's four
hand-picked, PRD-pinned targets a YAML registry adds no safety (YAGNI), and the
Phase-5 Research agent that would benefit from the catalog form is gated and
unbuilt. The ``TargetSpec`` dataclasses carry the per-target *contract metadata*
a catalog would hold (type, horizon, materiality criteria, baseline, deflation
method) in code, so a future YAML migration — when Phase 5 activates and an agent
needs to add targets safely under a drift test (METHODOLOGY §4, §6) — is
mechanical and reversible. Leave this note for that migration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from quant.features.labels import LabelResult

# ─── pinned constants (METHODOLOGY §1) ───────────────────────────────────────

#: Drawdown event threshold — label is 1 if the worst peak-to-trough decline
#: over the forward window exceeds this fraction (5%).
DRAWDOWN_THRESHOLD: float = 0.05

#: Per-target horizons in trading bars.
DRAWDOWN_HORIZON: int = 21
VOL_HORIZON: int = 21
DIRECTIONAL_HORIZON_SHORT: int = 5
DIRECTIONAL_HORIZON_LONG: int = 21

#: Materiality cut-offs vs the better baseline, per required regime.
DELTA_AUC_MATERIALITY: float = 0.02       # classification: ΔROC-AUC (absolute)
REL_MAE_REDUCTION_MATERIALITY: float = 0.05  # regression: relative MAE reduction
DELTA_SHARPE_MATERIALITY: float = 0.10    # directional Sharpe arm: ΔSharpe


# ─── shared validation prelude ───────────────────────────────────────────────


def _validate_prices(prices: pd.Series, label_name: str) -> None:
    """Validate the price series input — mirrors ``labels.generate_labels``.

    Every target rejects the same contract violations with the same messages so
    a caller cannot feed one target a malformed series another would reject.
    """
    if not isinstance(prices, pd.Series):
        raise TypeError(f"prices must be a pandas Series for {label_name}")
    if prices.empty:
        raise ValueError("prices must not be empty")
    if pd.api.types.is_bool_dtype(prices) or not pd.api.types.is_numeric_dtype(prices):
        raise TypeError(f"prices must have a numeric dtype, got {prices.dtype}")
    if prices.isna().any():
        raise ValueError(
            f"prices contains {prices.isna().sum()} NaN value(s); "
            f"fill or drop before calling {label_name}"
        )
    if (prices == 0.0).any():
        raise ValueError("prices contains zero values; forward return is undefined")
    if (
        isinstance(prices.index, pd.DatetimeIndex)
        and not prices.index.is_monotonic_increasing
    ):
        raise ValueError(
            "prices index must be sorted ascending; call prices.sort_index() first"
        )


# ─── T1: drawdown event ──────────────────────────────────────────────────────


def drawdown_event_labels(
    prices: pd.Series,
    horizon: int = DRAWDOWN_HORIZON,
    dd_threshold: float = DRAWDOWN_THRESHOLD,
) -> LabelResult:
    """Binary: did a drawdown worse than ``dd_threshold`` occur in the next ``horizon`` bars?

    For each bar ``t`` the forward path is ``prices[t .. t+horizon]`` (entry plus
    ``horizon`` forward bars). The label is ``1.0`` if the maximum peak-to-trough
    decline along that path — measured against the running peak from the entry —
    meets or exceeds ``dd_threshold`` in magnitude, else ``0.0``. The last
    ``horizon`` bars cannot form a full forward window and are ``NaN``.

    The running peak includes the entry price, so a path that only ever rises is
    labelled ``0`` (no drawdown). ``horizon_bars`` is set to ``horizon`` — the
    purge horizon the backtester must use.
    """
    if horizon < 1:
        raise ValueError(f"horizon must be >= 1, got {horizon}")
    if not 0.0 < dd_threshold < 1.0:
        raise ValueError(f"dd_threshold must be in (0, 1), got {dd_threshold}")
    _validate_prices(prices, "drawdown_event_labels")
    if horizon >= len(prices):
        raise ValueError(
            f"horizon ({horizon}) must be < len(prices) ({len(prices)}); "
            "all labels would be NaN"
        )

    n = len(prices)
    price_arr = prices.to_numpy()
    labels = np.full(n, np.nan, dtype=float)

    for t in range(n - horizon):
        window = price_arr[t : t + horizon + 1]
        running_peak = np.maximum.accumulate(window)
        drawdowns = window / running_peak - 1.0
        max_dd = drawdowns.min()  # most negative
        labels[t] = 1.0 if max_dd <= -dd_threshold else 0.0

    series = pd.Series(labels, index=prices.index, name=f"drawdown_event_{horizon}b")
    return LabelResult(series=series, horizon_bars=horizon)


# ─── T2: realized volatility ──────────────────────────────────────────────────


def realized_vol_labels(
    prices: pd.Series,
    horizon: int = VOL_HORIZON,
) -> LabelResult:
    """Regression: log realized volatility of one-bar returns over the next ``horizon`` bars.

    At bar ``t`` the label is ``log(std(r_{t+1} .. r_{t+horizon}))`` where
    ``r_s = prices[s]/prices[s-1] - 1`` and the standard deviation uses
    ``ddof=1``. Log-vol is variance-stabilising and the standard regression
    target in the volatility-forecasting literature (PRD §Open Questions pins
    log-vol over raw vol). The window is non-annualised — annualisation is a
    constant additive shift in log-space and so does not affect MAE deltas vs a
    baseline. The last ``horizon`` bars are ``NaN``.

    A forward window of constant prices has zero realised vol and an undefined
    log; this raises ``ValueError`` (mirroring ``label_schemes.vol_scaled_returns``)
    rather than emitting ``-inf``.
    """
    if horizon < 2:
        raise ValueError(f"horizon must be >= 2 for a realised-vol std, got {horizon}")
    _validate_prices(prices, "realized_vol_labels")
    if horizon >= len(prices):
        raise ValueError(
            f"horizon ({horizon}) must be < len(prices) ({len(prices)}); "
            "all labels would be NaN"
        )

    returns = prices.pct_change()
    # rolling(horizon).std() at bar s = std(r_{s-horizon+1} .. r_s); shift(-horizon)
    # moves the value computed at t+horizon back to t = std(r_{t+1} .. r_{t+horizon}).
    fwd_vol = returns.rolling(window=horizon, min_periods=horizon).std(ddof=1).shift(
        -horizon
    )

    valid = fwd_vol.notna()
    if (fwd_vol[valid] == 0.0).any():
        raise ValueError(
            "a forward window has zero realised vol (constant prices); "
            "log realised vol is undefined"
        )

    log_vol = np.log(fwd_vol)
    log_vol.name = f"log_realized_vol_{horizon}b"
    return LabelResult(series=log_vol, horizon_bars=horizon)


# ─── T3 / T4: directional ─────────────────────────────────────────────────────


def directional_labels(prices: pd.Series, horizon: int) -> LabelResult:
    """Binary up/down: ``1.0`` if the ``horizon``-bar forward return is positive, else ``0.0``.

    Used for both T3 (``horizon=5``) and T4 (``horizon=21``). The classification
    label is binary (up=1, down-or-flat=0) so it is ROC-AUC-scorable; the
    tradeable Sharpe arm in B1-M2 routes a model's ``sign(pred)`` through the
    existing simulator separately (it is not built here). The last ``horizon``
    bars are ``NaN``.
    """
    if horizon < 1:
        raise ValueError(f"horizon must be >= 1, got {horizon}")
    _validate_prices(prices, "directional_labels")
    if horizon >= len(prices):
        raise ValueError(
            f"horizon ({horizon}) must be < len(prices) ({len(prices)}); "
            "all labels would be NaN"
        )

    forward_return = prices.shift(-horizon) / prices - 1.0
    labels = forward_return.where(
        forward_return.isna(), (forward_return > 0).astype(float)
    )
    labels.name = f"directional_{horizon}b"
    return LabelResult(series=labels, horizon_bars=horizon)


# ─── target catalog (in-code registry) ────────────────────────────────────────


@dataclass(frozen=True)
class MaterialityCriterion:
    """One materiality condition on a metric delta vs the better baseline.

    * ``kind="delta_higher"`` — passes when ``variant - baseline >= threshold``
      (ROC-AUC, Sharpe: higher is better).
    * ``kind="rel_reduction"`` — passes when ``(baseline - variant) / baseline
      >= threshold`` (MAE: lower is better, measured as a relative reduction).
    """

    metric: str
    kind: Literal["delta_higher", "rel_reduction"]
    threshold: float


TargetType = Literal["classification", "regression", "directional"]


@dataclass(frozen=True)
class TargetSpec:
    """Pre-committed contract for one B1 candidate target.

    Carries every field a future ``targets.yaml`` catalog entry would hold, so
    the flat→catalog migration (Phase 5) is mechanical. ``materiality`` is a
    tuple of criteria **all** of which must hold in a regime for that regime to
    count toward the gate's materiality stage (directional targets carry both an
    AUC and a Sharpe criterion).
    """

    name: str
    target_type: TargetType
    description: str
    horizon_bars: int
    primary_metric: str
    materiality: tuple[MaterialityCriterion, ...]
    baseline_desc: str
    deflation: Literal["dsr", "skill_z"]
    notes: str = ""


#: The four pre-committed B1 targets. Keys are the canonical target ids.
#:
#: Deflation method (stage 3 of the gate): ``"dsr"`` for the directional Sharpe
#: arms (a return series exists → Bailey-López de Prado DSR), ``"skill_z"`` for
#: the drawdown classifier and the vol regression (no tradeable return series →
#: a forecast-skill z-score analog). NOTE: the PRD's T1 row literally says
#: "DSR > 0"; a probability target has no return series, so T1 uses the skill-z
#: analog the PRD prose grants vol (T2). Flagged as a B1-M1 finding to confirm
#: in B1-M2 — see post-task review.
TARGET_CATALOG: dict[str, TargetSpec] = {
    "drawdown_21d": TargetSpec(
        name="drawdown_21d",
        target_type="classification",
        description="P(max drawdown > 5% over next 21 bars)",
        horizon_bars=DRAWDOWN_HORIZON,
        primary_metric="auc",
        materiality=(
            MaterialityCriterion("auc", "delta_higher", DELTA_AUC_MATERIALITY),
        ),
        baseline_desc="climatology base-rate predictor + ARIMA-vol-implied DD probability (better of)",
        deflation="skill_z",
        notes="Imbalanced label; report base rate per regime (PRD Open Question).",
    ),
    "realized_vol_21d": TargetSpec(
        name="realized_vol_21d",
        target_type="regression",
        description="log realized volatility over next 21 bars",
        horizon_bars=VOL_HORIZON,
        primary_metric="mae",
        materiality=(
            MaterialityCriterion("mae", "rel_reduction", REL_MAE_REDUCTION_MATERIALITY),
        ),
        baseline_desc="EWMA(lambda=0.94) vol forecast + ARIMA-on-log-vol (better of)",
        deflation="skill_z",
        notes="Feeds C4 (confidence) / C3 (vol-targeted sizing), not a direction.",
    ),
    "directional_5d": TargetSpec(
        name="directional_5d",
        target_type="directional",
        description="sign(ret_5d) up/down",
        horizon_bars=DIRECTIONAL_HORIZON_SHORT,
        primary_metric="auc",
        materiality=(
            MaterialityCriterion("auc", "delta_higher", DELTA_AUC_MATERIALITY),
            MaterialityCriterion("sharpe", "delta_higher", DELTA_SHARPE_MATERIALITY),
        ),
        baseline_desc="majority-class + ARIMA(1,0,0) sign (better of for AUC; ARIMA for Sharpe)",
        deflation="dsr",
        notes="Must clear BOTH AUC and Sharpe to count as an edge.",
    ),
    "directional_21d": TargetSpec(
        name="directional_21d",
        target_type="directional",
        description="sign(ret_21d) up/down",
        horizon_bars=DIRECTIONAL_HORIZON_LONG,
        primary_metric="auc",
        materiality=(
            MaterialityCriterion("auc", "delta_higher", DELTA_AUC_MATERIALITY),
            MaterialityCriterion("sharpe", "delta_higher", DELTA_SHARPE_MATERIALITY),
        ),
        baseline_desc="majority-class + ARIMA(1,0,0) 21-bar sign (better of for AUC; ARIMA for Sharpe)",
        deflation="dsr",
        notes="Must clear BOTH AUC and Sharpe to count as an edge.",
    ),
}


def make_target_labels(target_id: str, prices: pd.Series) -> LabelResult:
    """Dispatch ``target_id`` to its label function with the pinned horizon.

    The single entry point B1-M2 calls so the ablation matrix never re-specifies
    a horizon and risks drifting it away from ``TargetSpec.horizon_bars`` (the
    horizon-coupling invariant in ``backtest/CLAUDE.md``).
    """
    if target_id not in TARGET_CATALOG:
        raise KeyError(
            f"unknown target id {target_id!r}; known: {sorted(TARGET_CATALOG)}"
        )
    spec = TARGET_CATALOG[target_id]
    if target_id == "drawdown_21d":
        return drawdown_event_labels(prices, horizon=spec.horizon_bars)
    if target_id == "realized_vol_21d":
        return realized_vol_labels(prices, horizon=spec.horizon_bars)
    # directional_5d / directional_21d
    return directional_labels(prices, horizon=spec.horizon_bars)
