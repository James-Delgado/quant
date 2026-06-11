"""Text and DataFrame report generation for BacktestResult."""
from __future__ import annotations

import io
import math

import pandas as pd

from quant.backtest.harness import BacktestResult
from quant.backtest.regime_metrics import (
    MIN_DM_OBS,
    compute_regime_metrics,
)
from quant.backtest.statistics import diebold_mariano


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


# ─── Ablation reporting (Phase 4A Milestone 2) ──────────────────────────────


def _scheme_per_regime_sharpe(
    result: BacktestResult,
    regime_labels: pd.Series,
) -> dict[str, float]:
    """Sharpe per regime + aggregate for a single scheme's result.

    Returns a flat dict keyed by regime name plus ``"aggregate"``. Regimes
    with zero observations on the OOS index map to NaN — Borda ranking
    handles NaNs at the rank step.
    """
    sharpes: dict[str, float] = {
        "aggregate": float(result.oos_metrics.get("sharpe", float("nan")))
    }
    # regime_labels may index a superset of result.oos_returns (e.g. tagged
    # over a wider master timeline). Slice to the result's OOS index first.
    common_idx = result.oos_returns.index.intersection(regime_labels.index)
    if len(common_idx) == 0:
        for regime in regime_labels.unique():
            sharpes[str(regime)] = float("nan")
        return sharpes
    per = compute_regime_metrics(
        result.oos_returns.loc[common_idx],
        regime_labels.loc[common_idx],
    )
    for regime in regime_labels.unique():
        sharpes[str(regime)] = float(per.get(regime, {}).get("sharpe", float("nan")))
    return sharpes


def ablation_summary_table(
    results: dict[str, BacktestResult],
    regime_labels: pd.Series,
) -> pd.DataFrame:
    """One row per scheme, columns = ``aggregate`` + each regime, cells = OOS Sharpe.

    Parameters
    ----------
    results:
        ``{scheme_name: BacktestResult}`` from ``run_label_ablation``.
        Each result must have ``oos_returns`` populated.
    regime_labels:
        Per-bar regime labels covering the union of all results' OOS indices.
    """
    if not results:
        raise ValueError("ablation_summary_table needs at least one result")

    rows: dict[str, dict[str, float]] = {}
    for name, result in results.items():
        rows[name] = _scheme_per_regime_sharpe(result, regime_labels)

    cols = ["aggregate"] + [str(r) for r in regime_labels.unique()]
    return pd.DataFrame.from_dict(rows, orient="index", columns=cols)


def ablation_composite_ranking(
    results: dict[str, BacktestResult],
    regime_labels: pd.Series,
) -> pd.DataFrame:
    """Balanced multi-regime composite ranking via Borda count.

    For each column in the ablation summary table (``aggregate`` plus every
    regime), rank schemes 1 → N where 1 = highest Sharpe. The composite
    rank is the mean of those per-column ranks (no regime gets special
    weighting). Lower composite is better.

    Returns a DataFrame with columns ``composite_rank``,
    ``mean_rank_across_regimes``, plus the per-regime ranks for
    transparency. Ties on per-regime rank are broken by ``method='min'``
    (the standard "competition ranking" convention); ties on composite
    rank fall through to ``method='min'`` again so the output is
    deterministic regardless of dict order.
    """
    summary = ablation_summary_table(results, regime_labels)
    # rank(ascending=False) → 1 = highest Sharpe. NaNs are placed last
    # (worst rank) so a scheme with no coverage in a regime is penalised
    # rather than silently dropped.
    ranks = summary.rank(ascending=False, method="min", na_option="bottom")
    mean_rank = ranks.mean(axis=1)
    composite = mean_rank.rank(method="min").astype(int)

    out = ranks.copy()
    out["mean_rank_across_regimes"] = mean_rank
    out["composite_rank"] = composite
    return out.sort_values("composite_rank")


def ablation_dm_matrix(
    results: dict[str, BacktestResult],
    regime_labels: pd.Series,
) -> pd.DataFrame:
    """Pairwise DM p-values per regime for every unordered scheme pair.

    Rows = ``(scheme_a, scheme_b)`` pairs; columns = each regime.
    A cell is the DM p-value testing whether scheme_a's forecast errors are
    smaller than scheme_b's (``alternative="less"``) on bars in that regime.
    Missing forecast errors → NaN cell rather than crash.
    """
    if not results:
        raise ValueError("ablation_dm_matrix needs at least one result")

    schemes = list(results.keys())
    pairs = [(a, b) for i, a in enumerate(schemes) for b in schemes[i + 1:]]
    regimes = [str(r) for r in regime_labels.unique()]

    rows: dict[tuple[str, str], dict[str, float]] = {}
    for a, b in pairs:
        res_a = results[a]
        res_b = results[b]
        if res_a.oos_forecast_errors.empty or res_b.oos_forecast_errors.empty:
            rows[(a, b)] = {regime: float("nan") for regime in regimes}
            continue
        # Restrict both error series and regime_labels to their common index.
        common_idx = (
            res_a.oos_forecast_errors.index
            .intersection(res_b.oos_forecast_errors.index)
            .intersection(regime_labels.index)
        )
        if len(common_idx) == 0:
            rows[(a, b)] = {regime: float("nan") for regime in regimes}
            continue
        err_a = res_a.oos_forecast_errors.loc[common_idx]
        err_b = res_b.oos_forecast_errors.loc[common_idx]
        labels = regime_labels.loc[common_idx]
        row: dict[str, float] = {}
        for regime in regimes:
            mask = (labels == regime).to_numpy()
            n_obs = int(mask.sum())
            if n_obs < MIN_DM_OBS:
                row[regime] = float("nan")
                continue
            try:
                dm = diebold_mariano(
                    err_a.to_numpy()[mask],
                    err_b.to_numpy()[mask],
                    alternative="less",
                )
                row[regime] = float(dm.p_value)
            except ValueError:
                row[regime] = float("nan")
        rows[(a, b)] = row

    index = pd.MultiIndex.from_tuples(pairs, names=["scheme_a", "scheme_b"])
    return pd.DataFrame.from_records(
        [rows[p] for p in pairs], index=index, columns=regimes
    )


def format_ablation_report(
    results: dict[str, BacktestResult],
    regime_labels: pd.Series,
) -> str:
    """Three-section 52-column text report: per-regime Sharpe, Borda ranking, DM matrix."""
    if not results:
        raise ValueError("format_ablation_report needs at least one result")

    summary = ablation_summary_table(results, regime_labels)
    ranking = ablation_composite_ranking(results, regime_labels)
    dm = ablation_dm_matrix(results, regime_labels)

    buf = io.StringIO()

    # Section 1: per-regime Sharpe.
    buf.write("=" * 52 + "\n")
    buf.write("Per-regime OOS Sharpe by scheme\n")
    buf.write("-" * 52 + "\n")
    header = f"{'Scheme':<18}" + "".join(f" {col:>11}" for col in summary.columns) + "\n"
    buf.write(header)
    for scheme in summary.index:
        row = f"{str(scheme):<18}"
        for col in summary.columns:
            row += f" {_fmt(summary.loc[scheme, col], '.3f'):>11}"
        buf.write(row + "\n")
    buf.write("=" * 52 + "\n")

    # Section 2: balanced Borda composite.
    buf.write("Balanced multi-regime Borda composite\n")
    buf.write("-" * 52 + "\n")
    buf.write(f"{'Scheme':<18}{'composite':>12}{'mean_rank':>12}\n")
    for scheme in ranking.index:
        comp = int(ranking.loc[scheme, "composite_rank"])
        mean_rank = ranking.loc[scheme, "mean_rank_across_regimes"]
        buf.write(f"{str(scheme):<18}{comp:>12}{mean_rank:>12.3f}\n")
    buf.write("=" * 52 + "\n")

    # Section 3: pairwise DM p-values per regime.
    buf.write("Pairwise DM p-values per regime (H1: A errors < B errors)\n")
    buf.write("-" * 52 + "\n")
    pair_header = f"{'A vs B':<28}" + "".join(f" {col:>11}" for col in dm.columns) + "\n"
    buf.write(pair_header)
    for (a, b) in dm.index:
        label = f"{a} vs {b}"
        line = f"{label:<28}"
        for col in dm.columns:
            line += f" {_fmt(dm.loc[(a, b), col], '.3f'):>11}"
        buf.write(line + "\n")
    buf.write("=" * 52 + "\n")

    return buf.getvalue()
