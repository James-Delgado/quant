"""Text and DataFrame report generation for BacktestResult."""
from __future__ import annotations

import io
import math

import pandas as pd

from quant.backtest.harness import BacktestResult
from quant.backtest.regime_metrics import compute_regime_metrics


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


# ─── Regime-conditional reporting (Phase 4A Milestone 1) ─────────────────────


_REGIME_TABLE_COLUMNS = ("sharpe", "sortino", "max_drawdown", "n_bars")


def regime_summary_table(
    result: BacktestResult,
    regime_labels: pd.Series,
) -> pd.DataFrame:
    """One row per regime, columns ``sharpe``, ``sortino``, ``max_drawdown``, ``n_bars``.

    The result must have ``oos_returns`` populated (default since Phase 4A).
    Regimes with zero observations on the OOS index are omitted.
    """
    per_regime = compute_regime_metrics(result.oos_returns, regime_labels)
    rows = {
        regime: {
            "sharpe": metrics["sharpe"],
            "sortino": metrics["sortino"],
            "max_drawdown": metrics["max_drawdown"],
            "n_bars": int((regime_labels == regime).sum()),
        }
        for regime, metrics in per_regime.items()
    }
    return pd.DataFrame.from_dict(rows, orient="index", columns=list(_REGIME_TABLE_COLUMNS))


def format_regime_report(
    result: BacktestResult,
    regime_labels: pd.Series,
) -> str:
    """Per-regime summary in the same 52-column layout as ``format_report``."""
    tbl = regime_summary_table(result, regime_labels)
    buf = io.StringIO()
    buf.write("=" * 52 + "\n")
    buf.write(
        f"{'Regime':<14} {'Sharpe':>10} {'Sortino':>10} "
        f"{'MaxDD':>8} {'Bars':>6}\n"
    )
    buf.write("-" * 52 + "\n")
    for regime in tbl.index:
        sharpe = _fmt(tbl.loc[regime, "sharpe"], ".3f")
        sortino = _fmt(tbl.loc[regime, "sortino"], ".3f")
        max_dd = _fmt(tbl.loc[regime, "max_drawdown"], ".2%")
        n_bars = int(tbl.loc[regime, "n_bars"])
        buf.write(f"{str(regime):<14} {sharpe:>10} {sortino:>10} {max_dd:>8} {n_bars:>6}\n")
    buf.write("=" * 52 + "\n")
    return buf.getvalue()


def print_regime_report(
    result: BacktestResult,
    regime_labels: pd.Series,
) -> None:
    """Print ``format_regime_report`` to stdout."""
    print(format_regime_report(result, regime_labels))
