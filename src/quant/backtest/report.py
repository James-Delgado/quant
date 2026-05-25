"""Text and DataFrame report generation for BacktestResult."""
from __future__ import annotations

import io
import math

import pandas as pd

from quant.backtest.harness import BacktestResult


def _fmt(val: float | None, spec: str) -> str:
    """Format val with spec; return '—' for None or NaN."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "—"
    return format(val, spec)


def summary_table(result: BacktestResult) -> pd.DataFrame:
    """Return OOS vs IS metrics as a side-by-side DataFrame."""
    oos = result.oos_metrics
    is_ = result.is_metrics
    keys = list(oos.keys())
    return pd.DataFrame(
        {"OOS": [oos[k] for k in keys], "IS": [is_.get(k, float("nan")) for k in keys]},
        index=keys,
    )


def print_report(result: BacktestResult) -> None:
    """Print a human-readable backtest summary to stdout."""
    print(format_report(result))


def format_report(result: BacktestResult) -> str:
    """Return the backtest summary as a string."""
    buf = io.StringIO()
    _write_report(result, buf)
    return buf.getvalue()


def _write_report(result: BacktestResult, buf: io.StringIO) -> None:
    oos = result.oos_metrics
    is_ = result.is_metrics

    buf.write("=" * 52 + "\n")
    buf.write(f"{'Metric':<22} {'OOS':>12} {'IS':>12}\n")
    buf.write("-" * 52 + "\n")

    fmt = {
        "sharpe": ".3f",
        "sortino": ".3f",
        "calmar": ".3f",
        "max_drawdown": ".2%",
        "total_return": ".2%",
        "annualized_return": ".2%",
        "hit_rate": ".2%",
        "profit_factor": ".3f",
    }

    for key, spec in fmt.items():
        oos_str = _fmt(oos.get(key), spec)
        is_str = _fmt(is_.get(key), spec)
        buf.write(f"{key:<22} {oos_str:>12} {is_str:>12}\n")

    buf.write("=" * 52 + "\n")

    n_trades = len(result.trade_log)
    n_folds = len(result.fold_metrics)
    buf.write(f"Trades: {n_trades}   Folds: {n_folds}\n")

    if n_trades > 0 and len(result.equity_curve) > 0:
        start = result.equity_curve.index[0].date()
        end = result.equity_curve.index[-1].date()
        buf.write(f"Period: {start} → {end}\n")
