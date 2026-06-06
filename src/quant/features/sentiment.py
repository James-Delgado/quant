"""Sentiment feature aggregation with point-in-time correctness.

Documents are scored upstream by FinBERT (features/finbert.py), producing
a sentiment_scored/ dataset. This module aggregates those scores into
per-symbol, per-day features consumed by build_features().

Key invariant: validate_point_in_time() is called for every non-empty
document window inside aggregate_sentiment() — look-ahead cannot slip
through for any bar with coverage.

Missing value policy (per Phase 3 eng review D5):
- Days with no documents: sentiment_score=0.0, doc_count=0, has_coverage=False
- The GBM can learn from doc_count=0 as a distinct regime signal.
- Zero-fill never forward-fills stale sentiment — each day is independent.
"""
from __future__ import annotations

import pandas as pd


def validate_point_in_time(
    docs_df: pd.DataFrame,
    bar_date_col: str = "bar_date",
    published_at_col: str = "published_at_check",
) -> None:
    """Assert no document is used on or after its publication date.

    Raises ValueError on any violation (published_at >= bar_date).
    This is the modular audit gate for all text sources — EDGAR, RSS,
    and any future source (GDELT, Bloomberg) pass through here.

    Parameters
    ----------
    docs_df:          DataFrame with published_at and bar_date columns.
    bar_date_col:     Column name for the bar date being served.
    published_at_col: Column name for document publication timestamp.
    """
    if docs_df.empty:
        return

    pub = pd.to_datetime(docs_df[published_at_col])
    bar = pd.to_datetime(docs_df[bar_date_col])

    if pub.dt.tz is not None:
        pub = pub.dt.tz_convert(None)
    if bar.dt.tz is not None:
        bar = bar.dt.tz_convert(None)

    mask = pub >= bar
    if mask.any():
        first = docs_df[mask].iloc[0]
        raise ValueError(
            f"Point-in-time violation: {mask.sum()} document(s) have "
            f"published_at >= bar_date. "
            f"First: published_at={first[published_at_col]}, "
            f"bar_date={first[bar_date_col]}"
        )


def aggregate_sentiment(
    symbol: str,
    bar_dates: pd.DatetimeIndex,
    scored_df: pd.DataFrame,
    lookback_days: int = 30,
) -> pd.DataFrame:
    """Aggregate FinBERT scores into per-bar features for one symbol.

    For each bar date d, uses documents where:
        published_at < d              (strict — same-day docs excluded)
        published_at >= d − lookback  (only recent coverage counts)

    validate_point_in_time() fires before any aggregation — look-ahead
    cannot slip through silently.

    Parameters
    ----------
    symbol:        Ticker to aggregate.
    bar_dates:     DatetimeIndex of trading bar dates (tz-aware UTC).
    scored_df:     DataFrame with columns:
                     symbol, published_at (tz-aware UTC), sentiment_score
    lookback_days: Rolling window in calendar days.

    Returns
    -------
    DataFrame indexed by bar_dates with columns:
        sentiment_score  float  mean net sentiment in window (0.0 if empty)
        doc_count        int    documents in window
        has_coverage     bool   True if doc_count > 0
    """
    sym_docs = scored_df[scored_df["symbol"] == symbol].copy()

    if sym_docs.empty:
        return pd.DataFrame(
            {"sentiment_score": 0.0, "doc_count": 0, "has_coverage": False},
            index=bar_dates,
        )

    pub = pd.to_datetime(sym_docs["published_at"])
    if pub.dt.tz is not None:
        pub = pub.dt.tz_convert(None)
    sym_docs["_pub_naive"] = pub.values

    lookback_delta = pd.Timedelta(days=lookback_days)
    records: list[dict] = []

    for bar in bar_dates:
        bar_naive = bar.tz_convert(None) if bar.tzinfo is not None else bar
        cutoff = bar_naive - lookback_delta

        window = sym_docs[
            (sym_docs["_pub_naive"] < bar_naive)
            & (sym_docs["_pub_naive"] >= cutoff)
        ]

        if not window.empty:
            audit = window.copy()
            audit["bar_date"] = bar_naive
            audit["published_at_check"] = audit["_pub_naive"]
            validate_point_in_time(audit)

        count = len(window)
        score = float(window["sentiment_score"].mean()) if count > 0 else 0.0

        records.append({
            "sentiment_score": score,
            "doc_count": count,
            "has_coverage": count > 0,
        })

    return pd.DataFrame(records, index=bar_dates)
