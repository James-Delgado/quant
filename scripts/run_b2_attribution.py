"""Project B2 Milestone 2 — headless runner for the OOS-attribution validation.

Runs the B2 attribution method on the **5-symbol × 8-year slice** (the surface
the B2 PRD pins G1/G2 to, matching nb08) and checkpoints the result so the
verdict notebook (B-CLOSE / ``notebooks/15_project_b_closeout.ipynb``) and the
catalog population (B2-M3) consume checkpoints, never re-fitting (METHODOLOGY §7).

What it computes (the gate is ``backtest.attribution.b2_attribution_gate`` —
the source of truth, METHODOLOGY §2):

* **G1** — on the frozen **M6 25-column feature set**: the canonical per-fold
  ablation lift (``per_fold_ablation_attribution``, the reference) vs the cheap
  OOS permutation importance (``oos_permutation_importance``, under test). The
  gate scores their Spearman ρ (materiality ρ ≥ 0.50, permutation-test
  significance p < 0.05).
* **G2** — port reproducibility on the **7 nb08 candidate features**: the
  systematized leave-one-out ablation vs nb08's **exact published statistic** —
  the **best-regime add-one lift** (``max`` over the per-regime Sharpe deltas of
  ``+c`` vs the 17-base baseline; nb08 §5's ``best_regime_lift``). A faithful
  systematization should rank the 7 near-identically (ρ ≥ 0.90). The reference is
  reproduced via nb08's recipe verbatim (METHODOLOGY §1, B2-M2-G2-NB08): the
  add-one ablation on the 17 Phase-2.5 base features, **GBM seed 7** (nb08 §3, not
  the runner's seed-0 default), regimes tagged by ``DateRangeDetector`` and scored
  by ``feature_ablation_table`` — then ``max`` across regime columns. The full
  per-regime lift table is frozen to ``importances_g2_regime_lifts.parquet`` so the
  reference vector is auditable. (Everything else — slice, walk-forward, n_iter,
  sim costs — already matches nb08.) NOTE the two arms use different ablation
  *designs* — the systematized signal is **leave-one-out** (the B2 canonical OOS
  method, METHODOLOGY §14) while nb08's reference is **add-one** — so a low/negative
  real-slice G2 ρ (−0.43 at the B2-M2-G2-NB08 commit) reflects a design mismatch,
  not necessarily a port bug; whether G2 should instead be add-one-vs-add-one is the
  open follow-up B2-G2-DESIGN-MISMATCH.
* **G3** — reported (not gated): the IS importance (XGB gain) vs the OOS ablation
  lift on the 7 candidates, reproducing the ρ ≈ −0.074 "IS does not transfer"
  sanity floor. Declared proxy (§9): XGB **gain** importance stands in for nb08's
  mean-|SHAP|; both are IS-importance-family signals.

Checkpoint contract (per run dir ``data/b2/<run>/``)
----------------------------------------------------
* ``importances_g1.parquet`` — index ``feature`` (25 rows), columns
  ``ablation_importance, permutation_importance, permutation_se``.
* ``importances_g2.parquet`` — index ``feature`` (7 rows), columns
  ``systematized_loo, addone_reference`` (``addone_reference`` = nb08's best-regime
  add-one lift).
* ``importances_g2_regime_lifts.parquet`` — the frozen nb08 reference vector: the
  full per-regime add-one lift table (rows ``baseline`` + ``+<candidate>``, columns
  ``aggregate`` + regime labels + ``n_bars``) whose row-wise regime max is
  ``addone_reference`` (auditable, B2-M2-G2-NB08).
* ``importances_g3.parquet`` — index ``feature`` (7 rows), columns
  ``is_gain, oos_ablation``.
* ``gate.json`` — the full ``b2_attribution_gate(...)`` dict (verbatim verdict).
* ``metadata.json`` — config hash, timings, symbols, fold/row counts, the pinned
  config + the declared deviations above (METHODOLOGY §8 — invariants in code).

Pre-committed protocol (frozen BEFORE any run; do not change based on results)
------------------------------------------------------------------------------
  1. Slice = ``DEMO_SYMBOLS`` × [``DEMO_START``, ``DEMO_END``] — the nb08 surface.
  2. Feature set = the frozen 25-column M6 set (``FINAL_FEATURE_COLUMNS``) for G1;
     the 7 ``CANDIDATES`` for G2/G3.
  3. Label = signed forward return (``generate_labels(close, horizon=1)``) — the
     M2/M6 default; ``sign(pred)`` is the trade signal (harness convention).
  4. Walk-forward = nb02/nb04 convention (train 504, test 63, step 63, embargo 3).
  5. GBM = RandomizedSearchCV n_iter=10 (the nb08 *preview* budget — B2 validates a
     method, not an edge), n_splits=3, seed=0 — EXCEPT the G2 best-regime add-one
     reference, which pins nb08's seed=7 (``NB08_GBM_RANDOM_STATE``) to reproduce
     nb08's published lifts exactly.
  6. Gate thresholds are the pinned defaults of ``b2_attribution_gate`` (ρ ≥ 0.50,
     p < 0.05, ρ ≥ 0.90; n_permutations=10,000) — the runner adds none.
  7. Ledger: ``n_comparisons = 1`` (the single validated method — OOS permutation;
     ablation is the reference, not a tested claim) per the B2 PRD.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import pickle
import subprocess
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quant.backtest.ablation import make_add_one_sets, run_feature_ablation
from quant.backtest.attribution import (
    DEFAULT_N_REPEATS,
    b2_attribution_gate,
    oos_permutation_importance,
    per_fold_ablation_attribution,
)
from quant.backtest.regimes import DateRangeDetector, tag_regimes
from quant.backtest.report import feature_ablation_table
from quant.features.cross_sectional import add_cross_sectional_features
from quant.features.engineering import FRED_PUBLICATION_LAGS, build_features
from quant.features.labels import generate_labels
from quant.ledger import record_run
from quant.models.gbm import GBMModel
from quant.storage import catalog, lake

logger = logging.getLogger(__name__)

# ─── Pinned slice + protocol constants ────────────────────────────────────────

DEMO_SYMBOLS: tuple[str, ...] = ("AAPL", "MSFT", "JPM", "JNJ", "SPY")
DEMO_START: str = "2018-01-02"
DEMO_END: str = "2026-04-21"
"""The 5-symbol × 8-year slice — identical to nb08/nb10 (the G2 reproduction surface)."""

WALK_FORWARD: dict[str, int] = {
    "train_window": 504, "test_window": 63, "step": 63, "embargo": 3,
}
SIM_KWARGS: dict[str, float] = {
    "initial_capital": 100_000.0, "commission_per_share": 0.005, "slippage_bps": 5.0,
}
LABEL_HORIZON: int = 1
GBM_N_ITER: int = 10          # nb08 preview budget — B2 validates a method, not an edge
GBM_N_SPLITS: int = 3
GBM_RANDOM_STATE: int = 0
NB08_GBM_RANDOM_STATE: int = 7  # nb08 §3 published-lift seed — the G2 best-regime reference (B2-M2-G2-NB08)
GBM_SMOKE_KWARGS: dict[str, int] = {"n_iter": 2, "n_splits": 2, "random_state": 0}
SENTIMENT_LOOKBACK_DAYS: int = 30
N_REPEATS: int = DEFAULT_N_REPEATS
SEED: int = 0
N_COMPARISONS: int = 1         # the single validated method (B2 PRD ledger discipline)

# 17 Phase-2.5 base features — the add-one baseline for the G2 reference.
BASE_FEATURES_17: tuple[str, ...] = (
    "ret_1d", "ret_5d", "ret_21d", "vol_21d", "vol_63d", "mom_21d", "rsi_14",
    "log_volume", "ret_252d", "ret_126d", "ma200_ratio", "ma50_ratio",
    "volume_ratio", "DGS10", "DFF", "VIXCLS", "yield_curve",
)

# The frozen 25-column M6 feature set (G1 surface) — mirrors
# run_b1_arms.FINAL_FEATURE_COLUMNS / run_phase4a_arms.FINAL_FEATURE_COLUMNS.
FINAL_FEATURE_COLUMNS: tuple[str, ...] = (
    *BASE_FEATURES_17,
    "vix_regime", "curve_inverted", "vol_regime_ratio", "trend_regime",  # 4 regime (M3)
    "sentiment_score", "doc_count", "has_coverage",                       # 3 sentiment
    "xs_rank_vol_21d",                                                    # 1 M3 xs survivor
)

# The 7 nb08 candidate features (G2/G3 surface).
CANDIDATES: tuple[str, ...] = (
    "xs_rank_ret_21d", "xs_rank_ret_252d", "xs_rank_vol_21d",
    "vix_regime", "curve_inverted", "vol_regime_ratio", "trend_regime",
)
# Cross-sectional columns needed so the 7 candidates + the M6 set all exist.
XS_COLUMNS: tuple[str, ...] = ("ret_21d", "ret_252d", "vol_21d")

DECLARED_DEVIATIONS: str = (
    "G2 reference is nb08's EXACT statistic — the best-regime add-one lift on the "
    "17-base baseline at GBM seed 7 (nb08 §3/§5), recomputed verbatim and frozen to "
    "importances_g2_regime_lifts.parquet (B2-M2-G2-NB08 closed; no longer the "
    "aggregate-OOS-Sharpe proxy). G3 IS signal is XGB gain importance as a proxy "
    "for nb08's mean-|SHAP| (both IS-importance family, reported not gated)."
)


# ─── Data loading (mirrors run_b1_arms) ───────────────────────────────────────


def _load_prices_panel(symbols: Sequence[str]) -> dict[str, pd.DataFrame]:
    """Load adjusted OHLCV from the lake, sliced to [DEMO_START, DEMO_END]."""
    syms_sql = ", ".join(f"'{s}'" for s in symbols)
    eq = catalog.query(
        f"""
        SELECT symbol, timestamp, open, high, low, close, adjClose, volume
        FROM {catalog.table("equity_eod_tiingo")}
        WHERE symbol IN ({syms_sql})
          AND timestamp >= '{DEMO_START}' AND timestamp <= '{DEMO_END}'
        ORDER BY symbol, timestamp
        """
    )
    eq["timestamp"] = pd.to_datetime(eq["timestamp"])
    eq = eq.set_index("timestamp")
    prices: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        sub = eq[eq["symbol"] == sym][["open", "high", "low", "adjClose", "volume"]].copy()
        if sub.empty:
            logger.warning("symbol %s missing from lake — skipping", sym)
            continue
        prices[sym] = sub.rename(columns={"adjClose": "close"}).sort_index().dropna()
    if not prices:
        raise RuntimeError("no symbols had usable bars in the lake")
    return prices


def _to_naive_utc(idx: pd.Index) -> pd.DatetimeIndex:
    """tz-naive UTC, preserving the instant (the run_b1_arms alignment fix)."""
    idx = pd.DatetimeIndex(idx)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return idx


def _build_slice_panel() -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """Build the feature panel (all M6 + 7-candidate columns) for the slice."""
    prices_raw = _load_prices_panel(DEMO_SYMBOLS)
    syms = list(prices_raw.keys())
    sent = lake.read_processed("sentiment_scored")
    feats_raw = build_features(
        syms, prices_raw,
        sentiment_df=sent if not sent.empty else None,
        sentiment_lookback_days=SENTIMENT_LOOKBACK_DAYS,
        fred_publication_lags=FRED_PUBLICATION_LAGS,
    )
    feats_raw = add_cross_sectional_features(feats_raw, columns=XS_COLUMNS)

    needed = sorted(set(FINAL_FEATURE_COLUMNS) | set(CANDIDATES) | set(BASE_FEATURES_17))
    features: dict[str, pd.DataFrame] = {}
    prices: dict[str, pd.DataFrame] = {}
    for sym in syms:
        feat = feats_raw[sym]
        missing = [c for c in needed if c not in feat.columns]
        if missing:
            raise RuntimeError(f"{sym!r} feature frame missing columns: {missing}")
        sliced = feat[needed].copy()
        sliced.index = _to_naive_utc(sliced.index)
        features[sym] = sliced
        px = prices_raw[sym].copy()
        px.index = _to_naive_utc(px.index)
        prices[sym] = px
    return features, prices


def _aligned_panel(
    features_by_symbol: dict[str, pd.DataFrame],
    prices_by_symbol: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series], dict[str, pd.DataFrame]]:
    """NaN-free (features, signed-return labels, prices) per symbol (single dropna)."""
    feats: dict[str, pd.DataFrame] = {}
    labels: dict[str, pd.Series] = {}
    prices: dict[str, pd.DataFrame] = {}
    for sym, feat in features_by_symbol.items():
        X = feat.dropna()
        y = generate_labels(prices_by_symbol[sym]["close"], horizon=LABEL_HORIZON).series.dropna()
        common = X.index.intersection(y.index)
        if len(common) == 0:
            logger.warning("symbol %s: no overlapping non-NaN bars — dropping", sym)
            continue
        feats[sym] = X.loc[common]
        labels[sym] = y.loc[common]
        prices[sym] = prices_by_symbol[sym].loc[common]
    if not feats:
        raise RuntimeError("no symbols survived alignment")
    return feats, labels, prices


# ─── Smoke panel ──────────────────────────────────────────────────────────────


def _make_smoke_panel() -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series], dict[str, pd.DataFrame]]:
    """Synthetic 3-symbol panel with one genuine driver — plumbing test only."""
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2021-01-04", periods=600)
    cols = sorted(set(FINAL_FEATURE_COLUMNS) | set(CANDIDATES) | set(BASE_FEATURES_17))
    feats, labels, prices = {}, {}, {}
    for sym in ("AAA", "BBB", "CCC"):
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(dates))))
        px = pd.DataFrame(
            {"open": close * (1 + rng.uniform(-0.002, 0.002, len(dates))),
             "high": close * 1.003, "low": close * 0.997, "close": close,
             "volume": np.full(len(dates), 1e6)},
            index=dates,
        )
        earned = px["open"].shift(-2) / px["open"].shift(-1) - 1.0
        f = pd.DataFrame(rng.standard_normal((len(dates), len(cols))), index=dates, columns=cols)
        f["xs_rank_vol_21d"] = earned + rng.normal(0, 0.002, len(dates))  # a real driver
        X = f.dropna()
        y = earned.dropna()
        cm = X.index.intersection(y.index)
        feats[sym], labels[sym], prices[sym] = X.loc[cm], y.loc[cm], px.loc[cm]
    return feats, labels, prices


# ─── Attribution arms ─────────────────────────────────────────────────────────


def _gbm(label_horizon: int, smoke: bool) -> GBMModel:
    gk = GBM_SMOKE_KWARGS if smoke else {
        "n_iter": GBM_N_ITER, "n_splits": GBM_N_SPLITS, "random_state": GBM_RANDOM_STATE,
    }
    return GBMModel(label_horizon=label_horizon, **gk)


def _nb08_addone_gbm(smoke: bool) -> GBMModel:
    """The GBM nb08 §3 used for its published add-one lifts (seed 7 — *not* the runner seed-0).

    The G2 reference must reproduce nb08's *exact* published numbers, so the
    reference arm pins nb08's seed; the rest of the runner (G1, systematized LOO)
    stays at the runner's seed-0 convention.
    """
    if smoke:
        return GBMModel(label_horizon=LABEL_HORIZON, **GBM_SMOKE_KWARGS)
    return GBMModel(
        label_horizon=LABEL_HORIZON, n_iter=GBM_N_ITER, n_splits=GBM_N_SPLITS,
        random_state=NB08_GBM_RANDOM_STATE,
    )


def _best_regime_from_lift_table(
    lift_table: pd.DataFrame, candidates: Sequence[str]
) -> pd.Series:
    """nb08 §5's published per-candidate statistic: the best-regime add-one lift.

    ``lift_table`` is ``feature_ablation_table`` output — one row per feature set
    (``baseline`` + ``+<candidate>``), columns ``aggregate`` + each regime label +
    ``n_bars``. For each candidate this returns ``max`` over the **regime** columns
    of its ``+c`` row (``aggregate``/``n_bars`` excluded; NaN regimes skipped), i.e.
    ``lift_table.loc['+c', regime_cols].max()`` — byte-for-byte nb08 §5.
    """
    regime_cols = [c for c in lift_table.columns if c not in ("aggregate", "n_bars")]
    return pd.Series(
        {c: float(lift_table.loc[f"+{c}", regime_cols].max()) for c in candidates},
        name="addone_reference",
    )


def _addone_reference(
    feats: dict[str, pd.DataFrame],
    labels: dict[str, pd.Series],
    prices: dict[str, pd.DataFrame],
    smoke: bool,
) -> tuple[pd.Series, pd.DataFrame]:
    """nb08's exact published reference: the best-regime add-one lift per candidate.

    Reproduces nb08 §4–§5 verbatim: run the add-one ablation (17-base baseline +
    each candidate) at nb08's seed, tag the OOS bars by the ``DateRangeDetector``
    macro-era axis, score per-regime Sharpe deltas with ``feature_ablation_table``,
    and take the best-regime lift per candidate. Returns ``(best_regime_lift,
    lift_table)`` — the second is frozen as the auditable reference vector.
    """
    sets = make_add_one_sets(BASE_FEATURES_17, CANDIDATES)
    results = run_feature_ablation(
        sets, _nb08_addone_gbm(smoke),
        features_by_symbol=feats, labels_by_symbol=labels, prices_by_symbol=prices,
        label_horizon=LABEL_HORIZON, **WALK_FORWARD, **SIM_KWARGS,
    )
    era_labels = tag_regimes(results["baseline"].oos_returns.index, DateRangeDetector())
    lift_table = feature_ablation_table(results, "baseline", era_labels)
    return _best_regime_from_lift_table(lift_table, CANDIDATES), lift_table


def _is_gain_importance(
    feats: dict[str, pd.DataFrame],
    labels: dict[str, pd.Series],
) -> pd.Series:
    """IS XGB-gain importance on the 7 candidates (one whole-slice fit — the G3 IS arm)."""
    X = np.vstack([feats[s][list(CANDIDATES)].to_numpy() for s in feats])
    y = np.concatenate([labels[s].to_numpy() for s in labels])
    gbm = GBMModel(label_horizon=LABEL_HORIZON, n_iter=GBM_N_ITER, n_splits=GBM_N_SPLITS,
                   random_state=GBM_RANDOM_STATE)
    gbm.fit(X, y)
    return pd.Series(dict(zip(CANDIDATES, gbm.feature_importances_)), name="is_gain")


def _run(output_dir: Path, smoke: bool, force: bool, log_ledger: bool) -> int:
    """Execute the full attribution validation end-to-end; checkpoint + verdict."""
    run_subdir = "smoke" if smoke else "slice"
    run_dir = output_dir / run_subdir
    meta_path = run_dir / "metadata.json"
    if meta_path.exists() and not force:
        logger.info("checkpoint present at %s — skipping (use --force to rerun)", meta_path)
        if log_ledger and not smoke:
            _maybe_log_ledger(run_dir)
        return 0

    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    logger.info("B2 attribution run (smoke=%s) starting %s", smoke, started.isoformat())

    if smoke:
        feats, labels, prices = _make_smoke_panel()
    else:
        feats_raw, prices_raw = _build_slice_panel()
        feats, labels, prices = _aligned_panel(feats_raw, prices_raw)

    # ── G1 — 25-col ablation reference vs permutation proxy ────────────────────
    logger.info("G1: per-fold ablation (LOO, %d cols)", len(FINAL_FEATURE_COLUMNS))
    abl = per_fold_ablation_attribution(
        _gbm(LABEL_HORIZON, smoke), feats, labels, prices, FINAL_FEATURE_COLUMNS,
        label_horizon=LABEL_HORIZON, **WALK_FORWARD, **SIM_KWARGS,
    )
    logger.info("G1: OOS permutation importance (%d repeats)", N_REPEATS)
    perm = oos_permutation_importance(
        _gbm(LABEL_HORIZON, smoke), feats, labels, prices, FINAL_FEATURE_COLUMNS,
        n_repeats=N_REPEATS, seed=SEED, label_horizon=LABEL_HORIZON,
        **WALK_FORWARD, **SIM_KWARGS,
    )

    # ── G2 — port reproducibility on the 7 candidates ─────────────────────────
    logger.info("G2: systematized LOO + nb08 best-regime add-one reference (7 candidates)")
    sys_loo = per_fold_ablation_attribution(
        _gbm(LABEL_HORIZON, smoke), feats, labels, prices, CANDIDATES,
        label_horizon=LABEL_HORIZON, **WALK_FORWARD, **SIM_KWARGS,
    )
    addone, addone_regime_lifts = _addone_reference(feats, labels, prices, smoke)

    # ── G3 — IS-vs-OOS contrast (reported) ────────────────────────────────────
    logger.info("G3: IS gain importance vs OOS ablation (7 candidates)")
    is_gain = _is_gain_importance(feats, labels)

    # ── Verdict (the gate is the source of truth) ─────────────────────────────
    gate = b2_attribution_gate(
        perm.importance.to_dict(),
        abl.importance.to_dict(),
        reproduction=(sys_loo.importance.to_dict(), addone.to_dict()),
        shap_contrast=(is_gain.to_dict(), sys_loo.importance.to_dict()),
        seed=SEED,
    )
    verdict = "gate_passed" if gate["gate_passed"] else "gate_failed"
    logger.info(
        "VERDICT=%s | G1 ρ=%.3f (p=%.4f) | G2 ρ=%s | G3 ρ=%s",
        verdict, gate["g1_rho"], gate["g1_p_value"],
        None if gate["g2_rho"] is None else round(gate["g2_rho"], 3),
        None if gate["g3_rho"] is None else round(gate["g3_rho"], 3),
    )

    elapsed = time.monotonic() - t0
    finished = datetime.now(timezone.utc)
    cfg = _build_run_config(smoke)
    metadata: dict[str, Any] = {
        "milestone": "B2-M2",
        "smoke": smoke,
        "git_sha": _git_sha(),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "elapsed_seconds": elapsed,
        "config_hash": _hash_config(cfg),
        "run_config": cfg,
        "declared_deviations": DECLARED_DEVIATIONS,
        "symbols": sorted(feats.keys()),
        "n_symbols": len(feats),
        "n_folds_permutation": perm.n_folds,
        "verdict": verdict,
        "n_comparisons": N_COMPARISONS,
    }
    _write_outputs(
        run_dir, abl, perm, sys_loo, addone, addone_regime_lifts, is_gain, gate, metadata
    )
    logger.info("wrote checkpoint to %s (elapsed=%.1fs)", run_dir, elapsed)

    if log_ledger and not smoke:
        _maybe_log_ledger(run_dir)
    return 0


# ─── Output + config + ledger ─────────────────────────────────────────────────


def _write_outputs(
    run_dir, abl, perm, sys_loo, addone, addone_regime_lifts, is_gain, gate, metadata
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "ablation_importance": abl.importance,
        "permutation_importance": perm.importance,
        "permutation_se": perm.std_error,
    }).rename_axis("feature").to_parquet(run_dir / "importances_g1.parquet")
    pd.DataFrame({
        "systematized_loo": sys_loo.importance,
        "addone_reference": addone,
    }).rename_axis("feature").to_parquet(run_dir / "importances_g2.parquet")
    # Frozen nb08 reference vector: the full per-regime add-one lift table the
    # best-regime statistic is the row-wise max of (B2-M2-G2-NB08, auditable).
    addone_regime_lifts.rename_axis("feature_set").to_parquet(
        run_dir / "importances_g2_regime_lifts.parquet"
    )
    pd.DataFrame({
        "is_gain": is_gain,
        "oos_ablation": sys_loo.importance,
    }).rename_axis("feature").to_parquet(run_dir / "importances_g3.parquet")
    with (run_dir / "gate.json").open("w", encoding="utf-8") as fh:
        json.dump(gate, fh, indent=2, sort_keys=False, default=str)
    with (run_dir / "metadata.json").open("w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2, sort_keys=False, default=str)


def _build_run_config(smoke: bool) -> dict[str, Any]:
    return {
        "milestone": "B2-M2",
        "slice": {"symbols": list(DEMO_SYMBOLS), "start": DEMO_START, "end": DEMO_END},
        "feature_columns_g1": list(FINAL_FEATURE_COLUMNS),
        "candidates_g2g3": list(CANDIDATES),
        "addone_baseline": list(BASE_FEATURES_17),
        "g2_reference": {
            "statistic": "best_regime_addone_lift",  # nb08 §5 exact statistic
            "gbm_random_state": GBM_SMOKE_KWARGS["random_state"] if smoke else NB08_GBM_RANDOM_STATE,
            "regime_detector": "DateRangeDetector",
        },
        "label": {"scheme": "signed_returns", "horizon": LABEL_HORIZON},
        "walk_forward": dict(WALK_FORWARD),
        "sim_kwargs": dict(SIM_KWARGS),
        "gbm_params": GBM_SMOKE_KWARGS if smoke else {
            "n_iter": GBM_N_ITER, "n_splits": GBM_N_SPLITS, "random_state": GBM_RANDOM_STATE,
        },
        "n_repeats": N_REPEATS,
        "seed": SEED,
        "fred_publication_lags": dict(FRED_PUBLICATION_LAGS),
    }


def _hash_config(cfg: Mapping[str, Any]) -> str:
    return hashlib.sha256(pickle.dumps(cfg, protocol=4)).hexdigest()


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent, stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _maybe_log_ledger(run_dir: Path) -> None:
    """Append this run's trial to the ledger from its metadata.json (idempotent)."""
    entry = record_run(
        run_dir / "metadata.json",
        prd="b2",
        milestone="B2-M2",
        preregistration=".claude/prds/b2-oos-attribution.prd.md",
        n_comparisons=N_COMPARISONS,
        verdict=json.loads((run_dir / "metadata.json").read_text())["verdict"],
        artifacts=[f"{run_dir}/"],
        notes="B2-M2 OOS-attribution validation (G1 permutation vs ablation; G2 port; G3 IS-vs-OOS).",
    )
    if entry is None:
        logger.info("ledger entry skipped — config_hash already recorded")
    else:
        logger.info("recorded ledger entry %s", entry.id)


# ─── argparse + main ──────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Project B2 Milestone 2 OOS-attribution runner.")
    p.add_argument("--output-dir", default="data/b2", help="checkpoint root (default data/b2)")
    p.add_argument("--smoke", action="store_true", help="synthetic plumbing run (never logged)")
    p.add_argument("--force", action="store_true", help="recompute even if a checkpoint exists")
    p.add_argument("--log-ledger", action="store_true", help="append the trial to data/ledger.yaml")
    p.add_argument("--verbose", action="store_true", help="DEBUG logging")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return _run(Path(args.output_dir), args.smoke, args.force, args.log_ledger)


if __name__ == "__main__":
    raise SystemExit(main())
