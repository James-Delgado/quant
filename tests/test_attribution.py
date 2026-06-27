"""Tests for the B2 OOS feature-attribution toolkit (``backtest.attribution``).

The module ships three things: the canonical ablation reference
(``per_fold_ablation_attribution``), the cheap proxy under test
(``oos_permutation_importance``), and the pre-committed G1–G3 gate
(``b2_attribution_gate``). These tests cover:

* **Mechanics on a controlled signal.** A synthetic panel where one feature
  genuinely drives the label and the rest are noise — both ablation and
  permutation must rank the driver #1 (the integration that proves the
  attribution actually attributes), and they must *agree* (the G1 hypothesis,
  in miniature, on a fixture where agreement is guaranteed by construction).
* **Determinism / seed control** for the permutation proxy.
* **Contract validation** (empty/duplicate columns, key mismatch, no-fold).
* **The pinned gate** — G1 materiality (ρ ≥ 0.50), G1 significance (permutation
  test p < α), G2 reproducibility (ρ ≥ 0.90), the three-way conjunction, the
  ``reproduction=None`` → unverified-port → fail path, and the reported-only G3.

Models are tiny deterministic OLS stubs — no XGBoost — so the suite stays fast.
The purged walk-forward itself is leakage-tested in ``tests/test_walkforward.py``;
``attribution.py`` reuses it wholesale, so these tests target attribution logic,
not split correctness.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from quant.backtest.attribution import (
    ALPHA,
    N_PERMUTATIONS,
    REPRODUCTION_THRESHOLD,
    RHO_THRESHOLD,
    AblationImportance,
    PermutationImportance,
    b2_attribution_gate,
    oos_permutation_importance,
    per_fold_ablation_attribution,
)

# ─── fixtures + stubs ────────────────────────────────────────────────────────

N = 600
SYMBOLS = ["AAA", "BBB"]
TRAIN_W, TEST_W, STEP, EMBARGO = 200, 50, 50, 3
NOISE_COLS = ["n0", "n1", "n2", "n3"]
SIGNAL_COL = "signal"
FEATURE_COLS = [SIGNAL_COL, *NOISE_COLS]

# Zero costs in the fixtures: the attribution machinery (not the cost model) is
# under test, and daily sign-flips on a random walk would otherwise churn costs
# that swamp the controlled signal. The harness cost path is tested elsewhere.
SIM_KW: dict[str, float] = {"commission_per_share": 0.0, "slippage_bps": 0.0}


class OLSModel:
    """Ordinary-least-squares linear model (intercept + slope) — fast, deterministic.

    It naturally loads weight onto whichever column correlates with ``y``, so on a
    panel where one feature drives the label it learns to predict from that column
    — exactly what makes both ablation (remove the column) and permutation (shuffle
    the column) register a large degradation on the *driver* and ~none on noise.
    """

    def __init__(self) -> None:
        self.beta: np.ndarray | None = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> "OLSModel":
        A = np.column_stack([np.ones(len(X)), np.asarray(X, dtype=float)])
        self.beta, *_ = np.linalg.lstsq(A, np.asarray(y, dtype=float), rcond=None)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        assert self.beta is not None, "fit() before predict()"
        A = np.column_stack([np.ones(len(X)), np.asarray(X, dtype=float)])
        return A @ self.beta


def _make_prices(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    open_ = close * (1 + rng.uniform(-0.002, 0.002, n))
    high = np.maximum(close, open_) * (1 + rng.uniform(0.0, 0.005, n))
    low = np.minimum(close, open_) * (1 - rng.uniform(0.0, 0.005, n))
    dates = pd.bdate_range("2018-01-02", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": rng.integers(500_000, 2_000_000, n).astype(float)},
        index=dates,
    )


def _signal_panel():
    """Panel where ``signal`` is an informative proxy of the *simulated* return.

    A signal at bar ``t`` fills at ``t+1`` open and earns the ``t+1→t+2``
    open-to-open return (``simulator.simulate`` docstring), so the label here is
    exactly that earned return — ``open.shift(-2)/open.shift(-1) − 1`` — and
    ``signal`` is it plus modest noise; the four ``n*`` columns are pure noise.
    The OLS model can exploit ``signal`` only, so destroying it (ablation or
    permutation) collapses the simulated strategy's Sharpe while destroying any
    noise column does almost nothing. This is a *controlled attribution fixture*,
    not a leakage test (those live in test_walkforward.py).
    """
    features, labels, prices = {}, {}, {}
    for i, sym in enumerate(SYMBOLS):
        px = _make_prices(N, seed=i)
        earned = px["open"].shift(-2) / px["open"].shift(-1) - 1.0
        rng = np.random.default_rng(500 + i)
        feat = pd.DataFrame(index=px.index)
        feat[SIGNAL_COL] = earned + rng.normal(0.0, 0.002, N)  # informative
        for c in NOISE_COLS:
            feat[c] = rng.normal(0.0, 1.0, N)                  # pure noise
        X = feat.dropna()
        y = earned.dropna()
        common = X.index.intersection(y.index)
        features[sym] = X.loc[common]
        labels[sym] = y.loc[common]
        prices[sym] = px.loc[common]
    return features, labels, prices


def _ranking(values: dict[str, float]) -> dict[str, float]:
    """Identity helper — readability sugar for constructing per-feature score maps."""
    return dict(values)


# ─── per_fold_ablation_attribution ───────────────────────────────────────────


class TestAblationAttribution:
    def test_signal_feature_ranks_first(self):
        feats, labels, prices = _signal_panel()
        out = per_fold_ablation_attribution(
            OLSModel(), feats, labels, prices, FEATURE_COLS,
            train_window=TRAIN_W, test_window=TEST_W, step=STEP, embargo=EMBARGO,
            **SIM_KW,
        )
        assert isinstance(out, AblationImportance)
        assert set(out.importance.index) == set(FEATURE_COLS)
        # Removing the driver hurts most → highest importance, rank 1.
        assert out.importance.idxmax() == SIGNAL_COL
        assert out.ranks[SIGNAL_COL] == 1.0
        # Driver importance exceeds every noise feature's.
        for c in NOISE_COLS:
            assert out.importance[SIGNAL_COL] > out.importance[c]

    def test_baseline_metric_is_all_features(self):
        feats, labels, prices = _signal_panel()
        out = per_fold_ablation_attribution(
            OLSModel(), feats, labels, prices, FEATURE_COLS,
            train_window=TRAIN_W, test_window=TEST_W, step=STEP, embargo=EMBARGO,
            **SIM_KW,
        )
        assert out.baseline_metric == pytest.approx(
            out.results["all"].oos_metrics["sharpe"]
        )

    def test_empty_columns_raise(self):
        feats, labels, prices = _signal_panel()
        with pytest.raises(ValueError, match="non-empty"):
            per_fold_ablation_attribution(OLSModel(), feats, labels, prices, [])

    def test_duplicate_columns_raise(self):
        feats, labels, prices = _signal_panel()
        with pytest.raises(ValueError, match="duplicate"):
            per_fold_ablation_attribution(
                OLSModel(), feats, labels, prices, [SIGNAL_COL, SIGNAL_COL]
            )


# ─── oos_permutation_importance ──────────────────────────────────────────────


class TestPermutationImportance:
    def _run(self, n_repeats=5, seed=0):
        feats, labels, prices = _signal_panel()
        return oos_permutation_importance(
            OLSModel(), feats, labels, prices, FEATURE_COLS,
            n_repeats=n_repeats, seed=seed,
            train_window=TRAIN_W, test_window=TEST_W, step=STEP, embargo=EMBARGO,
            **SIM_KW,
        )

    def test_signal_feature_ranks_first(self):
        out = self._run()
        assert isinstance(out, PermutationImportance)
        assert set(out.importance.index) == set(FEATURE_COLS)
        # Permuting the driver destroys the signal → largest degradation.
        assert out.importance.idxmax() == SIGNAL_COL
        assert out.ranks[SIGNAL_COL] == 1.0
        for c in NOISE_COLS:
            assert out.importance[SIGNAL_COL] > out.importance[c]

    def test_deterministic_under_seed(self):
        a = self._run(seed=11)
        b = self._run(seed=11)
        pd.testing.assert_series_equal(a.importance, b.importance)
        pd.testing.assert_series_equal(a.std_error, b.std_error)

    def test_seed_changes_result(self):
        a = self._run(seed=1)
        b = self._run(seed=2)
        # Different permutation draws → not bit-identical (the ranking may still
        # agree, but the float importances differ).
        assert not np.allclose(a.importance.to_numpy(), b.importance.to_numpy())

    def test_std_error_reported_and_shaped(self):
        out = self._run(n_repeats=5)
        assert set(out.std_error.index) == set(FEATURE_COLS)
        assert (out.std_error >= 0).all()
        assert out.n_repeats == 5
        assert out.n_folds > 0

    def test_single_repeat_zero_std_error(self):
        out = self._run(n_repeats=1)
        assert (out.std_error == 0.0).all()

    def test_empty_columns_raise(self):
        feats, labels, prices = _signal_panel()
        with pytest.raises(ValueError, match="non-empty"):
            oos_permutation_importance(OLSModel(), feats, labels, prices, [])

    def test_mismatched_keys_raise(self):
        feats, labels, prices = _signal_panel()
        labels.pop("BBB")
        with pytest.raises(ValueError, match="share keys"):
            oos_permutation_importance(OLSModel(), feats, labels, prices, FEATURE_COLS)

    def test_bad_n_repeats_raises(self):
        feats, labels, prices = _signal_panel()
        with pytest.raises(ValueError, match="n_repeats"):
            oos_permutation_importance(
                OLSModel(), feats, labels, prices, FEATURE_COLS, n_repeats=0
            )

    def test_no_fold_raises(self):
        feats, labels, prices = _signal_panel()
        with pytest.raises(ValueError, match="no fold"):
            oos_permutation_importance(
                OLSModel(), feats, labels, prices, FEATURE_COLS,
                train_window=N + 100, test_window=TEST_W, step=STEP,
            )


class TestPermutationAgreesWithAblation:
    """The G1 hypothesis in miniature: on a fixture where one feature drives the
    label, the cheap proxy and the reference must agree (both rank the driver #1)."""

    def test_rankings_agree_on_driver(self):
        feats, labels, prices = _signal_panel()
        abl = per_fold_ablation_attribution(
            OLSModel(), feats, labels, prices, FEATURE_COLS,
            train_window=TRAIN_W, test_window=TEST_W, step=STEP, embargo=EMBARGO,
            **SIM_KW,
        )
        perm = oos_permutation_importance(
            OLSModel(), feats, labels, prices, FEATURE_COLS, n_repeats=5, seed=0,
            train_window=TRAIN_W, test_window=TEST_W, step=STEP, embargo=EMBARGO,
            **SIM_KW,
        )
        assert abl.ranks[SIGNAL_COL] == 1.0
        assert perm.ranks[SIGNAL_COL] == 1.0
        rho = float(
            stats.spearmanr(
                abl.importance[FEATURE_COLS].to_numpy(),
                perm.importance[FEATURE_COLS].to_numpy(),
            ).statistic
        )
        assert rho > 0.0  # positive agreement on a constructed-agreeable fixture


# ─── b2_attribution_gate ─────────────────────────────────────────────────────


# Seven features with distinct, monotone scores — a clean ranking surface.
_FEATS7 = [f"f{i}" for i in range(7)]
_PERFECT = _ranking({f: float(6 - i) for i, f in enumerate(_FEATS7)})
_REVERSED = _ranking({f: float(i) for i, f in enumerate(_FEATS7)})


class TestB2Gate:
    def test_perfect_agreement_passes_g1(self):
        out = b2_attribution_gate(
            _PERFECT, _PERFECT, reproduction=(_PERFECT, _PERFECT),
            n_permutations=2000, seed=0,
        )
        assert out["g1_rho"] == pytest.approx(1.0)
        assert out["g1_materiality_passed"] is True
        assert out["g1_significance_passed"] is True
        assert out["g1_p_value"] < ALPHA
        assert out["g2_rho"] == pytest.approx(1.0)
        assert out["g2_passed"] is True
        assert out["gate_passed"] is True

    def test_reversed_fails_materiality(self):
        out = b2_attribution_gate(
            _PERFECT, _REVERSED, reproduction=(_PERFECT, _PERFECT),
            n_permutations=2000, seed=0,
        )
        assert out["g1_rho"] == pytest.approx(-1.0)
        assert out["g1_materiality_passed"] is False
        assert out["gate_passed"] is False

    def test_weak_agreement_below_threshold_fails(self):
        # Scramble the middle/tail so ρ vs _PERFECT lands at ~0.43 — positive but
        # below the 0.50 materiality bar (verified against scipy.stats.spearmanr).
        weak = _ranking({"f0": 6.0, "f1": 5.0, "f2": 2.0, "f3": 1.0,
                         "f4": 0.0, "f5": 3.0, "f6": 4.0})
        out = b2_attribution_gate(
            _PERFECT, weak, reproduction=(_PERFECT, _PERFECT),
            n_permutations=2000, seed=0,
        )
        assert 0.0 < out["g1_rho"] < RHO_THRESHOLD
        assert out["g1_materiality_passed"] is False
        assert out["gate_passed"] is False

    def test_g2_below_reproduction_threshold_fails(self):
        # G1 perfect but the port does not reproduce nb08 (ρ = -1 over the 7).
        out = b2_attribution_gate(
            _PERFECT, _PERFECT, reproduction=(_PERFECT, _REVERSED),
            n_permutations=2000, seed=0,
        )
        assert out["g1_materiality_passed"] is True
        assert out["g2_passed"] is False
        assert out["gate_passed"] is False

    def test_reproduction_none_blocks_pass(self):
        out = b2_attribution_gate(_PERFECT, _PERFECT, n_permutations=2000, seed=0)
        assert out["g1_materiality_passed"] is True
        assert out["g1_significance_passed"] is True
        assert out["g2_rho"] is None
        assert out["g2_passed"] is None  # unverified port
        assert out["gate_passed"] is False

    def test_g3_reported_not_gated(self):
        # G3 high would be alarming (premise wrong) but must NOT affect the verdict.
        out = b2_attribution_gate(
            _PERFECT, _PERFECT, reproduction=(_PERFECT, _PERFECT),
            shap_contrast=(_PERFECT, _PERFECT), n_permutations=2000, seed=0,
        )
        assert out["g3_rho"] == pytest.approx(1.0)
        assert out["gate_passed"] is True  # G3 not in the conjunction

    def test_g3_reproduces_negative_contrast(self):
        out = b2_attribution_gate(
            _PERFECT, _PERFECT, reproduction=(_PERFECT, _PERFECT),
            shap_contrast=(_PERFECT, _REVERSED), n_permutations=2000, seed=0,
        )
        assert out["g3_rho"] == pytest.approx(-1.0)  # the ρ≈-0.074 sanity floor

    def test_too_few_features_raises(self):
        with pytest.raises(ValueError, match="common features"):
            b2_attribution_gate({"a": 1.0, "b": 2.0}, {"a": 1.0, "b": 2.0})

    def test_pinned_defaults(self):
        # The gate echoes the pinned thresholds (METHODOLOGY §1/§2) verbatim.
        out = b2_attribution_gate(
            _PERFECT, _PERFECT, reproduction=(_PERFECT, _PERFECT), n_permutations=2000,
        )
        assert out["rho_threshold"] == RHO_THRESHOLD == 0.50
        assert out["alpha"] == ALPHA == 0.05
        assert out["reproduction_threshold"] == REPRODUCTION_THRESHOLD == 0.90
        assert N_PERMUTATIONS == 10_000

    def test_significance_uses_common_features_only(self):
        # Extra keys in one map are ignored; ρ is over the intersection.
        perm = {**_PERFECT, "extra": 99.0}
        out = b2_attribution_gate(
            perm, _PERFECT, reproduction=(_PERFECT, _PERFECT), n_permutations=2000,
        )
        assert out["g1_n_features"] == len(_FEATS7)
        assert out["g1_rho"] == pytest.approx(1.0)
