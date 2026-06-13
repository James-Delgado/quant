"""Phase 4A Milestone 6 — headless runner for the four full-panel arms.

Runs one arm at a time (``--arm {signed,vol_scaled,triple_barrier,arima}``)
and writes per-arm parquet checkpoints under ``data/phase4a/{arm}/``.

The notebook (``notebooks/09_phase4a_exit_gate.ipynb``) *consumes*
checkpoints; it never re-runs an arm. Each GBM arm is an nb02-scale run
(~1h+); three GBM arms + an ARIMA control serially is a multi-hour job, and
a kernel death at hour 3 must not lose hours 1 and 2.

Pre-committed protocol (Phase 4A Milestone 6 plan, sections 1-7) — quoted
here so the file is self-describing for the report's reproducibility
appendix.

  1. Primary gate arm = GBM + signed_returns (M2 default); evaluated at
     DM alpha=0.05.
  2. Secondary arms = GBM + vol_scaled, GBM + triple_barrier; gate claims
     must clear Bonferroni-adjusted alpha = 0.05/3.
  3. Control = ARIMA(1,0,0) — one run; label-scheme-independent.
  4. DM error-unit contract: signed-arm errors are native return space;
     vol_scaled predictions are converted to return space via the
     point-in-time vol denominator *before* error computation;
     triple_barrier residuals are classification residuals, NOT
     commensurable with ARIMA return errors → Sharpe only, DM in a
     caveated appendix, no gate claims.
  5. OOS index alignment: gate evaluated on the **intersection** of the
     four runs' oos_returns indices; per-arm dropped-bar count reported.
  6. Sample-weight parity audit: López de Prado uniqueness weights
     (``features/weights.py::compute_sample_weights``) depend ONLY on
     (n_samples, horizon) — NOT on label values. Inside ``GBMModel.fit``
     the horizon is read from ``self.label_horizon``, which is set at
     ``GBMModel(...)`` construction. ``run_label_ablation`` deep-copies
     ONE model per scheme but does NOT update ``self.label_horizon``;
     therefore all three schemes would share whatever horizon the
     supplied model was constructed with, which is wrong for
     triple_barrier (horizon=5) when paired with signed_returns
     (horizon=1). **This runner sidesteps the issue by construction**:
     each ``--arm`` invocation constructs a fresh ``GBMModel`` with the
     scheme's own ``label_horizon`` and calls ``run_portfolio_backtest``
     directly (not via ``run_label_ablation``). The audit finding is
     recorded verbatim in every arm's ``metadata.json`` under
     ``sample_weight_parity_audit``.
  7. These rules are fixed BEFORE any run starts. Do not change them
     based on results.

Deviation from the M6 plan (Task 2)
------------------------------------
The plan suggests dispatching the GBM arms through
``run_label_ablation`` (one scheme at a time, via the ``--arm`` flag) so
the kwargs-discipline is inherited. This runner deliberately does NOT.
Reason: ``run_label_ablation`` deep-copies the supplied model per scheme
but does NOT re-construct it, so ``GBMModel.label_horizon`` stays fixed
at the value it had at the caller's construction site — which silently
mis-weights ``triple_barrier`` (h=5) when paired with a model built for
``signed_returns`` (h=1). The runner sidesteps this by constructing a
fresh ``GBMModel(label_horizon=<scheme_horizon>)`` per arm and calling
``run_portfolio_backtest`` directly. Kwargs-discipline is preserved by
the module-level ``WALK_FORWARD`` and ``SIM_KWARGS`` constants — they
are read once and forwarded identically to every arm.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import pickle
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quant.backtest.harness import BacktestResult, run_portfolio_backtest
from quant.config import settings
from quant.features.cross_sectional import add_cross_sectional_features
from quant.features.engineering import FRED_PUBLICATION_LAGS, build_features
from quant.features.label_schemes import (
    LDP_DEFAULT,
    triple_barrier_labels,
    vol_scaled_returns,
)
from quant.features.labels import LabelResult, generate_labels
from quant.models.arima_baseline import ARIMABaseline
from quant.models.gbm import GBMModel
from quant.storage import catalog, lake

logger = logging.getLogger(__name__)

# ─── Constants pinned by the M6 plan ─────────────────────────────────────────

VALID_ARMS: tuple[str, ...] = ("signed", "vol_scaled", "triple_barrier", "arima")
"""Arms the runner knows how to dispatch."""

WALK_FORWARD: dict[str, int] = {
    "train_window": 504,
    "test_window": 63,
    "step": 63,
    "embargo": 3,
}
"""nb02/nb04 convention — frozen for the M6 evaluation."""

SIM_KWARGS: dict[str, float] = {
    "initial_capital": 100_000.0,
    "commission_per_share": 0.005,
    "slippage_bps": 5.0,
}
"""Trade-simulator parameters — match nb04 (Phase 3 sentiment ablation)."""

GBM_N_ITER: int = 50
"""RandomizedSearchCV budget — pre-committed at the M2 default."""

GBM_N_SPLITS: int = 3
"""Inner TimeSeriesSplit folds — pre-committed at the M2 default."""

GBM_RANDOM_STATE: int = 0
"""Seed — deterministic reproduction across re-runs."""

ARIMA_ORDER: tuple[int, int, int] = (1, 0, 0)
"""AR(1) on stationary forward returns — see models/arima_baseline.py docstring."""

SENTIMENT_LOOKBACK_DAYS: int = 30
"""Match Phase 3's +sentiment arm convention."""

FINAL_FEATURE_COLUMNS: tuple[str, ...] = (
    # 17 base features (Phase 2.5 set — 13 price/trend + 3 FRED + 1 derived)
    "ret_1d",
    "ret_5d",
    "ret_21d",
    "vol_21d",
    "vol_63d",
    "mom_21d",
    "rsi_14",
    "log_volume",
    "ret_252d",
    "ret_126d",
    "ma200_ratio",
    "ma50_ratio",
    "volume_ratio",
    "DGS10",
    "DFF",
    "VIXCLS",
    "yield_curve",
    # 4 regime-indicator features (Phase 4A Milestone 3 — appended by
    # ``_add_regime_features`` after the 17 base columns)
    "vix_regime",
    "curve_inverted",
    "vol_regime_ratio",
    "trend_regime",
    # 3 sentiment features (Phase 3 — appended by build_features when
    # sentiment_df is passed)
    "sentiment_score",
    "doc_count",
    "has_coverage",
    # 1 Milestone 3 cross-sectional survivor (xs_rank_vol_21d). The other
    # two ranks are not added — this runner's contract is the final set.
    "xs_rank_vol_21d",
)
"""The final 25-column feature set. Order is contract-relevant; the
config hash is computed over this exact tuple."""

SAMPLE_WEIGHT_PARITY_AUDIT: str = (
    "compute_sample_weights(n_samples, horizon) depends ONLY on the "
    "train-window size and the label horizon — NOT on label values. "
    "GBMModel.fit reads horizon from self.label_horizon (set at "
    "construction time). run_label_ablation deep-copies one model per "
    "scheme but does NOT update self.label_horizon, so weights would not "
    "match a scheme whose horizon differs from the model's construction "
    "horizon (e.g., triple_barrier h=5 paired with a model built for "
    "h=1). This runner sidesteps the issue by construction: each --arm "
    "invocation constructs a fresh GBMModel with the scheme's own "
    "label_horizon and calls run_portfolio_backtest directly, NOT via "
    "run_label_ablation. Per-arm metadata pins (label_horizon, "
    "gbm_label_horizon) for downstream audit."
)
"""Recorded verbatim in every arm's metadata.json (protocol item 6)."""


# ─── Data loading ────────────────────────────────────────────────────────────


def _load_prices_panel(symbols: Sequence[str]) -> dict[str, pd.DataFrame]:
    """Load adjusted OHLCV bars from the lake for each symbol in *symbols*.

    Mirrors the nb04 loader: pulls ``equity_eod_tiingo`` via DuckDB,
    renames ``adjClose`` to ``close``, and returns a per-symbol dict
    indexed by timestamp. Empty symbols are dropped silently.
    """
    if not symbols:
        raise ValueError("symbols must not be empty")
    syms_sql = ", ".join(f"'{s}'" for s in symbols)
    eq = catalog.query(
        f"""
        SELECT symbol, timestamp, open, high, low, close, adjClose, volume
        FROM {catalog.table("equity_eod_tiingo")}
        WHERE symbol IN ({syms_sql})
        ORDER BY symbol, timestamp
        """
    )
    eq["timestamp"] = pd.to_datetime(eq["timestamp"])
    eq = eq.set_index("timestamp")
    prices: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        sub = eq[eq["symbol"] == sym][["open", "high", "low", "adjClose", "volume"]].copy()
        if sub.empty:
            logger.warning("symbol %s missing from equity_eod_tiingo — skipping", sym)
            continue
        prices[sym] = sub.rename(columns={"adjClose": "close"}).sort_index().dropna()
    if not prices:
        raise RuntimeError("no symbols had usable bars in the lake")
    return prices


def _load_sentiment_df() -> pd.DataFrame:
    """Load the FinBERT-scored sentiment table from the lake.

    Returns an empty DataFrame if the dataset is missing — callers can
    decide whether that is a fatal error.
    """
    df = lake.read_processed("sentiment_scored")
    if df.empty:
        logger.warning(
            "sentiment_scored dataset empty — runner will build features "
            "without sentiment columns (this DEVIATES from the pre-committed "
            "Phase 3 +sentiment arm convention)"
        )
    return df


# ─── Panel construction ──────────────────────────────────────────────────────


def _build_full_panel(
    symbols: Sequence[str],
    sentiment_df: pd.DataFrame,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """Build the 25-column feature panel for *symbols*, mirroring nb04.

    Returns ``(features_by_symbol, prices_by_symbol)`` where both dicts
    are keyed by ticker. ``features_by_symbol`` is sliced down to
    ``FINAL_FEATURE_COLUMNS`` in that exact order.
    """
    prices_raw = _load_prices_panel(symbols)
    actual_symbols = list(prices_raw.keys())

    sentiment_arg: pd.DataFrame | None
    sentiment_arg = sentiment_df if not sentiment_df.empty else None

    features_raw = build_features(
        actual_symbols,
        prices_raw,
        sentiment_df=sentiment_arg,
        sentiment_lookback_days=SENTIMENT_LOOKBACK_DAYS,
        fred_publication_lags=FRED_PUBLICATION_LAGS,
    )
    # Add the M3 survivor: xs_rank_vol_21d (only). The other two default
    # ranks in cross_sectional are not added — the final-feature contract
    # specifies one M3 column.
    features_raw = add_cross_sectional_features(
        features_raw, columns=("vol_21d",)
    )

    final_cols = list(FINAL_FEATURE_COLUMNS)
    features_by_symbol: dict[str, pd.DataFrame] = {}
    prices_by_symbol: dict[str, pd.DataFrame] = {}
    for sym in actual_symbols:
        feat = features_raw[sym]
        missing = [c for c in final_cols if c not in feat.columns]
        if missing:
            raise RuntimeError(
                f"feature frame for {sym!r} missing required columns: {missing}"
            )
        sliced = feat[final_cols]
        features_by_symbol[sym] = sliced
        prices_by_symbol[sym] = prices_raw[sym]

    return features_by_symbol, prices_by_symbol


# ─── Label-scheme dispatch ───────────────────────────────────────────────────


def _generate_labels_for_arm(
    arm: str,
    prices_by_symbol: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.Series], int]:
    """Build ``labels_by_symbol`` and return the (uniform) label horizon.

    ARIMA uses signed_returns for label consistency — ARIMA fits the
    return series directly so labels do not change its predictions, but
    the same label series is used so purge horizons match the primary
    arm.
    """
    horizon: int | None = None
    labels_by_symbol: dict[str, pd.Series] = {}
    for sym, prices_df in prices_by_symbol.items():
        close = prices_df["close"]
        if arm in ("signed", "arima"):
            lr: LabelResult = generate_labels(close, horizon=1)
        elif arm == "vol_scaled":
            lr = vol_scaled_returns(close, horizon=1, vol_window=21)
        elif arm == "triple_barrier":
            lr = triple_barrier_labels(close, LDP_DEFAULT)
        else:
            raise ValueError(f"unknown arm {arm!r}")
        labels_by_symbol[sym] = lr.series
        if horizon is None:
            horizon = lr.horizon_bars
        elif horizon != lr.horizon_bars:
            raise RuntimeError(
                f"arm {arm!r} produced inconsistent horizons across symbols "
                f"({horizon} vs {lr.horizon_bars})"
            )
    assert horizon is not None
    return labels_by_symbol, horizon


def _build_model(arm: str, label_horizon: int) -> Any:
    """Construct the per-arm model with the matching ``label_horizon``."""
    if arm == "arima":
        return ARIMABaseline(order=ARIMA_ORDER)
    return GBMModel(
        label_horizon=label_horizon,
        n_iter=GBM_N_ITER,
        n_splits=GBM_N_SPLITS,
        random_state=GBM_RANDOM_STATE,
    )


# ─── Config-hash + git metadata ──────────────────────────────────────────────


def _git_sha() -> str:
    """Best-effort short git SHA — falls back to ``"unknown"``."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8").strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _build_run_config(
    arm: str,
    label_horizon: int,
    walk_forward: Mapping[str, int] = WALK_FORWARD,
) -> dict[str, Any]:
    """Build the deterministic run-config dict that gets hashed.

    Order is preserved — pickling a dict in CPython 3.7+ retains insertion
    order, so the config-hash is deterministic across runs as long as the
    keys here are listed in a fixed order.
    """
    return {
        "arm": arm,
        "label_horizon": label_horizon,
        "feature_columns": list(FINAL_FEATURE_COLUMNS),
        "walk_forward": dict(walk_forward),
        "sim_kwargs": dict(SIM_KWARGS),
        "model_params": (
            {
                "type": "GBMModel",
                "n_iter": GBM_N_ITER,
                "n_splits": GBM_N_SPLITS,
                "random_state": GBM_RANDOM_STATE,
                "label_horizon": label_horizon,
            }
            if arm != "arima"
            else {"type": "ARIMABaseline", "order": ARIMA_ORDER}
        ),
        "sentiment_lookback_days": SENTIMENT_LOOKBACK_DAYS,
        "fred_publication_lags": dict(FRED_PUBLICATION_LAGS),
    }


def _hash_config(cfg: Mapping[str, Any]) -> str:
    """SHA-256 of the pickled config — protocol-frozen identity for a run."""
    payload = pickle.dumps(cfg, protocol=4)
    return hashlib.sha256(payload).hexdigest()


# ─── Output writers ──────────────────────────────────────────────────────────


def _write_outputs(
    arm_dir: Path,
    result: BacktestResult,
    metadata: dict[str, Any],
) -> None:
    """Write the three per-arm artifacts.

    Parquet files are written via pandas + pyarrow. The DatetimeIndex on
    the return / error series is preserved by writing a single-column
    frame whose index is the timestamps.
    """
    arm_dir.mkdir(parents=True, exist_ok=True)

    oos_returns = result.oos_returns
    oos_errors = result.oos_forecast_errors

    oos_returns.to_frame(name="oos_returns").to_parquet(
        arm_dir / "oos_returns.parquet"
    )
    oos_errors.to_frame(name="oos_forecast_errors").to_parquet(
        arm_dir / "oos_forecast_errors.parquet"
    )
    with (arm_dir / "metadata.json").open("w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2, sort_keys=False, default=str)


# ─── Smoke-mode synthetic panel ──────────────────────────────────────────────


def _make_smoke_panel() -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """3-symbol x 252-bar synthetic panel that exercises the runner plumbing.

    Builds OHLCV + a constant random-noise feature frame restricted to
    ``FINAL_FEATURE_COLUMNS``. Not a real-world signal — only proves
    the file IO / argparse / hashing wiring.
    """
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2024-01-02", periods=252, tz="UTC")
    prices_by_symbol: dict[str, pd.DataFrame] = {}
    features_by_symbol: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(("AAA", "BBB", "CCC")):
        ret = rng.normal(0.0003, 0.012, len(dates))
        close = 100.0 * np.cumprod(1 + ret)
        prices_by_symbol[sym] = pd.DataFrame(
            {
                "open": close * (1 + rng.uniform(-0.002, 0.002, len(dates))),
                "high": close * (1 + rng.uniform(0.0, 0.005, len(dates))),
                "low": close * (1 - rng.uniform(0.0, 0.005, len(dates))),
                "close": close,
                "volume": rng.integers(500_000, 2_000_000, len(dates)).astype(float),
            },
            index=dates,
        )
        feats = pd.DataFrame(
            rng.standard_normal((len(dates), len(FINAL_FEATURE_COLUMNS))),
            index=dates,
            columns=list(FINAL_FEATURE_COLUMNS),
        )
        features_by_symbol[sym] = feats
    return features_by_symbol, prices_by_symbol


# ─── Per-arm dispatch ────────────────────────────────────────────────────────


def _run_arm(
    arm: str,
    output_dir: Path,
    smoke: bool,
    force: bool,
) -> int:
    """Execute one arm end-to-end. Return 0 on success / skip, nonzero on error.

    Idempotency: if ``metadata.json`` already exists in the arm's output
    directory and ``--force`` is not set, this function logs a skip
    message and returns 0 without invoking ``run_portfolio_backtest``.
    """
    arm_subdir = f"smoke_{arm}" if smoke else arm
    arm_dir = output_dir / arm_subdir
    meta_path = arm_dir / "metadata.json"

    if meta_path.exists() and not force:
        logger.info("checkpoint present at %s — skipping (use --force to re-run)", meta_path)
        return 0

    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    logger.info("arm=%s smoke=%s starting at %s", arm, smoke, started.isoformat())

    # Walk-forward parameters: smoke uses smaller windows so the 252-bar
    # synthetic panel produces real folds; production uses the M6-frozen
    # nb02/nb04 conventions.
    wf_params = (
        {"train_window": 100, "test_window": 30, "step": 30, "embargo": 3}
        if smoke
        else WALK_FORWARD
    )

    if smoke:
        features_by_symbol, prices_by_symbol = _make_smoke_panel()
        labels_by_symbol, label_horizon = _generate_labels_for_arm(arm, prices_by_symbol)
    else:
        sentiment_df = _load_sentiment_df()
        features_by_symbol, prices_by_symbol = _build_full_panel(
            settings.equity_universe, sentiment_df
        )
        labels_by_symbol, label_horizon = _generate_labels_for_arm(arm, prices_by_symbol)

    # Align indices per-symbol: drop bars where feature/label are NaN, mirroring
    # nb04's pre-backtest cleanup. This also ensures the simulator sees only
    # bars where all three frames exist.
    aligned_features: dict[str, pd.DataFrame] = {}
    aligned_labels: dict[str, pd.Series] = {}
    aligned_prices: dict[str, pd.DataFrame] = {}
    for sym in features_by_symbol:
        feat = features_by_symbol[sym]
        lab = labels_by_symbol[sym]
        valid = feat.dropna().index.intersection(lab.dropna().index)
        if len(valid) == 0:
            logger.warning("symbol %s has no overlapping non-NaN bars — dropping", sym)
            continue
        aligned_features[sym] = feat.loc[valid]
        aligned_labels[sym] = lab.loc[valid]
        aligned_prices[sym] = prices_by_symbol[sym].loc[valid]

    if not aligned_features:
        raise RuntimeError("no symbols survived feature/label alignment — cannot run")

    model = _build_model(arm, label_horizon)
    cfg = _build_run_config(arm, label_horizon, walk_forward=wf_params)
    config_hash = _hash_config(cfg)

    result = run_portfolio_backtest(
        model=model,
        features_by_symbol=aligned_features,
        labels_by_symbol=aligned_labels,
        prices_by_symbol=aligned_prices,
        train_window=wf_params["train_window"],
        test_window=wf_params["test_window"],
        step=wf_params["step"],
        embargo=wf_params["embargo"],
        label_horizon=label_horizon,
        **SIM_KWARGS,
    )
    elapsed = time.monotonic() - t0
    finished = datetime.now(timezone.utc)

    oos_returns = result.oos_returns
    oos_idx = oos_returns.index
    metadata: dict[str, Any] = {
        "arm": arm,
        "smoke": smoke,
        "git_sha": _git_sha(),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "elapsed_seconds": elapsed,
        "config_hash": config_hash,
        "run_config": cfg,
        "sample_weight_parity_audit": SAMPLE_WEIGHT_PARITY_AUDIT,
        "n_symbols_in_panel": len(aligned_features),
        "symbols": sorted(aligned_features.keys()),
        "n_oos_bars": int(len(oos_returns)),
        "n_folds": int(len(result.fold_metrics)),
        "oos_start": str(oos_idx.min()) if len(oos_idx) > 0 else None,
        "oos_end": str(oos_idx.max()) if len(oos_idx) > 0 else None,
        "aggregate_sharpe": float(result.oos_metrics.get("sharpe", float("nan"))),
        "aggregate_max_dd": float(result.oos_metrics.get("max_drawdown", float("nan"))),
        "label_horizon": label_horizon,
    }

    _write_outputs(arm_dir, result, metadata)
    logger.info(
        "arm=%s wrote %d bars (%d folds) Sharpe=%.4f MaxDD=%.4f elapsed=%.1fs",
        arm,
        metadata["n_oos_bars"],
        metadata["n_folds"],
        metadata["aggregate_sharpe"],
        metadata["aggregate_max_dd"],
        elapsed,
    )
    return 0


# ─── argparse + main ─────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for the runner.

    Exposed at module scope so tests can construct + parse without
    invoking ``main`` (and the heavy data-loading path it triggers).
    """
    parser = argparse.ArgumentParser(
        description="Phase 4A Milestone 6 headless arm runner.",
    )
    parser.add_argument(
        "--arm",
        required=True,
        choices=VALID_ARMS,
        help="Which arm to run: signed (primary), vol_scaled, triple_barrier, or arima (control).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/phase4a"),
        help="Root directory for per-arm checkpoint subdirectories.",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run on a 3-symbol x 252-bar synthetic panel — plumbing test only.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run even if a checkpoint already exists for this arm.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default INFO).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    return _run_arm(
        arm=args.arm,
        output_dir=args.output_dir,
        smoke=args.smoke,
        force=args.force,
    )


if __name__ == "__main__":
    sys.exit(main())
