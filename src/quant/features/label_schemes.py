"""Additional label-generation schemes for the Phase 4A ablation matrix.

The ``LabelResult`` NamedTuple from ``labels.py`` is reused here so the
caller cannot drift the purge horizon away from the label horizon.

Schemes
-------
* ``vol_scaled_returns(prices, horizon, vol_window)`` — forward return scaled
  by point-in-time realised vol. Standardises the training signal across
  vol regimes so the GBM is not implicitly weighted toward crisis bars.

* ``triple_barrier_labels(prices, config)`` — López de Prado AFML §3.5 method.
  First-hit between an upper PT barrier, a lower SL barrier (both scaled by
  σ̂), and a time-out at ``config.max_horizon``. Encodes the discipline
  "trade if I expect ≥ pt_sigma·σ̂ upside and can survive sl_sigma·σ̂ adverse
  motion" — exactly the convexity the signed-return GBM lacks.

Point-in-time invariant
-----------------------
The vol denominator at bar t uses **only** returns at bars ≤ t. The forward
window is consumed by the label numerator; the σ̂ estimate must never peek
into it.

Parameter rationale (LDP_DEFAULT)
---------------------------------
See ``docs/concepts/label-schemes.md`` for citations. Defaults are
pre-committed: do not retune to make a scheme pass the gate.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant.features.labels import LabelResult


# ─── shared validation prelude ───────────────────────────────────────────────


def _validate_prices(prices: pd.Series, label_name: str) -> None:
    """Validate the price series input shape and contents.

    Mirrors ``generate_labels`` validation so every scheme rejects the same
    contract violations with the same error messages.
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


# ─── vol_scaled_returns ──────────────────────────────────────────────────────


def vol_scaled_returns(
    prices: pd.Series,
    horizon: int,
    vol_window: int = 21,
) -> LabelResult:
    """Forward return over ``horizon`` bars divided by trailing realised vol.

    The denominator is the rolling standard deviation of one-bar pct returns
    over the most recent ``vol_window`` returns ending at bar t (no look-ahead
    into the forward window). The numerator is the same forward return as
    ``generate_labels`` so the scheme is a strict re-scaling of the signed
    target.
    """
    if horizon < 1:
        raise ValueError(f"horizon must be >= 1, got {horizon}")
    if vol_window < 2:
        raise ValueError(f"vol_window must be >= 2, got {vol_window}")
    _validate_prices(prices, "vol_scaled_returns")
    if horizon >= len(prices):
        raise ValueError(
            f"horizon ({horizon}) must be < len(prices) ({len(prices)}); "
            "all labels would be NaN"
        )

    forward_return = prices.shift(-horizon) / prices - 1.0
    returns = prices.pct_change()
    vol = returns.rolling(window=vol_window, min_periods=vol_window).std()

    if (vol == 0.0).any():
        raise ValueError(
            "rolling-vol window contains zero realised vol; "
            "scaled return undefined"
        )

    scaled = forward_return / vol
    scaled.name = f"vol_scaled_fwd_return_{horizon}b"
    return LabelResult(series=scaled, horizon_bars=horizon)


# ─── triple_barrier_labels ──────────────────────────────────────────────────


@dataclass(frozen=True)
class TripleBarrierConfig:
    """Parameters for ``triple_barrier_labels``.

    Defaults (in ``LDP_DEFAULT``) are pre-committed; see
    ``docs/concepts/label-schemes.md`` for rationale and citations.
    """

    pt_sigma: float = 2.0
    sl_sigma: float = 1.0
    vol_window: int = 21
    max_horizon: int = 5


LDP_DEFAULT = TripleBarrierConfig()


def triple_barrier_labels(
    prices: pd.Series,
    config: TripleBarrierConfig = LDP_DEFAULT,
) -> LabelResult:
    """Compute López de Prado triple-barrier labels.

    For each bar t with a valid σ̂[t] (vol over returns at bars
    t-vol_window+1 .. t) and a forward window of ``max_horizon`` bars:

    * ``pt = prices[t] * (1 + pt_sigma * σ̂[t])`` — upper (profit-take) barrier
    * ``sl = prices[t] * (1 - sl_sigma * σ̂[t])`` — lower (stop-loss) barrier
    * Walk forward up to ``max_horizon`` bars; first hit wins.

    ``horizon_bars`` is set to ``config.max_horizon`` — the *conservative*
    purge horizon. Actual label fills may be earlier, but the purging logic
    over-purges by the worst case rather than under-purging.
    """
    if config.max_horizon < 1:
        raise ValueError(f"max_horizon must be >= 1, got {config.max_horizon}")
    if config.vol_window < 2:
        raise ValueError(f"vol_window must be >= 2, got {config.vol_window}")
    if config.pt_sigma <= 0:
        raise ValueError(f"pt_sigma must be > 0, got {config.pt_sigma}")
    if config.sl_sigma <= 0:
        raise ValueError(f"sl_sigma must be > 0, got {config.sl_sigma}")
    _validate_prices(prices, "triple_barrier_labels")
    if config.max_horizon >= len(prices):
        raise ValueError(
            f"max_horizon ({config.max_horizon}) must be < len(prices) "
            f"({len(prices)}); all labels would be NaN"
        )

    n = len(prices)
    price_arr = prices.to_numpy()
    returns = prices.pct_change()
    vol = returns.rolling(window=config.vol_window, min_periods=config.vol_window).std()
    vol_arr = vol.to_numpy()

    labels_arr = np.full(n, np.nan, dtype=float)

    for t in range(n - config.max_horizon):
        sigma = vol_arr[t]
        if np.isnan(sigma) or sigma == 0.0:
            continue
        p_t = price_arr[t]
        pt_barrier = p_t * (1.0 + config.pt_sigma * sigma)
        sl_barrier = p_t * (1.0 - config.sl_sigma * sigma)

        label = 0
        for k in range(1, config.max_horizon + 1):
            p_future = price_arr[t + k]
            hit_pt = p_future >= pt_barrier
            hit_sl = p_future <= sl_barrier
            if hit_pt and hit_sl:
                # Same-bar two-sided hit; intraday order unknown.
                # Treat as neutral rather than fabricate an outcome.
                label = 0
                break
            if hit_pt:
                label = 1
                break
            if hit_sl:
                label = -1
                break
        labels_arr[t] = label

    series = pd.Series(
        labels_arr,
        index=prices.index,
        name=f"triple_barrier_{config.max_horizon}b",
    )
    return LabelResult(series=series, horizon_bars=config.max_horizon)
