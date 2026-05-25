"""Label generation for predictive modeling.

The LabelResult namedtuple couples the label series to its horizon so the
caller cannot pass the wrong horizon_bars to run_backtest(). Always derive
label_horizon from LabelResult.horizon_bars — do not set it separately.
"""
from __future__ import annotations

from typing import NamedTuple

import pandas as pd


class LabelResult(NamedTuple):
    """Container that keeps a label series and its horizon inseparable."""

    series: pd.Series
    horizon_bars: int


def generate_labels(prices: pd.Series, horizon: int) -> LabelResult:
    """Compute per-bar forward returns over *horizon* bars.

    Args:
        prices: Closing price series, sorted ascending by date.
        horizon: Number of bars forward. Must be >= 1.

    Returns:
        LabelResult with the forward-return series (NaN for the last
        *horizon* bars) and the horizon used, so callers can pass
        result.horizon_bars directly to run_backtest().
    """
    if horizon < 1:
        raise ValueError(f"horizon must be >= 1, got {horizon}")
    if not isinstance(prices, pd.Series):
        raise TypeError("prices must be a pandas Series")
    if prices.empty:
        raise ValueError("prices must not be empty")
    if pd.api.types.is_bool_dtype(prices) or not pd.api.types.is_numeric_dtype(prices):
        raise TypeError(f"prices must have a numeric dtype, got {prices.dtype}")
    if prices.isna().any():
        raise ValueError(
            f"prices contains {prices.isna().sum()} NaN value(s); "
            "fill or drop before calling generate_labels"
        )
    if (prices == 0.0).any():
        raise ValueError("prices contains zero values; forward return is undefined")
    if horizon >= len(prices):
        raise ValueError(
            f"horizon ({horizon}) must be < len(prices) ({len(prices)}); "
            "all labels would be NaN"
        )
    if isinstance(prices.index, pd.DatetimeIndex) and not prices.index.is_monotonic_increasing:
        raise ValueError(
            "prices index must be sorted ascending; call prices.sort_index() first"
        )

    forward_return = prices.shift(-horizon) / prices - 1.0
    return LabelResult(series=forward_return, horizon_bars=horizon)
