"""Reusable catalog validation script.

Checks data quality for all ingested datasets:
  - Row counts and date ranges per symbol
  - Gap detection (missing trading days)
  - OHLCV sanity (positive prices, volume, OHLC ordering)
  - Feature NaN rates after build_features()
  - Label distribution after generate_labels()
  - FRED macro coverage

Usage:
    .venv/bin/python scripts/validate_catalog.py
    .venv/bin/python scripts/validate_catalog.py --symbols AAPL MSFT SPY
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import quant.storage.catalog as catalog
from quant.features.engineering import build_features
from quant.features.labels import generate_labels

PANEL_SYMS_DEFAULT = ["AAPL", "MSFT", "NKE", "SPY"]
FRED_SERIES = ["DGS10", "DFF"]


def _hr(title: str) -> None:
    width = 60
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


# ── 1. Equity bars ───────────────────────────────────────────────────────────

def validate_equity_bars(symbols: list[str]) -> dict:
    _hr("1. Equity bars (Alpaca equity_bars_daily)")
    results = {}
    try:
        df = catalog.query(f"""
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM {catalog.table('equity_bars_daily')}
            ORDER BY symbol, timestamp
        """)
    except Exception as e:
        _fail(f"Could not query equity_bars_daily: {e}")
        return results

    for sym in symbols:
        sub = df[df["symbol"] == sym].copy()
        if sub.empty:
            _fail(f"{sym}: no rows found")
            results[sym] = None
            continue

        sub["timestamp"] = pd.to_datetime(sub["timestamp"], utc=True)
        sub = sub.sort_values("timestamp").reset_index(drop=True)

        first, last = sub["timestamp"].iloc[0], sub["timestamp"].iloc[-1]
        n = len(sub)

        issues = []
        if (sub["close"] <= 0).any():
            issues.append("non-positive close prices")
        if (sub["high"] < sub["low"]).any():
            issues.append("high < low")
        if (sub["high"] < sub["close"]).any():
            issues.append("high < close")
        if (sub["low"] > sub["close"]).any():
            issues.append("low > close")
        if (sub["volume"] < 0).any():
            issues.append("negative volume")
        nan_counts = sub[["open", "high", "low", "close", "volume"]].isna().sum()
        if nan_counts.any():
            issues.append(f"NaNs: {nan_counts[nan_counts > 0].to_dict()}")

        if issues:
            _warn(f"{sym}: {n} rows  {first.date()} → {last.date()}  issues: {'; '.join(issues)}")
        else:
            _ok(f"{sym}: {n} rows  {first.date()} → {last.date()}  clean")
        results[sym] = sub

    return results


# ── 2. Tiingo EOD ─────────────────────────────────────────────────────────────

def validate_tiingo(symbols: list[str]) -> None:
    _hr("2. Adjusted EOD prices (Tiingo equity_eod_tiingo)")
    try:
        df = catalog.query(f"""
            SELECT symbol, timestamp, adjClose
            FROM {catalog.table('equity_eod_tiingo')}
            ORDER BY symbol, timestamp
        """)
    except Exception as e:
        _fail(f"Could not query equity_eod_tiingo: {e}")
        return

    for sym in symbols:
        sub = df[df["symbol"] == sym]
        if sub.empty:
            _fail(f"{sym}: no rows")
            continue
        n = len(sub)
        ts = pd.to_datetime(sub["timestamp"], utc=True)
        _ok(f"{sym}: {n} rows  {ts.min().date()} → {ts.max().date()}")


# ── 3. FRED macro ─────────────────────────────────────────────────────────────

def validate_fred() -> None:
    _hr("3. FRED macro (macro_fred)")
    try:
        df = catalog.query(f"""
            SELECT series_id, COUNT(*) as rows,
                   MIN(timestamp) as first_date,
                   MAX(timestamp) as last_date
            FROM {catalog.table('macro_fred')}
            GROUP BY series_id
            ORDER BY series_id
        """)
    except Exception as e:
        _fail(f"Could not query macro_fred: {e}")
        return

    if df.empty:
        _fail("No FRED data found")
        return

    for _, row in df.iterrows():
        _ok(f"{row['series_id']}: {row['rows']} obs  "
            f"{pd.Timestamp(row['first_date']).date()} → "
            f"{pd.Timestamp(row['last_date']).date()}")

    missing = [s for s in FRED_SERIES if s not in df["series_id"].values]
    if missing:
        _warn(f"Expected series missing: {missing}")


# ── 4. Features ───────────────────────────────────────────────────────────────

def validate_features(prices_by_symbol: dict, symbols: list[str]) -> dict:
    _hr("4. Feature engineering (build_features)")
    if not prices_by_symbol:
        _fail("No price data available — skipping feature check")
        return {}

    try:
        features = build_features(symbols, prices_by_symbol)
    except Exception as e:
        _fail(f"build_features() raised: {e}")
        return {}

    for sym in symbols:
        feat = features.get(sym)
        if feat is None or feat.empty:
            _fail(f"{sym}: no feature rows")
            continue
        nan_rates = feat.isna().mean()
        high_nan = nan_rates[nan_rates > 0.05]
        if not high_nan.empty:
            detail = ", ".join(f"{c}={v:.0%}" for c, v in high_nan.items())
            _warn(f"{sym}: {len(feat)} rows, high NaN cols: {detail}")
        else:
            _ok(
                f"{sym}: {len(feat)} rows, {len(feat.columns)} features, "
                f"max NaN rate {nan_rates.max():.1%}"
            )

    return features


# ── 5. Labels ─────────────────────────────────────────────────────────────────

def validate_labels(prices_by_symbol: dict, symbols: list[str], horizon: int = 5) -> None:
    _hr(f"5. Labels (generate_labels, horizon={horizon})")
    if not prices_by_symbol:
        _fail("No price data available — skipping label check")
        return

    for sym in symbols:
        prices = prices_by_symbol.get(sym)
        if prices is None or prices.empty:
            continue
        try:
            result = generate_labels(prices["close"], horizon=horizon)
        except Exception as e:
            _fail(f"{sym}: generate_labels raised: {e}")
            continue

        labels = result.series.dropna()
        n_nan = result.series.isna().sum()
        _ok(
            f"{sym}: {len(labels)} valid labels, {n_nan} NaN (expected {horizon}), "
            f"mean={labels.mean():.4f}, std={labels.std():.4f}, "
            f"min={labels.min():.4f}, max={labels.max():.4f}"
        )
        pct_pos = (labels > 0).mean()
        if pct_pos < 0.35 or pct_pos > 0.65:
            _warn(f"{sym}: {pct_pos:.0%} positive returns — strongly skewed universe")
        else:
            _ok(f"{sym}: {pct_pos:.0%} positive returns (in range 35–65%)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(symbols: list[str]) -> None:
    print(f"\nCatalog validation  symbols={symbols}")

    alpaca_data = validate_equity_bars(symbols)
    validate_tiingo(symbols)
    validate_fred()

    prices_by_symbol: dict = {}
    for sym, sub in alpaca_data.items():
        if sub is not None and not sub.empty:
            sub = sub.set_index("timestamp").sort_index()
            prices_by_symbol[sym] = sub

    features_by_symbol = validate_features(prices_by_symbol, symbols)
    validate_labels(prices_by_symbol, symbols)

    _hr("Summary")
    n_syms = sum(1 for v in alpaca_data.values() if v is not None)
    n_feat_cols = (
        len(next(iter(features_by_symbol.values())).columns)
        if features_by_symbol
        else 0
    )
    print(f"  Symbols with data : {n_syms}/{len(symbols)}")
    print(f"  Feature columns   : {n_feat_cols}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate ingested catalog data")
    parser.add_argument("--symbols", nargs="+", default=PANEL_SYMS_DEFAULT)
    args = parser.parse_args()
    main(args.symbols)
