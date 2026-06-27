"""Project B1 Milestone 3 — headless runner for the four full-panel target arms.

Runs one *target* at a time (``--target {drawdown_21d,realized_vol_21d,
directional_5d,directional_21d}``) on the full 33-symbol × ~22-year panel and
writes per-target checkpoints under ``data/b1/{target}/``.

This is the confirmatory full-panel run for B1-M2's provisional slice ablation
(METHODOLOGY §11 — slice verdict is provisional; this milestone confirms it on
the full panel, which restores all three required regimes incl. ``qe_bull`` that
the 2018-start slice could not test). The notebook
(``notebooks/11_b1_exit_gate.ipynb``) *consumes* checkpoints and renders the
verdict via ``b1_gate_report``; it never re-fits a model (METHODOLOGY §7).

Why this is NOT a copy of ``run_phase4a_arms.py``
-------------------------------------------------
The Phase 4A runner is **return → simulator → Sharpe** centric: every arm is a
``GBMModel`` (XGBRegressor) routed through ``run_portfolio_backtest`` which only
ever emits ``oos_returns`` / ``oos_forecast_errors``. B1 evaluates *non-return*
prediction objects whose primary metrics are **ROC-AUC** (T1/T3/T4) and **MAE**
(T2). Those need raw OOS ``(y_true, y_pred)`` pairs, which the harness path never
exposes. This runner therefore uses ``target_eval.collect_oos_predictions`` (the
purged-walk-forward prediction *collector* — same split machinery, same leakage
controls) and checkpoints the prediction frames. The directional targets (T3/T4)
additionally carry a **tradeable Sharpe arm**: the runner routes the GBM
``sign(pred − 0.5)`` and an ARIMA return forecast through the simulator and
checkpoints both return series.

Checkpoint contract (per target dir ``data/b1/{target}/``)
----------------------------------------------------------
* ``predictions.parquet`` — date-indexed, columns ``symbol, y_true, gbm_pred``
  plus the per-target deterministic/ARIMA baseline columns the gate needs:
    - ``drawdown_21d``:   ``+ vol_proxy``    (EWMA-vol score; higher vol → higher
                                              DD-probability rank — the
                                              vol-implied DD proxy baseline)
    - ``realized_vol_21d``: ``+ ewma_pred, arima_pred``
    - ``directional_5d`` / ``directional_21d``: ``+ arima_pred`` (the AUC baseline
                                                  score)
* ``returns_gbm.parquet`` / ``returns_arima.parquet`` — directional targets only:
  the GBM and ARIMA tradeable-Sharpe-arm OOS return series.
* ``metadata.json`` — config hash, timings, panel symbols, fold/row counts, the
  drawdown base rate, the invariant-parity audit, and the pinned walk-forward /
  GBM / ARIMA config (METHODOLOGY §8 — invariant parity in code, not prose).

Every fit-derived artifact is checkpointed; the deterministic baselines (EWMA,
climatology) are computed here too, aligned to the GBM frame, so the verdict
notebook is a pure scoring step over checkpoints with no model fit and no price
reload.

Pre-committed protocol (frozen BEFORE any run; do not change based on results)
------------------------------------------------------------------------------
  1. Feature set = the frozen 25-column M6 set (``FINAL_FEATURE_COLUMNS``),
     identical to ``run_phase4a_arms.py`` — B1 holds the feature set fixed and
     varies only the target. Asserted equal by ``tests/test_run_b1_arms.py``.
  2. Walk-forward = nb02/nb04 convention (train 504, test 63, step 63, embargo 3).
  3. GBM = RandomizedSearchCV n_iter=50, n_splits=3, seed=0 (the M2 default).
  4. ARIMA control = (1,0,0) — AR(1), label-order-independent.
  5. Targets, horizons, metrics, baselines and all materiality thresholds are
     pinned in ``features/targets.py`` (``TARGET_CATALOG``) — the runner adds no
     thresholds; the gate (``b1_gate_report``) renders the verdict.
  6. Deflation N for the gate = ``cumulative_trial_count()`` at gate time +
     ``N_SELF_COMPARISONS`` (this matrix's own 4×3 comparisons, pinned below), so
     the gate auto-penalises B1's search width exactly as the slice did — the
     notebook applies this; the runner's ``--log-ledger`` then appends the trials
     so future PRDs inherit the grown N.
  7. These rules are fixed before any run. Do not change them based on results.
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

from quant.backtest.harness import run_portfolio_backtest
from quant.backtest.metrics import compute_metrics
from quant.backtest.target_eval import collect_oos_predictions, simulate_signal_returns
from quant.config import settings
from quant.features.cross_sectional import add_cross_sectional_features
from quant.features.engineering import FRED_PUBLICATION_LAGS, build_features
from quant.features.targets import TARGET_CATALOG, make_target_labels
from quant.ledger import record_run
from quant.models.arima_baseline import ARIMABaseline
from quant.models.gbm import GBMModel
from quant.storage import catalog, lake

logger = logging.getLogger(__name__)

# ─── Constants pinned by the B1-M3 protocol ──────────────────────────────────

VALID_TARGETS: tuple[str, ...] = tuple(TARGET_CATALOG)
"""The four B1 targets, sourced from the pinned catalog (no drift)."""

WALK_FORWARD: dict[str, int] = {
    "train_window": 504,
    "test_window": 63,
    "step": 63,
    "embargo": 3,
}
"""nb02/nb04 convention — frozen for the B1-M3 evaluation (matches Phase 4A M6)."""

SIM_KWARGS: dict[str, float] = {
    "initial_capital": 100_000.0,
    "commission_per_share": 0.005,
    "slippage_bps": 5.0,
}
"""Trade-simulator parameters — match nb04 / the Phase 4A runner (Sharpe arm)."""

GBM_N_ITER: int = 50
GBM_N_SPLITS: int = 3
GBM_RANDOM_STATE: int = 0
"""RandomizedSearchCV budget / inner folds / seed — the M2 default, pre-committed."""

GBM_SMOKE_KWARGS: dict[str, int] = {"n_iter": 2, "n_splits": 2, "random_state": GBM_RANDOM_STATE}
"""Tiny GBM budget for ``--smoke`` plumbing runs only (never the real verdict)."""

ARIMA_ORDER: tuple[int, int, int] = (1, 0, 0)
"""AR(1) — the directional Sharpe-arm and vol-MAE baseline control."""

SENTIMENT_LOOKBACK_DAYS: int = 30
"""Match Phase 3 / the Phase 4A runner +sentiment arm convention."""

EWMA_LAMBDA: float = 0.94
"""RiskMetrics persistence — the T1 vol-implied-DD proxy and T2 EWMA baseline."""

REQUIRED_REGIMES: tuple[str, ...] = ("qe_bull", "covid", "rate_cycle")
"""The three required regimes the gate evaluates (DEFAULT_REGIMES_REQUIRED)."""

N_SELF_COMPARISONS: int = len(TARGET_CATALOG) * len(REQUIRED_REGIMES)
"""4 targets × 3 required regimes = 12 — this matrix's self-comparison count,
pinned before any result so the DSR deflation-N self-penalty is not chosen post
hoc (mirrors nb10's pinned ``N_SELF_COMPARISONS``)."""

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
    # 4 regime-indicator features (Phase 4A M3)
    "vix_regime",
    "curve_inverted",
    "vol_regime_ratio",
    "trend_regime",
    # 3 sentiment features (Phase 3)
    "sentiment_score",
    "doc_count",
    "has_coverage",
    # 1 M3 cross-sectional survivor
    "xs_rank_vol_21d",
)
"""The frozen 25-column M6 feature set — identical to
``run_phase4a_arms.FINAL_FEATURE_COLUMNS``. B1 varies only the TARGET; the input
feature set is held fixed at M6. ``tests/test_run_b1_arms.py`` asserts parity with
the Phase 4A runner so the two contracts cannot silently drift (METHODOLOGY §6)."""

INVARIANT_PARITY_AUDIT: str = (
    "B1-M3 reuses the SAME purged walk-forward machinery as the harness: "
    "collect_oos_predictions calls walkforward_splits with label_horizon = the "
    "target's pinned TARGET_CATALOG horizon (drawdown/vol/dir-21d=21, dir-5d=5), "
    "so purge over-purges by the label horizon, never under (backtest/CLAUDE.md "
    "inv. 1-4). Each target constructs a FRESH GBMModel(label_horizon=<horizon>) "
    "so López de Prado uniqueness weights match the horizon (the run_label_ablation "
    "mis-weight bug the Phase 4A runner documents cannot occur here — no ablation "
    "orchestrator is used). The 25-col M6 feature set is held fixed; only the "
    "target/label changes. Per-target metadata pins (label_horizon, n_folds, "
    "symbols, n_oos_rows) for downstream audit."
)
"""Recorded verbatim in every target's metadata.json (METHODOLOGY §8)."""

# Whether each target carries a tradeable Sharpe arm (directional only).
_HAS_SHARPE_ARM: dict[str, bool] = {
    "drawdown_21d": False,
    "realized_vol_21d": False,
    "directional_5d": True,
    "directional_21d": True,
}


# ─── Data loading (mirrors run_phase4a_arms._load_prices_panel) ───────────────


def _load_prices_panel(symbols: Sequence[str]) -> dict[str, pd.DataFrame]:
    """Load adjusted OHLCV bars from the lake for each symbol in *symbols*."""
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
    """Load the FinBERT-scored sentiment table from the lake (empty → no columns)."""
    df = lake.read_processed("sentiment_scored")
    if df.empty:
        logger.warning(
            "sentiment_scored dataset empty — runner will build features without "
            "sentiment columns (DEVIATES from the M6 25-col feature contract)"
        )
    return df


def _to_naive_utc(idx: pd.Index) -> pd.DatetimeIndex:
    """Normalise a DatetimeIndex to tz-naive UTC, preserving the instant.

    The lake's price index is ``America/New_York`` (close ~20:00-04:00) while
    ``build_features`` returns its index in **UTC** — the *same instants*, different
    tz representations (e.g. ``2001-06-11 20:00 EDT`` ≡ ``2001-06-12 00:00 UTC``).
    The Phase 4A runner keeps both tz-aware and lets pandas intersect by instant,
    but ``collect_oos_predictions`` requires ``features.index.equals(labels.index)``,
    so we must put feature *and* price/label indices in ONE tz. We ``tz_convert`` to
    UTC then drop the tz — NOT ``tz_localize(None)``, which would strip the label
    without converting and shift the index by the UTC offset (the alignment bug).
    """
    idx = pd.DatetimeIndex(idx)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return idx


def _build_full_panel(
    symbols: Sequence[str],
    sentiment_df: pd.DataFrame,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """Build the 25-col M6 feature panel, mirroring ``run_phase4a_arms._build_full_panel``."""
    prices_raw = _load_prices_panel(symbols)
    actual_symbols = list(prices_raw.keys())

    sentiment_arg = sentiment_df if not sentiment_df.empty else None
    features_raw = build_features(
        actual_symbols,
        prices_raw,
        sentiment_df=sentiment_arg,
        sentiment_lookback_days=SENTIMENT_LOOKBACK_DAYS,
        fred_publication_lags=FRED_PUBLICATION_LAGS,
    )
    features_raw = add_cross_sectional_features(features_raw, columns=("vol_21d",))

    final_cols = list(FINAL_FEATURE_COLUMNS)
    features_by_symbol: dict[str, pd.DataFrame] = {}
    prices_by_symbol: dict[str, pd.DataFrame] = {}
    for sym in actual_symbols:
        feat = features_raw[sym]
        missing = [c for c in final_cols if c not in feat.columns]
        if missing:
            raise RuntimeError(f"feature frame for {sym!r} missing columns: {missing}")
        # Normalise feature (UTC) and price (NY) indices to a common naive-UTC
        # calendar so they align by instant — see _to_naive_utc.
        sliced = feat[final_cols].copy()
        sliced.index = _to_naive_utc(sliced.index)
        features_by_symbol[sym] = sliced
        px = prices_raw[sym].copy()
        px.index = _to_naive_utc(px.index)
        prices_by_symbol[sym] = px

    return features_by_symbol, prices_by_symbol


# ─── Deterministic baselines (no model fit — computed here so the notebook
#     stays checkpoint-only) ───────────────────────────────────────────────────


def _ewma_logvol(close: pd.Series, lam: float = EWMA_LAMBDA) -> pd.Series:
    """Causal EWMA daily-vol persistence forecast, as log-vol (T1/T2 baseline)."""
    r = close.pct_change()
    var = r.pow(2).ewm(alpha=1 - lam, adjust=False).mean()
    vol = np.sqrt(var)
    return np.log(vol.replace(0.0, np.nan))


def _align_series_to_frame(
    frame: pd.DataFrame, series_by_sym: Mapping[str, pd.Series]
) -> np.ndarray:
    """Align a per-symbol Series onto ``frame``'s (date, symbol) rows → 1-D array."""
    out = np.full(len(frame), np.nan)
    syms = frame["symbol"].to_numpy()
    for i, (dt, s) in enumerate(zip(frame.index, syms)):
        ser = series_by_sym.get(s)
        if ser is not None and dt in ser.index:
            out[i] = float(ser.loc[dt])
    return out


# ─── Panel construction + per-target alignment ───────────────────────────────


def _aligned_target_panel(
    target: str,
    features_by_symbol: dict[str, pd.DataFrame],
    prices_by_symbol: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series], dict[str, pd.DataFrame], int]:
    """Build NaN-free aligned (features, labels, prices) for *target*; return horizon.

    Single-``dropna`` + intersection discipline per symbol, exactly as the slice
    notebook and the prediction collector require (identical feature/label index).
    """
    horizon = TARGET_CATALOG[target].horizon_bars
    feats: dict[str, pd.DataFrame] = {}
    labels: dict[str, pd.Series] = {}
    prices: dict[str, pd.DataFrame] = {}
    for sym, feat in features_by_symbol.items():
        X = feat.dropna()
        y = make_target_labels(target, prices_by_symbol[sym]["close"]).series.dropna()
        common = X.index.intersection(y.index)
        if len(common) == 0:
            logger.warning("symbol %s: no overlapping non-NaN bars for %s — dropping", sym, target)
            continue
        feats[sym] = X.loc[common]
        labels[sym] = y.loc[common]
        prices[sym] = prices_by_symbol[sym].loc[common]
    if not feats:
        raise RuntimeError(f"no symbols survived alignment for target {target!r}")
    return feats, labels, prices, horizon


# ─── Config-hash + git metadata (mirrors run_phase4a_arms) ────────────────────


def _git_sha() -> str:
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
    target: str,
    label_horizon: int,
    walk_forward: Mapping[str, int] = WALK_FORWARD,
) -> dict[str, Any]:
    """Deterministic run-config dict that gets hashed (insertion-order stable)."""
    return {
        "milestone": "B1-M3",
        "target": target,
        "label_horizon": label_horizon,
        "feature_columns": list(FINAL_FEATURE_COLUMNS),
        "walk_forward": dict(walk_forward),
        "sim_kwargs": dict(SIM_KWARGS),
        "gbm_params": {
            "n_iter": GBM_N_ITER,
            "n_splits": GBM_N_SPLITS,
            "random_state": GBM_RANDOM_STATE,
            "label_horizon": label_horizon,
        },
        "arima_order": list(ARIMA_ORDER),
        "ewma_lambda": EWMA_LAMBDA,
        "sentiment_lookback_days": SENTIMENT_LOOKBACK_DAYS,
        "fred_publication_lags": dict(FRED_PUBLICATION_LAGS),
    }


def _hash_config(cfg: Mapping[str, Any]) -> str:
    return hashlib.sha256(pickle.dumps(cfg, protocol=4)).hexdigest()


# ─── Smoke-mode synthetic panel ──────────────────────────────────────────────


def _make_smoke_panel() -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """5-symbol × ~900-bar synthetic panel — plumbing test only (no real signal).

    Longer than the Phase 4A smoke panel because the 21-bar targets need a few
    walk-forward folds with 252-bar train windows to produce any OOS rows.
    """
    rng = np.random.default_rng(11)
    dates = pd.bdate_range("2021-01-04", periods=900)
    features_by_symbol: dict[str, pd.DataFrame] = {}
    prices_by_symbol: dict[str, pd.DataFrame] = {}
    for sym in ("AAA", "BBB", "CCC", "DDD", "EEE"):
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
        features_by_symbol[sym] = pd.DataFrame(
            rng.standard_normal((len(dates), len(FINAL_FEATURE_COLUMNS))),
            index=dates,
            columns=list(FINAL_FEATURE_COLUMNS),
        )
    return features_by_symbol, prices_by_symbol


# ─── Per-target fit + checkpoint ──────────────────────────────────────────────


def _collect_predictions(
    model: object,
    feats: dict[str, pd.DataFrame],
    labels: dict[str, pd.Series],
    horizon: int,
    wf: Mapping[str, int],
) -> pd.DataFrame:
    """Thin wrapper pinning the walk-forward kwargs for the prediction collector."""
    return collect_oos_predictions(
        model,
        feats,
        labels,
        train_window=wf["train_window"],
        test_window=wf["test_window"],
        step=wf["step"],
        label_horizon=horizon,
        embargo=wf["embargo"],
    )


def _align_other_frame(base: pd.DataFrame, other: pd.DataFrame) -> np.ndarray:
    """Take ``other['y_pred']`` aligned onto ``base``'s (date, symbol) rows.

    ``collect_oos_predictions`` is deterministic over the same panel/splits, so the
    two frames share (date, symbol) rows; we assert that and read y_pred directly,
    falling back to a (date, symbol) reindex if a row set ever diverges.
    """
    if base.index.equals(other.index) and np.array_equal(
        base["symbol"].to_numpy(), other["symbol"].to_numpy()
    ):
        return other["y_pred"].to_numpy(dtype=float)
    o = other.assign(_d=other.index).set_index(["_d", "symbol"])["y_pred"]
    keys = pd.MultiIndex.from_arrays([base.index, base["symbol"].to_numpy()])
    return o.reindex(keys).to_numpy(dtype=float)


def _arima_sharpe_arm(
    feats: dict[str, pd.DataFrame],
    prices: dict[str, pd.DataFrame],
    horizon: int,
    wf: Mapping[str, int],
) -> pd.Series:
    """ARIMA directional Sharpe baseline: AR(1) on forward returns → sign → simulate.

    Mirrors nb10's ``score_directional`` ARIMA control: the label here is the
    forward *return* (not the 0/1 direction), routed through the harness so the
    Sharpe is commensurable with the Phase 4A ARIMA control.
    """
    feats_r: dict[str, pd.DataFrame] = {}
    lab_r: dict[str, pd.Series] = {}
    px_r: dict[str, pd.DataFrame] = {}
    for s, X in feats.items():
        fwd = prices[s]["close"].shift(-horizon) / prices[s]["close"] - 1.0
        common = X.index.intersection(fwd.dropna().index)
        if len(common) == 0:
            continue
        feats_r[s] = X.loc[common]
        lab_r[s] = fwd.loc[common]
        px_r[s] = prices[s].loc[common]
    if not feats_r:
        return pd.Series(dtype=float)
    result = run_portfolio_backtest(
        ARIMABaseline(order=ARIMA_ORDER),
        feats_r,
        lab_r,
        px_r,
        train_window=wf["train_window"],
        test_window=wf["test_window"],
        step=wf["step"],
        label_horizon=horizon,
        embargo=wf["embargo"],
        **SIM_KWARGS,
    )
    return result.oos_returns


def _build_predictions_frame(
    target: str,
    feats: dict[str, pd.DataFrame],
    labels: dict[str, pd.Series],
    prices: dict[str, pd.DataFrame],
    horizon: int,
    wf: Mapping[str, int],
    gbm_kwargs: Mapping[str, int] | None = None,
) -> tuple[pd.DataFrame, dict[str, pd.Series], float]:
    """Fit the GBM (+ ARIMA where the baseline needs it) and assemble checkpoints.

    Returns ``(predictions_frame, return_series_by_arm, drawdown_base_rate)``.
    ``return_series_by_arm`` is ``{"gbm": Series, "arima": Series}`` for directional
    targets, else ``{}``. ``drawdown_base_rate`` is the P(DD>5%) base rate (NaN for
    non-drawdown targets — climatology only matters for T1). ``gbm_kwargs`` defaults
    to the pinned production budget; ``--smoke`` passes the reduced budget.
    """
    gk = dict(gbm_kwargs) if gbm_kwargs is not None else {
        "n_iter": GBM_N_ITER,
        "n_splits": GBM_N_SPLITS,
        "random_state": GBM_RANDOM_STATE,
    }
    gbm = GBMModel(label_horizon=horizon, **gk)
    gbm_frame = _collect_predictions(gbm, feats, labels, horizon, wf)
    frame = gbm_frame.rename(columns={"y_pred": "gbm_pred"})[
        ["symbol", "y_true", "gbm_pred"]
    ].copy()

    return_series: dict[str, pd.Series] = {}
    base_rate = float("nan")

    if target == "drawdown_21d":
        # T1 baseline: climatology base-rate (scalar, in metadata) + EWMA vol-implied
        # DD proxy (higher vol → higher DD-probability rank). vol_proxy is a SCORE,
        # not a probability — AUC is rank-based so a monotone score suffices.
        base_rate = float(np.nanmean(frame["y_true"].to_numpy()))
        ewma = {s: _ewma_logvol(prices[s]["close"]) for s in feats}
        frame["vol_proxy"] = _align_series_to_frame(gbm_frame, ewma)

    elif target == "realized_vol_21d":
        # T2 baseline: EWMA(0.94) log-vol + ARIMA-on-log-vol (better-of). Both are
        # the MAE baseline; the gate picks the better per regime.
        ewma = {s: _ewma_logvol(prices[s]["close"]) for s in feats}
        frame["ewma_pred"] = _align_series_to_frame(gbm_frame, ewma)
        arima_frame = _collect_predictions(ARIMABaseline(order=ARIMA_ORDER), feats, labels, horizon, wf)
        frame["arima_pred"] = _align_other_frame(gbm_frame, arima_frame)

    else:  # directional_5d / directional_21d
        # T3/T4: ARIMA score is the AUC baseline; the tradeable Sharpe arm routes
        # GBM sign(pred−0.5) and an ARIMA forward-return forecast through the simulator.
        arima_frame = _collect_predictions(ARIMABaseline(order=ARIMA_ORDER), feats, labels, horizon, wf)
        frame["arima_pred"] = _align_other_frame(gbm_frame, arima_frame)
        return_series["gbm"] = simulate_signal_returns(gbm_frame, prices, threshold=0.5, **SIM_KWARGS)
        return_series["arima"] = _arima_sharpe_arm(feats, prices, horizon, wf)

    return frame, return_series, base_rate


# ─── Output writers ──────────────────────────────────────────────────────────


def _write_outputs(
    target_dir: Path,
    frame: pd.DataFrame,
    return_series: Mapping[str, pd.Series],
    metadata: dict[str, Any],
) -> None:
    """Write predictions.parquet, any return-arm parquets, and metadata.json."""
    target_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target_dir / "predictions.parquet")
    for arm, series in return_series.items():
        series.to_frame(name=f"{arm}_returns").to_parquet(
            target_dir / f"returns_{arm}.parquet"
        )
    with (target_dir / "metadata.json").open("w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2, sort_keys=False, default=str)


# ─── Per-target dispatch ──────────────────────────────────────────────────────


def _run_target(
    target: str,
    output_dir: Path,
    smoke: bool,
    force: bool,
    ledger_meta: Mapping[str, Any] | None = None,
) -> int:
    """Execute one target end-to-end. Return 0 on success/skip, nonzero on error.

    Idempotent: a pre-existing ``metadata.json`` short-circuits the fit. With
    ``--log-ledger`` set, a skipped (already-checkpointed) target still logs its
    trial from the existing metadata — logging is decoupled from fitting so the
    ledger can be written post-gate without re-fitting (METHODOLOGY §7, §12).
    """
    target_subdir = f"smoke_{target}" if smoke else target
    target_dir = output_dir / target_subdir
    meta_path = target_dir / "metadata.json"

    if meta_path.exists() and not force:
        logger.info("checkpoint present at %s — skipping fit", meta_path)
        if ledger_meta is not None:
            _maybe_log_ledger(target, target_dir, smoke, ledger_meta)
        return 0

    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    logger.info("target=%s smoke=%s starting at %s", target, smoke, started.isoformat())

    wf = (
        {"train_window": 252, "test_window": 63, "step": 63, "embargo": 3}
        if smoke
        else WALK_FORWARD
    )

    if smoke:
        features_by_symbol, prices_by_symbol = _make_smoke_panel()
    else:
        sentiment_df = _load_sentiment_df()
        features_by_symbol, prices_by_symbol = _build_full_panel(
            settings.equity_universe, sentiment_df
        )

    feats, labels, prices, horizon = _aligned_target_panel(
        target, features_by_symbol, prices_by_symbol
    )
    frame, return_series, base_rate = _build_predictions_frame(
        target, feats, labels, prices, horizon, wf,
        gbm_kwargs=GBM_SMOKE_KWARGS if smoke else None,
    )

    elapsed = time.monotonic() - t0
    finished = datetime.now(timezone.utc)
    cfg = _build_run_config(target, horizon, walk_forward=wf)

    sharpe_summary = {
        arm: (float(compute_metrics(s)["sharpe"]) if len(s) else float("nan"))
        for arm, s in return_series.items()
    }
    metadata: dict[str, Any] = {
        "target": target,
        "milestone": "B1-M3",
        "smoke": smoke,
        "git_sha": _git_sha(),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "elapsed_seconds": elapsed,
        "config_hash": _hash_config(cfg),
        "run_config": cfg,
        "invariant_parity_audit": INVARIANT_PARITY_AUDIT,
        "n_symbols_in_panel": len(feats),
        "symbols": sorted(feats.keys()),
        "n_oos_rows": int(len(frame)),
        "n_oos_dates": int(frame.index.nunique()),
        "oos_start": str(frame.index.min()) if len(frame) else None,
        "oos_end": str(frame.index.max()) if len(frame) else None,
        "label_horizon": horizon,
        "has_sharpe_arm": _HAS_SHARPE_ARM[target],
        "drawdown_base_rate": base_rate,
        "sharpe_arm_aggregate": sharpe_summary,
        "n_self_comparisons": N_SELF_COMPARISONS,
    }

    _write_outputs(target_dir, frame, return_series, metadata)
    logger.info(
        "target=%s wrote %d OOS rows (%d dates) horizon=%d elapsed=%.1fs sharpe=%s",
        target,
        metadata["n_oos_rows"],
        metadata["n_oos_dates"],
        horizon,
        elapsed,
        sharpe_summary or "n/a",
    )

    if ledger_meta is not None:
        _maybe_log_ledger(target, target_dir, smoke, ledger_meta)
    return 0


def _maybe_log_ledger(
    target: str,
    target_dir: Path,
    smoke: bool,
    ledger_meta: Mapping[str, Any],
) -> None:
    """Append this target's trial to the ledger from its metadata.json (idempotent).

    Smoke runs never log (synthetic data). Idempotent by ``config_hash``, so logging
    a target whose trial is already recorded is a no-op — safe to call post-gate.
    """
    if smoke:
        logger.info("target=%s smoke run — NOT logging synthetic trial", target)
        return
    meta_path = target_dir / "metadata.json"
    entry = record_run(
        meta_path,
        artifacts=[f"{target_dir}/"],
        **ledger_meta,
    )
    if entry is None:
        logger.info("target=%s ledger entry skipped — config_hash already recorded", target)
    else:
        logger.info("target=%s recorded ledger entry %s", target, entry.id)


# ─── argparse + main ─────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for the B1-M3 runner."""
    parser = argparse.ArgumentParser(
        description="Project B1 Milestone 3 full-panel target-arm runner.",
    )
    parser.add_argument(
        "--target",
        required=True,
        choices=VALID_TARGETS,
        help="Which target arm to run on the full panel.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/b1"),
        help="Root directory for per-target checkpoint subdirectories.",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run on a 5-symbol synthetic panel — plumbing test only.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fit even if a checkpoint already exists for this target.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default INFO).",
    )
    # ── Ledger logging (METHODOLOGY §12) ─────────────────────────────────────
    # The verdict is supplied (not derived) because the gate runs AFTER the arm,
    # in the notebook. Log post-gate: a checkpointed target re-logs from metadata
    # without re-fitting (see _maybe_log_ledger).
    parser.add_argument(
        "--log-ledger",
        action="store_true",
        help="Append this target's trial to data/ledger.yaml (idempotent by config_hash).",
    )
    parser.add_argument("--ledger-prd", default="b1", help="Ledger entry prd field.")
    parser.add_argument("--ledger-milestone", default="B1-M3", help="Ledger entry milestone.")
    parser.add_argument(
        "--ledger-preregistration",
        default=".claude/prds/b1-target-reframing.prd.md#pre-committed-gate",
        help="Ledger entry preregistration path/anchor.",
    )
    parser.add_argument(
        "--ledger-n-comparisons",
        type=int,
        default=len(REQUIRED_REGIMES),
        help="Per-regime comparisons this target contributes to N (default 3).",
    )
    parser.add_argument(
        "--ledger-verdict",
        default="inconclusive",
        choices=["gate_passed", "gate_failed", "inconclusive"],
        help="Trial verdict (decided by the gate; supplied explicitly post-gate).",
    )
    parser.add_argument(
        "--ledger-agent",
        default="human",
        choices=["human", "R", "F", "M"],
        help="Who ran this trial (ledger agent field).",
    )
    parser.add_argument("--ledger-notes", default="", help="Free-text ledger notes.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    ledger_meta: dict[str, Any] | None = None
    if args.log_ledger:
        ledger_meta = {
            "prd": args.ledger_prd,
            "milestone": args.ledger_milestone,
            "preregistration": args.ledger_preregistration,
            "n_comparisons": args.ledger_n_comparisons,
            "verdict": args.ledger_verdict,
            "agent": args.ledger_agent,
            "notes": args.ledger_notes,
        }

    return _run_target(
        target=args.target,
        output_dir=args.output_dir,
        smoke=args.smoke,
        force=args.force,
        ledger_meta=ledger_meta,
    )


if __name__ == "__main__":
    sys.exit(main())
