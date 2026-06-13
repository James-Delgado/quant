"""Text and DataFrame report generation for BacktestResult."""
from __future__ import annotations

import io
import math
import warnings
from typing import Any

import pandas as pd

from quant.backtest.harness import BacktestResult
from quant.backtest.regime_metrics import (
    MIN_DM_OBS,
    compute_regime_metrics,
)
from quant.backtest.statistics import bootstrap_sharpe_delta_ci, diebold_mariano


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


# ─── Feature-ablation reporting + PRD gate (Phase 4A Milestone 3) ────────────


def feature_ablation_table(
    results: dict[str, BacktestResult],
    baseline_name: str,
    regime_labels: pd.Series,
) -> pd.DataFrame:
    """One row per feature set: Sharpe *deltas* vs baseline, per regime.

    Columns are ``aggregate`` + each regime label + ``n_bars``. The baseline
    row shows *absolute* Sharpe (the reference level); every other row shows
    the Sharpe difference ``variant − baseline`` in that column. ``n_bars``
    counts each result's OOS bars, mirroring the accounting in
    ``regime_summary_table``.

    ``regime_labels`` may index a superset of each result's OOS index; the
    slicing-to-common-index behaviour matches ``ablation_summary_table``
    (regimes with no overlap show NaN).
    """
    if not results:
        raise ValueError("feature_ablation_table needs at least one result")
    if baseline_name not in results:
        raise ValueError(
            f"baseline {baseline_name!r} not found in results: {list(results)}"
        )

    sharpes = {
        name: _scheme_per_regime_sharpe(res, regime_labels)
        for name, res in results.items()
    }
    cols = ["aggregate"] + [str(r) for r in regime_labels.unique()]
    base = sharpes[baseline_name]

    rows: dict[str, dict[str, float]] = {}
    for name, s in sharpes.items():
        if name == baseline_name:
            row = {c: s[c] for c in cols}
        else:
            row = {c: s[c] - base[c] for c in cols}
        row["n_bars"] = float(len(results[name].oos_returns))
        rows[name] = row
    out = pd.DataFrame.from_dict(rows, orient="index", columns=[*cols, "n_bars"])
    out["n_bars"] = out["n_bars"].astype(int)
    return out


def feature_ablation_gate(
    results: dict[str, BacktestResult],
    baseline_name: str,
    regime_labels: pd.Series,
    *,
    min_lift: float = 0.1,
    min_features: int = 3,
    noise_guard: bool = True,
    block_len: int = 21,
    n_boot: int = 1000,
    seed: int = 0,
) -> dict[str, Any]:
    """Evaluate the Phase 4A Milestone 3 feature gate.

    PRD metric, verbatim: *"≥ 3 features show ≥ 0.1 Sharpe lift net of
    costs in ≥ 1 regime."* Net-of-costs is inherent — ``oos_returns`` are
    post-cost simulator output. A candidate feature set qualifies when its
    Sharpe delta vs the baseline set is ``>= min_lift`` in at least one
    regime, **and** (when ``noise_guard=True``) at least one of:

    a. the paired 21-day block-bootstrap 90% CI on the Sharpe delta
       (``bootstrap_sharpe_delta_ci`` on the regime-sliced return series)
       excludes 0 in the qualifying regime, or
    b. the delta is positive in ≥ 2 regime columns (cross-regime
       sign-consistency).

    Why the noise guard: the standard error of an annualized Sharpe
    estimate over ~8 years of daily data is ≈ 0.35 — several times the
    0.1 lift threshold — so picking the features with the largest raw
    lifts out of 7 candidates × several regimes would mostly select noise
    (winner's curse). Requiring the CI to exclude 0 *or* the lift to
    replicate in sign across regimes filters one-regime flukes.

    Regimes with no overlap between ``regime_labels`` and a result's OOS
    index warn (``warnings.warn``) and are skipped rather than crashing,
    mirroring ``regime_dm_test``'s thin-regime handling.

    Returns
    -------
    dict with keys:

    * ``gate_passed``         — bool — ``len(qualifying) >= min_features``
    * ``qualifying_features`` — ``{name: {regime, lift, ci_low, ci_high,
                                  sign_consistent}}`` (best qualifying
                                  regime per feature, highest lift first)
    * ``n_candidates``        — number of non-baseline feature sets
    * ``thresholds``          — echo of the gate parameters
    """
    if not results:
        raise ValueError("feature_ablation_gate needs at least one result")
    if baseline_name not in results:
        raise ValueError(
            f"baseline {baseline_name!r} not found in results: {list(results)}"
        )

    baseline_res = results[baseline_name]
    base_sharpes = _scheme_per_regime_sharpe(baseline_res, regime_labels)
    regime_values = list(regime_labels.unique())

    qualifying: dict[str, dict[str, Any]] = {}
    for name, res in results.items():
        if name == baseline_name:
            continue
        sharpes = _scheme_per_regime_sharpe(res, regime_labels)
        deltas: dict[str, float] = {}
        regime_for_key: dict[str, Any] = {}
        missing: list[str] = []
        for rv in regime_values:
            key = str(rv)
            regime_for_key[key] = rv
            delta = sharpes[key] - base_sharpes[key]
            if math.isnan(delta):
                missing.append(key)
            else:
                deltas[key] = delta
        if missing:
            warnings.warn(
                f"feature set {name!r}: regimes {missing} have no "
                "overlapping OOS bars — skipped in the gate",
                stacklevel=2,
            )

        sign_consistent = sum(1 for d in deltas.values() if d > 0) >= 2

        # Best qualifying regime wins: walk candidates by descending lift.
        candidates = sorted(
            ((k, d) for k, d in deltas.items() if d >= min_lift),
            key=lambda kv: kv[1],
            reverse=True,
        )
        for key, lift in candidates:
            ci_low, ci_high = float("nan"), float("nan")
            regime_dates = regime_labels.index[regime_labels == regime_for_key[key]]
            variant_r = res.oos_returns.loc[
                res.oos_returns.index.intersection(regime_dates)
            ]
            baseline_r = baseline_res.oos_returns.loc[
                baseline_res.oos_returns.index.intersection(regime_dates)
            ]
            try:
                ci_low, ci_high = bootstrap_sharpe_delta_ci(
                    variant_r,
                    baseline_r,
                    block_len=block_len,
                    n_boot=n_boot,
                    seed=seed,
                )
            except ValueError as exc:
                warnings.warn(
                    f"feature set {name!r}, regime {key!r}: bootstrap "
                    f"failed ({exc}) — CI unavailable",
                    stacklevel=2,
                )
            ci_excludes_zero = not math.isnan(ci_low) and (
                ci_low > 0.0 or ci_high < 0.0
            )
            if (not noise_guard) or ci_excludes_zero or sign_consistent:
                qualifying[name] = {
                    "regime": key,
                    "lift": float(lift),
                    "ci_low": float(ci_low),
                    "ci_high": float(ci_high),
                    "sign_consistent": sign_consistent,
                }
                break

    return {
        "gate_passed": len(qualifying) >= min_features,
        "qualifying_features": qualifying,
        "n_candidates": len(results) - 1,
        "thresholds": {
            "min_lift": min_lift,
            "min_features": min_features,
            "noise_guard": noise_guard,
            "block_len": block_len,
            "n_boot": n_boot,
            "ci": 0.90,
        },
    }


def format_feature_ablation_report(
    results: dict[str, BacktestResult],
    baseline_name: str,
    regime_labels: pd.Series,
    **gate_kwargs: Any,
) -> str:
    """Two-section 52-column text report: Sharpe-delta table + gate verdict.

    ``gate_kwargs`` are forwarded verbatim to ``feature_ablation_gate``
    (``min_lift``, ``min_features``, ``noise_guard``, ``n_boot``, ...).
    """
    tbl = feature_ablation_table(results, baseline_name, regime_labels)
    gate = feature_ablation_gate(results, baseline_name, regime_labels, **gate_kwargs)
    th = gate["thresholds"]

    buf = io.StringIO()

    # Section 1: per-regime Sharpe delta table.
    buf.write("=" * 52 + "\n")
    buf.write(f"Per-regime OOS Sharpe Δ vs {baseline_name!r} (baseline row = absolute)\n")
    buf.write("-" * 52 + "\n")
    value_cols = [c for c in tbl.columns if c != "n_bars"]
    header = f"{'Set':<14}" + "".join(f" {col:>11}" for col in value_cols) + f" {'bars':>6}\n"
    buf.write(header)
    for name in tbl.index:
        line = f"{str(name):<14}"
        for col in value_cols:
            line += f" {_fmt(tbl.loc[name, col], '.3f'):>11}"
        line += f" {int(tbl.loc[name, 'n_bars']):>6}"
        buf.write(line + "\n")
    buf.write("=" * 52 + "\n")

    # Section 2: gate verdict + qualifying features.
    verdict = "PASSED" if gate["gate_passed"] else "FAILED"
    guard = "on" if th["noise_guard"] else "off"
    buf.write(
        f"Phase 4A M3 gate: {verdict} — "
        f"{len(gate['qualifying_features'])}/{th['min_features']} features "
        f"with >= {th['min_lift']} Sharpe lift in >= 1 regime "
        f"(noise guard {guard})\n"
    )
    buf.write("-" * 52 + "\n")
    if gate["qualifying_features"]:
        buf.write(f"{'Feature':<16}{'regime':>12}{'lift':>8}{'CI90':>18}{'sign':>6}\n")
        for name, info in gate["qualifying_features"].items():
            ci_str = f"[{_fmt(info['ci_low'], '.2f')}, {_fmt(info['ci_high'], '.2f')}]"
            sign = "yes" if info["sign_consistent"] else "no"
            buf.write(
                f"{str(name):<16}{str(info['regime']):>12}"
                f"{info['lift']:>8.3f}{ci_str:>18}{sign:>6}\n"
            )
    else:
        buf.write("No qualifying features.\n")
    buf.write("=" * 52 + "\n")

    return buf.getvalue()
