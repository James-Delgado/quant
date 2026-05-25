"""Performance metrics for evaluated strategies.

All functions operate on plain pandas Series / DataFrames so they can be
called independently of the harness (useful for notebooks and ad-hoc analysis).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


_TRADING_DAYS = 252


def compute_metrics(
    returns: pd.Series,
    trade_log: pd.DataFrame | None = None,
    trading_days_per_year: int = _TRADING_DAYS,
) -> dict[str, float]:
    """Compute a standard set of strategy performance metrics.

    Parameters
    ----------
    returns:               Daily arithmetic portfolio returns (pct_change output).
    trade_log:             If provided, also computes hit_rate and profit_factor.
    trading_days_per_year: Annualisation factor (252 for equity).

    Returns
    -------
    dict with keys: sharpe, sortino, calmar, max_drawdown, total_return,
                    annualized_return, and (if trade_log) hit_rate, profit_factor.
    """
    r = returns.dropna()
    ann = float(trading_days_per_year)

    # ── Return metrics ─────────────────────────────────────────────────────
    total_return = float(np.prod(1.0 + r.to_numpy()) - 1.0) if len(r) > 0 else 0.0

    n = len(r)
    equity_end = 1.0 + total_return
    if n > 0 and equity_end > 0:
        annualized_return = float(equity_end ** (ann / n) - 1.0)
    elif n > 0:
        annualized_return = -1.0   # total ruin — can't annualise through zero
    else:
        annualized_return = 0.0

    # ── Sharpe ────────────────────────────────────────────────────────────
    mean = float(r.mean()) if n > 0 else 0.0
    std = float(r.std(ddof=1)) if n > 1 else 0.0
    sharpe = (mean / std) * math.sqrt(ann) if std > 0 else 0.0

    # ── Sortino (downside deviation, target = 0) ───────────────────────────
    downside = r[r < 0]
    downside_std = float(np.sqrt((downside ** 2).mean())) if len(downside) > 0 else 0.0
    sortino = (mean / downside_std) * math.sqrt(ann) if downside_std > 0 else 0.0

    # ── Max drawdown ──────────────────────────────────────────────────────
    equity = np.cumprod(1.0 + r.to_numpy()) if n > 0 else np.array([1.0])
    running_peak = np.maximum.accumulate(equity)
    drawdowns = (equity - running_peak) / running_peak
    max_drawdown = float(drawdowns.min()) if len(drawdowns) > 0 else 0.0

    # ── Calmar (annualised return / |max drawdown|) ───────────────────────
    calmar = (
        annualized_return / abs(max_drawdown)
        if max_drawdown < 0 else float("inf")
    )

    metrics: dict[str, float] = {
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": max_drawdown,
        "total_return": total_return,
        "annualized_return": annualized_return,
    }

    # ── Trade-level metrics (require trade log) ────────────────────────────
    if trade_log is not None and len(trade_log) > 0 and "net_pnl" in trade_log.columns:
        pnl = trade_log["net_pnl"].to_numpy(dtype=float)
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]

        hit_rate = float(len(wins) / len(pnl)) if len(pnl) > 0 else 0.0
        profit_factor = (
            float(wins.sum() / abs(losses.sum()))
            if len(losses) > 0 and losses.sum() != 0
            else float("inf")
        )
        metrics["hit_rate"] = hit_rate
        metrics["profit_factor"] = profit_factor

    return metrics
