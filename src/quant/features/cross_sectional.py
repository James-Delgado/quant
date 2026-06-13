"""Cross-sectional rank features (Phase 4A Milestone 3).

For each pre-committed source column, every symbol receives its percentile
rank (0–1] across the universe symbols that have data on that date —
"where does this symbol sit relative to the panel today". Ranks are
NaN-aware: a symbol without a value at date t is excluded from that date's
rank pool, and dates whose pool is smaller than ``min_symbols`` are set to
NaN wholesale (a rank over two symbols is noise; slice notebooks run
5-symbol panels).

Leakage: none possible by construction. Each rank at bar t uses ONLY the
same-date values of point-in-time features already produced by
``build_features`` — no temporal aggregation, no forward information.

Residual caveat — survivorship: the universe whose cross-section is ranked
(DJIA 30 + ETFs, chosen in Phase 2.5) was selected with hindsight of which
constituents survived to selection time. The rank features inherit that
universe-membership bias; they do not add to it.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

_DEFAULT_RANK_COLUMNS: tuple[str, ...] = ("ret_21d", "ret_252d", "vol_21d")


def add_cross_sectional_features(
    features_by_symbol: dict[str, pd.DataFrame],
    columns: Sequence[str] = _DEFAULT_RANK_COLUMNS,
    min_symbols: int = 5,
) -> dict[str, pd.DataFrame]:
    """Append ``xs_rank_<col>`` percentile-rank columns to each symbol's frame.

    Parameters
    ----------
    features_by_symbol: {symbol: feature DataFrame} as produced by
                        ``build_features`` — DatetimeIndex, one row per bar.
    columns:            Source columns to rank cross-sectionally. Every
                        symbol's frame must contain all of them.
    min_symbols:        Minimum number of symbols with data at a date for
                        the rank to be meaningful; thinner dates get NaN.

    Returns
    -------
    New ``{symbol: DataFrame}`` — input frames are not mutated. Each output
    frame keeps its own index and all original columns, plus one
    ``xs_rank_<col>`` column per source column.
    """
    if not features_by_symbol:
        raise ValueError("features_by_symbol must not be empty")
    for col in columns:
        missing = [
            sym for sym, df in features_by_symbol.items() if col not in df.columns
        ]
        if missing:
            raise ValueError(
                f"source column {col!r} missing from symbols: {missing}"
            )

    result = {sym: df.copy() for sym, df in features_by_symbol.items()}
    for col in columns:
        wide = pd.DataFrame({sym: df[col] for sym, df in features_by_symbol.items()})
        ranks = wide.rank(axis=1, pct=True)
        thin = wide.notna().sum(axis=1) < min_symbols
        ranks.loc[thin] = np.nan
        for sym in result:
            result[sym][f"xs_rank_{col}"] = ranks[sym].reindex(result[sym].index)
    return result
