"""Tests for the B1 OOS-prediction collector + per-regime scoring (target_eval).

The collector reuses ``walkforward_splits`` (already leakage-tested), so these
tests cover: contract validation, that real edge is transmitted and no-skill is
not (perfect-foresight → AUC≈1 / MAE≈0; random → AUC≈0.5), pooled cross-sectional
training, the directional Sharpe arm's threshold + edge transmission, and the
per-regime grouping (including the single-class-AUC → nan degradation).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import mean_absolute_error, roc_auc_score

from quant.backtest.target_eval import (
    PRED_COLUMNS,
    collect_oos_predictions,
    per_regime_metric,
    simulate_signal_returns,
)


# ─── fixtures + stubs ────────────────────────────────────────────────────────

N = 600
SYMBOLS = ["AAPL", "MSFT"]
TRAIN_W, TEST_W, STEP, EMBARGO = 200, 50, 50, 3


def _make_prices(n: int, seed: int = 0) -> pd.DataFrame:
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


def _directional_label(prices: pd.DataFrame, horizon: int = 1) -> pd.Series:
    fwd = prices["close"].shift(-horizon) / prices["close"] - 1.0
    return (fwd > 0).astype(float).where(fwd.notna())


class RandomModel:
    def __init__(self, seed: int = 42) -> None:
        self._rng = np.random.default_rng(seed)

    def fit(self, X, y):  # noqa: D401
        return self

    def predict(self, X):
        return self._rng.uniform(0.0, 1.0, len(X))


class PeekLastColModel:
    """Returns the last feature column verbatim — used to inject true labels."""

    def fit(self, X, y):
        return self

    def predict(self, X):
        return X[:, -1].astype(float)


class RecordingModel:
    """Records the number of training rows seen on each fit() call."""

    def __init__(self) -> None:
        self.train_sizes: list[int] = []

    def fit(self, X, y):
        self.train_sizes.append(len(y))
        return self

    def predict(self, X):
        return np.zeros(len(X))


def _panel(label_in_last_col: bool = False, horizon: int = 1):
    """Build aligned features/labels/prices dicts (single dropna + intersection)."""
    features, labels, prices = {}, {}, {}
    for i, sym in enumerate(SYMBOLS):
        px = _make_prices(N, seed=i)
        rng = np.random.default_rng(100 + i)
        feat = pd.DataFrame(
            rng.standard_normal((N, 4)),
            index=px.index,
            columns=[f"f{j}" for j in range(4)],
        )
        lab = _directional_label(px, horizon=horizon)
        if label_in_last_col:
            feat["f_label"] = lab  # PeekLastColModel reads this as y_pred
        X = feat.dropna()
        y = lab.dropna()
        common = X.index.intersection(y.index)
        features[sym] = X.loc[common]
        labels[sym] = y.loc[common]
        prices[sym] = px.loc[common]
    return features, labels, prices


def _auc(y_true, y_pred) -> float:
    return float(roc_auc_score(y_true, y_pred))


def _mae(y_true, y_pred) -> float:
    return float(mean_absolute_error(y_true, y_pred))


# ─── collect_oos_predictions ─────────────────────────────────────────────────

class TestCollectOOSPredictions:
    def test_shape_columns_and_index(self):
        feats, labels, _ = _panel()
        out = collect_oos_predictions(
            RandomModel(), feats, labels,
            train_window=TRAIN_W, test_window=TEST_W, step=STEP, embargo=EMBARGO,
        )
        assert list(out.columns) == list(PRED_COLUMNS)
        assert isinstance(out.index, pd.DatetimeIndex)
        assert set(out["symbol"].unique()) <= set(SYMBOLS)
        assert len(out) > 0
        # y_true equals the supplied labels at each (symbol, date)
        for sym in SYMBOLS:
            sub = out[out["symbol"] == sym]
            expected = labels[sym].loc[sub.index]
            np.testing.assert_allclose(sub["y_true"].to_numpy(), expected.to_numpy())

    def test_perfect_foresight_recovers_labels(self):
        feats, labels, _ = _panel(label_in_last_col=True)
        out = collect_oos_predictions(
            PeekLastColModel(), feats, labels,
            train_window=TRAIN_W, test_window=TEST_W, step=STEP, embargo=EMBARGO,
        )
        # predict() returned the injected true label → perfect AUC, ~zero MAE
        assert _auc(out["y_true"], out["y_pred"]) == pytest.approx(1.0)
        assert _mae(out["y_true"], out["y_pred"]) == pytest.approx(0.0, abs=1e-9)

    def test_random_model_auc_near_half(self):
        feats, labels, _ = _panel()
        out = collect_oos_predictions(
            RandomModel(seed=7), feats, labels,
            train_window=TRAIN_W, test_window=TEST_W, step=STEP, embargo=EMBARGO,
        )
        assert _auc(out["y_true"], out["y_pred"]) == pytest.approx(0.5, abs=0.08)

    def test_pooled_training_uses_all_symbols(self):
        feats, labels, _ = _panel()
        rec = RecordingModel()
        collect_oos_predictions(
            rec, feats, labels,
            train_window=TRAIN_W, test_window=TEST_W, step=STEP, embargo=EMBARGO,
        )
        # Both symbols share the calendar, so each fold pools ~2× the per-symbol
        # purged train window (well above a single symbol's train_window).
        assert rec.train_sizes
        assert max(rec.train_sizes) > TRAIN_W

    def test_mismatched_keys_raise(self):
        feats, labels, _ = _panel()
        labels.pop("MSFT")
        with pytest.raises(ValueError, match="identical keys"):
            collect_oos_predictions(RandomModel(), feats, labels)

    def test_misaligned_index_raises(self):
        feats, labels, _ = _panel()
        labels["AAPL"] = labels["AAPL"].iloc[:-5]  # drop the alignment
        with pytest.raises(ValueError, match="identical index"):
            collect_oos_predictions(RandomModel(), feats, labels)

    def test_empty_keys_raises(self):
        with pytest.raises(ValueError, match="at least one symbol"):
            collect_oos_predictions(RandomModel(), {}, {})

    def test_no_splits_returns_empty_frame(self):
        feats, labels, _ = _panel()
        out = collect_oos_predictions(
            RandomModel(), feats, labels,
            train_window=N + 100, test_window=TEST_W, step=STEP,
        )
        assert out.empty
        assert list(out.columns) == list(PRED_COLUMNS)


# ─── simulate_signal_returns ─────────────────────────────────────────────────

def _uptrend_prices(n: int = 300, seed: int = 0) -> pd.DataFrame:
    """A steadily-rising path (strong positive drift, low vol) — always-long wins."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0025, 0.004, n)))
    open_ = close * (1 + rng.uniform(-0.001, 0.001, n))
    high = np.maximum(close, open_) * 1.002
    low = np.minimum(close, open_) * 0.998
    dates = pd.bdate_range("2018-01-02", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": np.full(n, 1_000_000.0)},
        index=dates,
    )


class TestSimulateSignalReturns:
    def test_signal_direction_maps_through_simulator(self):
        """sign(pred-0.5): pred>0.5 → long (wins on uptrend); pred<0.5 → short (loses)."""
        from quant.backtest.metrics import compute_metrics
        prices = {"AAPL": _uptrend_prices()}
        idx = prices["AAPL"].index

        long_preds = pd.DataFrame(
            {"symbol": "AAPL", "y_true": 1.0, "y_pred": np.full(len(idx), 1.0)},
            index=idx,
        )
        short_preds = long_preds.assign(y_pred=0.0)

        long_rets = simulate_signal_returns(long_preds, prices, threshold=0.5)
        short_rets = simulate_signal_returns(short_preds, prices, threshold=0.5)

        assert not long_rets.empty
        assert compute_metrics(long_rets)["sharpe"] > 0  # long an uptrend
        assert compute_metrics(short_rets)["sharpe"] < 0  # short an uptrend

    def test_threshold_flat_signal_gives_zero_returns(self):
        _, labels, prices = _panel()
        rows = []
        for sym in SYMBOLS:
            lab = labels[sym]
            rows.append(pd.DataFrame(
                {"symbol": sym, "y_true": lab.to_numpy(),
                 "y_pred": np.full(len(lab), 0.5)},  # exactly at threshold → flat
                index=lab.index,
            ))
        preds = pd.concat(rows)
        rets = simulate_signal_returns(preds, prices, threshold=0.5)
        # All signals are 0 (flat) → no positions → returns are all ~0.
        assert np.allclose(rets.to_numpy(), 0.0)

    def test_empty_predictions_returns_empty(self):
        empty = pd.DataFrame(
            {"symbol": pd.Series(dtype=object), "y_true": pd.Series(dtype=float),
             "y_pred": pd.Series(dtype=float)}
        )
        out = simulate_signal_returns(empty, {})
        assert out.empty


# ─── per_regime_metric ───────────────────────────────────────────────────────

class TestPerRegimeMetric:
    def _two_regime_preds(self):
        dates = pd.bdate_range("2019-06-01", periods=40)  # qe_bull
        dates2 = pd.bdate_range("2020-06-01", periods=40)  # covid
        idx = dates.append(dates2)
        rng = np.random.default_rng(0)
        y_true = rng.integers(0, 2, len(idx)).astype(float)
        preds = pd.DataFrame(
            {"symbol": "AAPL", "y_true": y_true, "y_pred": rng.uniform(0, 1, len(idx))},
            index=idx,
        )
        from quant.backtest.regimes import DateRangeDetector, tag_regimes
        regime_labels = tag_regimes(idx.unique(), DateRangeDetector())
        return preds, regime_labels

    def test_scores_each_regime(self):
        preds, regime_labels = self._two_regime_preds()
        out = per_regime_metric(preds, regime_labels, _auc)
        assert set(out) == {"qe_bull", "covid"}
        # Manual check for one regime
        sub = preds.loc[regime_labels.index[regime_labels == "covid"]]
        assert out["covid"] == pytest.approx(_auc(sub["y_true"], sub["y_pred"]))

    def test_single_class_regime_is_nan(self):
        preds, regime_labels = self._two_regime_preds()
        # Force the covid regime to a single class → AUC undefined → nan.
        covid_dates = regime_labels.index[regime_labels == "covid"]
        preds.loc[covid_dates, "y_true"] = 1.0
        out = per_regime_metric(preds, regime_labels, _auc)
        assert np.isnan(out["covid"])
        assert not np.isnan(out["qe_bull"])

    def test_restrict_regimes(self):
        preds, regime_labels = self._two_regime_preds()
        out = per_regime_metric(preds, regime_labels, _auc, regimes=("covid",))
        assert set(out) == {"covid"}

    def test_empty_predictions_returns_empty_dict(self):
        empty = pd.DataFrame(
            {"symbol": pd.Series(dtype=object), "y_true": pd.Series(dtype=float),
             "y_pred": pd.Series(dtype=float)}
        )
        assert per_regime_metric(empty, pd.Series(dtype=object), _auc) == {}
