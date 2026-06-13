"""Tests for src/quant/backtest/ablation.py and the ablation reporter.

The ablation orchestrator iterates over label schemes (not models, as
``evaluate_panel`` does) and runs each through ``run_portfolio_backtest``
with identical hyperparameters. Same kwargs-discipline + ``copy.deepcopy``
on the model.

The reporter (in ``backtest/report.py``) ranks the resulting schemes by a
balanced multi-regime Borda count — no regime weighted more than another.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from quant.backtest.ablation import (
    LabelSchemeFn,
    make_add_one_sets,
    make_leave_one_out_sets,
    run_feature_ablation,
    run_label_ablation,
)
from quant.backtest.harness import BacktestResult
from quant.backtest.metrics import compute_metrics
from quant.features.label_schemes import (
    LDP_DEFAULT,
    triple_barrier_labels,
    vol_scaled_returns,
)
from quant.features.labels import LabelResult, generate_labels


# ─── Helpers (mirror tests/test_portfolio_harness.py shape) ─────────────────


def _make_prices(n: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n)))
    open_ = close * (1 + rng.uniform(-0.002, 0.002, n))
    high = np.maximum(close, open_) * (1 + rng.uniform(0.0, 0.005, n))
    low = np.minimum(close, open_) * (1 - rng.uniform(0.0, 0.005, n))
    dates = pd.bdate_range("2018-01-02", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": rng.integers(500_000, 2_000_000, n).astype(float)},
        index=dates,
    )


def _make_features(n: int, n_cols: int = 5, seed: int = 0, dates: pd.Index | None = None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = dates if dates is not None else pd.bdate_range("2018-01-02", periods=n)
    return pd.DataFrame(
        rng.standard_normal((n, n_cols)),
        index=idx,
        columns=[f"f{i}" for i in range(n_cols)],
    )


class _ConstantModel:
    """Predicts mean(y_train) on every row.

    Trivial deterministic model used to verify orchestration mechanics
    without depending on real model behaviour.
    """

    def __init__(self) -> None:
        self._mean: float = 0.0

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        finite = y[np.isfinite(y)]
        self._mean = float(finite.mean()) if finite.size > 0 else 0.0

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.full(len(X), self._mean, dtype=float)


# ─── Fixtures ────────────────────────────────────────────────────────────────


N = 600
SYMBOLS = ["AAPL", "MSFT"]
TRAIN_W, TEST_W, STEP = 200, 50, 50
EMBARGO = 3


@pytest.fixture(scope="module")
def prices_by_sym() -> dict[str, pd.DataFrame]:
    return {sym: _make_prices(N, seed=i) for i, sym in enumerate(SYMBOLS)}


@pytest.fixture(scope="module")
def features_by_sym(prices_by_sym: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    return {
        sym: _make_features(N, dates=prices_by_sym[sym].index, seed=i)
        for i, sym in enumerate(SYMBOLS)
    }


def _signed_returns_scheme(prices: pd.Series) -> LabelResult:
    return generate_labels(prices, horizon=1)


def _vol_scaled_scheme(prices: pd.Series) -> LabelResult:
    return vol_scaled_returns(prices, horizon=1, vol_window=21)


def _triple_barrier_scheme(prices: pd.Series) -> LabelResult:
    return triple_barrier_labels(prices, config=LDP_DEFAULT)


THREE_SCHEMES: dict[str, LabelSchemeFn] = {
    "signed_returns": _signed_returns_scheme,
    "vol_scaled": _vol_scaled_scheme,
    "triple_barrier": _triple_barrier_scheme,
}


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestRunLabelAblation:
    def test_returns_dict_keyed_by_scheme_name(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        out = run_label_ablation(
            label_schemes=THREE_SCHEMES,
            model=_ConstantModel(),
            features_by_symbol=features_by_sym,
            prices_by_symbol=prices_by_sym,
            train_window=TRAIN_W,
            test_window=TEST_W,
            step=STEP,
            embargo=EMBARGO,
        )
        assert set(out.keys()) == set(THREE_SCHEMES.keys())
        for name, res in out.items():
            assert isinstance(res, BacktestResult), f"scheme {name}: bad result type"

    def test_label_horizon_derived_from_scheme(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        captured: dict[str, int] = {}

        def long_horizon_scheme(prices: pd.Series) -> LabelResult:
            res = generate_labels(prices, horizon=3)
            captured["horizon_bars"] = res.horizon_bars
            return res

        out = run_label_ablation(
            label_schemes={"h3": long_horizon_scheme},
            model=_ConstantModel(),
            features_by_symbol=features_by_sym,
            prices_by_symbol=prices_by_sym,
            train_window=TRAIN_W,
            test_window=TEST_W,
            step=STEP,
            embargo=EMBARGO,
        )
        assert captured["horizon_bars"] == 3
        assert isinstance(out["h3"], BacktestResult)

    def test_kwargs_identical_across_schemes(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        prices_by_sym: dict[str, pd.DataFrame],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Capture run_portfolio_backtest kwargs for each scheme; the
        # train/test/step/embargo set must be byte-identical across schemes.
        captured_kwargs: list[dict[str, Any]] = []
        import quant.backtest.ablation as ablation_mod
        real = ablation_mod.run_portfolio_backtest

        def spy(*args: Any, **kwargs: Any) -> Any:
            recorded = {
                k: v for k, v in kwargs.items()
                if k in ("train_window", "test_window", "step", "embargo")
            }
            captured_kwargs.append(recorded)
            return real(*args, **kwargs)

        monkeypatch.setattr("quant.backtest.ablation.run_portfolio_backtest", spy)

        run_label_ablation(
            label_schemes=THREE_SCHEMES,
            model=_ConstantModel(),
            features_by_symbol=features_by_sym,
            prices_by_symbol=prices_by_sym,
            train_window=TRAIN_W,
            test_window=TEST_W,
            step=STEP,
            embargo=EMBARGO,
        )
        assert len(captured_kwargs) == len(THREE_SCHEMES)
        first = captured_kwargs[0]
        for k in captured_kwargs[1:]:
            assert k == first, f"kwargs drifted: {k} vs {first}"

    def test_model_template_not_mutated(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        class CountingModel:
            def __init__(self) -> None:
                self.fit_calls = 0

            def fit(self, X: np.ndarray, y: np.ndarray) -> None:
                self.fit_calls += 1

            def predict(self, X: np.ndarray) -> np.ndarray:
                return np.zeros(len(X))

        template = CountingModel()
        out = run_label_ablation(
            label_schemes=THREE_SCHEMES,
            model=template,
            features_by_symbol=features_by_sym,
            prices_by_symbol=prices_by_sym,
            train_window=TRAIN_W,
            test_window=TEST_W,
            step=STEP,
            embargo=EMBARGO,
        )
        assert template.fit_calls == 0, "template model should not be mutated"
        assert all(isinstance(r, BacktestResult) for r in out.values())

    def test_empty_schemes_raises(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        with pytest.raises(ValueError, match="at least one label scheme"):
            run_label_ablation(
                label_schemes={},
                model=_ConstantModel(),
                features_by_symbol=features_by_sym,
                prices_by_symbol=prices_by_sym,
                train_window=TRAIN_W,
                test_window=TEST_W,
                step=STEP,
                embargo=EMBARGO,
            )

    def test_oos_returns_populated(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        out = run_label_ablation(
            label_schemes={"signed_returns": _signed_returns_scheme},
            model=_ConstantModel(),
            features_by_symbol=features_by_sym,
            prices_by_symbol=prices_by_sym,
            train_window=TRAIN_W,
            test_window=TEST_W,
            step=STEP,
            embargo=EMBARGO,
        )
        res = out["signed_returns"]
        assert len(res.oos_returns) > 0
        assert len(res.oos_forecast_errors) > 0


# ─── Reporter tests — synthetic BacktestResults for fast, deterministic
#     verification of the balanced Borda ranking and ablation tables. ─────────


def _synthetic_result(
    n: int,
    daily_mean: float,
    daily_vol: float,
    seed: int,
) -> BacktestResult:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2010-01-04", periods=n)
    returns = pd.Series(rng.normal(daily_mean, daily_vol, n), index=idx)
    errors = pd.Series(rng.normal(0.0, daily_vol, n), index=idx)
    return BacktestResult(
        oos_metrics=compute_metrics(returns),
        is_metrics={"sharpe": 0.0},
        equity_curve=(1 + returns).cumprod() * 100_000.0,
        trade_log=pd.DataFrame(),
        oos_returns=returns,
        oos_forecast_errors=errors,
    )


def _regime_labels_for(result: BacktestResult, regimes: list[str]) -> pd.Series:
    """Split the OOS index roughly evenly across ``regimes``."""
    n = len(result.oos_returns)
    chunk = n // len(regimes)
    labels = pd.Series("", index=result.oos_returns.index, dtype=object)
    for i, regime in enumerate(regimes):
        start = i * chunk
        end = (i + 1) * chunk if i < len(regimes) - 1 else n
        labels.iloc[start:end] = regime
    return labels


@pytest.fixture(scope="module")
def synthetic_results() -> dict[str, BacktestResult]:
    """Three schemes with distinct synthetic Sharpe profiles for ranking tests."""
    return {
        "scheme_a": _synthetic_result(n=120, daily_mean=0.0010, daily_vol=0.010, seed=1),
        "scheme_b": _synthetic_result(n=120, daily_mean=0.0005, daily_vol=0.010, seed=2),
        "scheme_c": _synthetic_result(n=120, daily_mean=-0.0002, daily_vol=0.010, seed=3),
    }


@pytest.fixture(scope="module")
def synthetic_regime_labels(synthetic_results: dict[str, BacktestResult]) -> pd.Series:
    any_result = next(iter(synthetic_results.values()))
    return _regime_labels_for(any_result, regimes=["qe_bull", "covid", "rate_cycle"])


class TestAblationReport:
    def test_summary_table_one_row_per_scheme(
        self,
        synthetic_results: dict[str, BacktestResult],
        synthetic_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import ablation_summary_table

        tbl = ablation_summary_table(synthetic_results, synthetic_regime_labels)
        assert isinstance(tbl, pd.DataFrame)
        assert set(tbl.index) == set(synthetic_results.keys())

    def test_summary_table_has_aggregate_and_per_regime_columns(
        self,
        synthetic_results: dict[str, BacktestResult],
        synthetic_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import ablation_summary_table

        tbl = ablation_summary_table(synthetic_results, synthetic_regime_labels)
        for col in ("aggregate", "qe_bull", "covid", "rate_cycle"):
            assert col in tbl.columns, f"missing column {col!r}: got {list(tbl.columns)}"

    def test_composite_ranking_borda(
        self,
        synthetic_results: dict[str, BacktestResult],
        synthetic_regime_labels: pd.Series,
    ) -> None:
        # scheme_a has the highest mean daily return → should win the Borda
        # composite. scheme_c has the lowest → should lose.
        from quant.backtest.report import ablation_composite_ranking

        ranking = ablation_composite_ranking(synthetic_results, synthetic_regime_labels)
        assert isinstance(ranking, pd.DataFrame)
        assert {"composite_rank", "mean_rank_across_regimes"}.issubset(ranking.columns)
        # composite_rank values are 1..N (1 = best)
        ranks = sorted(ranking["composite_rank"].tolist())
        assert ranks == [1, 2, 3]
        # scheme_a should be rank 1 given its dominant Sharpe profile
        assert ranking.loc["scheme_a", "composite_rank"] == 1
        # scheme_c should be rank 3
        assert ranking.loc["scheme_c", "composite_rank"] == 3

    def test_composite_ranking_equal_weight_per_regime(
        self,
        synthetic_results: dict[str, BacktestResult],
        synthetic_regime_labels: pd.Series,
    ) -> None:
        # Balanced ranking: no regime weighted more than another. Verify by
        # checking that mean_rank_across_regimes = average of per-regime ranks
        # for at least one scheme.
        from quant.backtest.report import ablation_composite_ranking

        ranking = ablation_composite_ranking(synthetic_results, synthetic_regime_labels)
        regime_cols = [
            c for c in ranking.columns
            if c not in ("composite_rank", "mean_rank_across_regimes")
        ]
        for scheme in ranking.index:
            mean_rank = ranking.loc[scheme, "mean_rank_across_regimes"]
            row_ranks = ranking.loc[scheme, regime_cols].to_numpy()
            expected = float(np.mean(row_ranks))
            assert mean_rank == pytest.approx(expected, rel=1e-9)

    def test_dm_matrix_shape(
        self,
        synthetic_results: dict[str, BacktestResult],
        synthetic_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import ablation_dm_matrix

        dm = ablation_dm_matrix(synthetic_results, synthetic_regime_labels)
        # One row per scheme pair × regime column
        assert isinstance(dm, pd.DataFrame)
        scheme_pairs = [
            (a, b)
            for i, a in enumerate(synthetic_results)
            for b in list(synthetic_results)[i + 1:]
        ]
        assert len(dm) == len(scheme_pairs)
        for regime in ("qe_bull", "covid", "rate_cycle"):
            assert regime in dm.columns

    def test_format_ablation_report_returns_string(
        self,
        synthetic_results: dict[str, BacktestResult],
        synthetic_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import format_ablation_report

        out = format_ablation_report(synthetic_results, synthetic_regime_labels)
        assert isinstance(out, str)
        assert len(out) > 0
        for scheme in synthetic_results:
            assert scheme in out
        # Should mention each regime
        for regime in ("qe_bull", "covid", "rate_cycle"):
            assert regime in out

    def test_summary_table_includes_aggregate_sharpe(
        self,
        synthetic_results: dict[str, BacktestResult],
        synthetic_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import ablation_summary_table

        tbl = ablation_summary_table(synthetic_results, synthetic_regime_labels)
        # aggregate column for scheme_a should equal its overall OOS Sharpe.
        expected = synthetic_results["scheme_a"].oos_metrics["sharpe"]
        assert tbl.loc["scheme_a", "aggregate"] == pytest.approx(expected)

    def test_empty_results_raises(
        self,
        synthetic_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import ablation_summary_table

        with pytest.raises(ValueError, match="at least one"):
            ablation_summary_table({}, synthetic_regime_labels)


# ─── Feature ablation (Phase 4A Milestone 3, Tasks 3-4) ─────────────────────


@pytest.fixture(scope="module")
def labels_by_sym(prices_by_sym: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
    return {
        sym: generate_labels(df["close"], horizon=1).series
        for sym, df in prices_by_sym.items()
    }


BASELINE_COLS = ["f0", "f1", "f2"]
FEATURE_SETS: dict[str, list[str]] = {
    "baseline": BASELINE_COLS,
    "+f3": BASELINE_COLS + ["f3"],
    "+f4": BASELINE_COLS + ["f4"],
}


class TestFeatureSetHelpers:
    def test_make_add_one_sets_n_plus_one(self) -> None:
        sets = make_add_one_sets(["a", "b"], ["c", "d", "e"])
        assert len(sets) == 4  # baseline + 3 candidates

    def test_make_add_one_sets_contents(self) -> None:
        sets = make_add_one_sets(["a", "b"], ["c", "d"])
        assert sets["baseline"] == ["a", "b"]
        assert sets["+c"] == ["a", "b", "c"]
        assert sets["+d"] == ["a", "b", "d"]

    def test_make_add_one_sets_does_not_mutate_baseline(self) -> None:
        baseline = ["a", "b"]
        sets = make_add_one_sets(baseline, ["c"])
        sets["+c"].append("zzz")
        assert baseline == ["a", "b"]
        assert sets["baseline"] == ["a", "b"]

    def test_make_add_one_sets_duplicate_candidate_raises(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            make_add_one_sets(["a"], ["c", "c"])

    def test_make_add_one_sets_candidate_in_baseline_raises(self) -> None:
        with pytest.raises(ValueError, match="already"):
            make_add_one_sets(["a", "b"], ["b"])

    def test_make_leave_one_out_sets_n_plus_one(self) -> None:
        sets = make_leave_one_out_sets(["a", "b", "c"])
        assert len(sets) == 4  # all + 3 leave-outs

    def test_make_leave_one_out_sets_contents(self) -> None:
        sets = make_leave_one_out_sets(["a", "b", "c"])
        assert sets["all"] == ["a", "b", "c"]
        assert sets["-a"] == ["b", "c"]
        assert sets["-b"] == ["a", "c"]
        assert sets["-c"] == ["a", "b"]

    def test_make_leave_one_out_sets_duplicate_raises(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            make_leave_one_out_sets(["a", "a"])


class _ColumnWidthModel:
    """Records X column counts in a class attribute shared across deepcopies."""

    widths_seen: list[int] = []

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        type(self).widths_seen.append(int(X.shape[1]))

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.zeros(len(X))


class TestRunFeatureAblation:
    def test_returns_dict_keyed_by_set_name(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        labels_by_sym: dict[str, pd.Series],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        out = run_feature_ablation(
            feature_sets=FEATURE_SETS,
            model=_ConstantModel(),
            features_by_symbol=features_by_sym,
            labels_by_symbol=labels_by_sym,
            prices_by_symbol=prices_by_sym,
            train_window=TRAIN_W,
            test_window=TEST_W,
            step=STEP,
            embargo=EMBARGO,
        )
        assert set(out.keys()) == set(FEATURE_SETS.keys())
        for name, res in out.items():
            assert isinstance(res, BacktestResult), f"set {name}: bad result type"
            assert len(res.oos_returns) > 0

    def test_model_receives_expected_column_count(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        labels_by_sym: dict[str, pd.Series],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        _ColumnWidthModel.widths_seen = []
        run_feature_ablation(
            feature_sets={"baseline": BASELINE_COLS, "+f3": BASELINE_COLS + ["f3"]},
            model=_ColumnWidthModel(),
            features_by_symbol=features_by_sym,
            labels_by_symbol=labels_by_sym,
            prices_by_symbol=prices_by_sym,
            train_window=TRAIN_W,
            test_window=TEST_W,
            step=STEP,
            embargo=EMBARGO,
        )
        assert set(_ColumnWidthModel.widths_seen) == {3, 4}

    def test_kwargs_forwarded_identically_across_sets(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        labels_by_sym: dict[str, pd.Series],
        prices_by_sym: dict[str, pd.DataFrame],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: list[dict[str, Any]] = []
        import quant.backtest.ablation as ablation_mod
        real = ablation_mod.run_portfolio_backtest

        def spy(*args: Any, **kwargs: Any) -> Any:
            recorded = {
                k: v for k, v in kwargs.items()
                if k in ("train_window", "test_window", "step", "embargo",
                         "label_horizon", "commission_per_share")
            }
            recorded["feature_cols"] = tuple(
                next(iter(kwargs["features_by_symbol"].values())).columns
            )
            captured.append(recorded)
            return real(*args, **kwargs)

        monkeypatch.setattr("quant.backtest.ablation.run_portfolio_backtest", spy)

        run_feature_ablation(
            feature_sets=FEATURE_SETS,
            model=_ConstantModel(),
            features_by_symbol=features_by_sym,
            labels_by_symbol=labels_by_sym,
            prices_by_symbol=prices_by_sym,
            train_window=TRAIN_W,
            test_window=TEST_W,
            step=STEP,
            embargo=EMBARGO,
            commission_per_share=0.01,
        )
        assert len(captured) == len(FEATURE_SETS)
        # The feature columns vary per set...
        cols_per_call = [c.pop("feature_cols") for c in captured]
        assert cols_per_call == [tuple(cols) for cols in FEATURE_SETS.values()]
        # ...but every other kwarg is byte-identical across calls.
        first = captured[0]
        for k in captured[1:]:
            assert k == first, f"kwargs drifted: {k} vs {first}"

    def test_model_template_not_mutated(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        labels_by_sym: dict[str, pd.Series],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        class CountingModel:
            def __init__(self) -> None:
                self.fit_calls = 0

            def fit(self, X: np.ndarray, y: np.ndarray) -> None:
                self.fit_calls += 1

            def predict(self, X: np.ndarray) -> np.ndarray:
                return np.zeros(len(X))

        template = CountingModel()
        run_feature_ablation(
            feature_sets=FEATURE_SETS,
            model=template,
            features_by_symbol=features_by_sym,
            labels_by_symbol=labels_by_sym,
            prices_by_symbol=prices_by_sym,
            train_window=TRAIN_W,
            test_window=TEST_W,
            step=STEP,
            embargo=EMBARGO,
        )
        assert template.fit_calls == 0, "template model should not be mutated"

    def test_missing_column_raises_naming_column_and_symbol(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        labels_by_sym: dict[str, pd.Series],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        with pytest.raises(ValueError) as excinfo:
            run_feature_ablation(
                feature_sets={"bad": ["f0", "not_a_column"]},
                model=_ConstantModel(),
                features_by_symbol=features_by_sym,
                labels_by_symbol=labels_by_sym,
                prices_by_symbol=prices_by_sym,
                train_window=TRAIN_W,
                test_window=TEST_W,
                step=STEP,
                embargo=EMBARGO,
            )
        msg = str(excinfo.value)
        assert "not_a_column" in msg
        for sym in SYMBOLS:
            assert sym in msg

    def test_empty_sets_raises(
        self,
        features_by_sym: dict[str, pd.DataFrame],
        labels_by_sym: dict[str, pd.Series],
        prices_by_sym: dict[str, pd.DataFrame],
    ) -> None:
        with pytest.raises(ValueError, match="at least one feature set"):
            run_feature_ablation(
                feature_sets={},
                model=_ConstantModel(),
                features_by_symbol=features_by_sym,
                labels_by_symbol=labels_by_sym,
                prices_by_symbol=prices_by_sym,
            )


# ─── Feature-ablation reporters + PRD gate (Task 4b) ─────────────────────────


REGIMES = ["qe_bull", "covid", "rate_cycle"]
N_PER_REGIME = 80


def _result_from_returns(returns: pd.Series) -> BacktestResult:
    return BacktestResult(
        oos_metrics=compute_metrics(returns),
        is_metrics={"sharpe": 0.0},
        equity_curve=(1 + returns).cumprod() * 100_000.0,
        trade_log=pd.DataFrame(),
        oos_returns=returns,
        oos_forecast_errors=pd.Series(0.1, index=returns.index),
    )


@pytest.fixture(scope="module")
def fa_baseline_returns() -> pd.Series:
    rng = np.random.default_rng(100)
    idx = pd.bdate_range("2012-01-03", periods=N_PER_REGIME * len(REGIMES))
    return pd.Series(rng.normal(0.0003, 0.01, len(idx)), index=idx)


@pytest.fixture(scope="module")
def fa_regime_labels(fa_baseline_returns: pd.Series) -> pd.Series:
    labels = pd.Series("", index=fa_baseline_returns.index, dtype=object)
    for i, regime in enumerate(REGIMES):
        labels.iloc[i * N_PER_REGIME:(i + 1) * N_PER_REGIME] = regime
    return labels


def _shifted_variant(
    baseline: pd.Series,
    regime_labels: pd.Series,
    regime: str,
    shift: float,
) -> pd.Series:
    """Variant identical to baseline except a constant shift inside one regime."""
    out = baseline.copy()
    out.loc[regime_labels == regime] += shift
    return out


def _noisy_variant(
    baseline: pd.Series,
    regime_labels: pd.Series,
    regime: str,
    noise_mean: float,
    noise_vol: float,
    seed: int,
) -> pd.Series:
    """Variant = baseline + independent noise inside one regime (CI straddles 0)."""
    out = baseline.copy()
    mask = regime_labels == regime
    rng = np.random.default_rng(seed)
    out.loc[mask] += rng.normal(noise_mean, noise_vol, int(mask.sum()))
    return out


def _regime_sharpe(returns: pd.Series, regime_labels: pd.Series, regime: str) -> float:
    return compute_metrics(returns.loc[regime_labels == regime])["sharpe"]


class TestFeatureAblationTable:
    def test_baseline_row_absolute_variant_rows_delta(
        self,
        fa_baseline_returns: pd.Series,
        fa_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import feature_ablation_table

        variant = _shifted_variant(fa_baseline_returns, fa_regime_labels, "covid", 0.004)
        results = {
            "baseline": _result_from_returns(fa_baseline_returns),
            "+x": _result_from_returns(variant),
        }
        tbl = feature_ablation_table(results, "baseline", fa_regime_labels)

        # Baseline row = absolute Sharpe.
        base_covid = _regime_sharpe(fa_baseline_returns, fa_regime_labels, "covid")
        assert tbl.loc["baseline", "covid"] == pytest.approx(base_covid)
        assert tbl.loc["baseline", "aggregate"] == pytest.approx(
            compute_metrics(fa_baseline_returns)["sharpe"]
        )
        # Variant rows = delta vs baseline, algebraically expected.
        var_covid = _regime_sharpe(variant, fa_regime_labels, "covid")
        assert tbl.loc["+x", "covid"] == pytest.approx(var_covid - base_covid)
        # Outside the shifted regime the variant equals the baseline → delta 0.
        assert tbl.loc["+x", "qe_bull"] == pytest.approx(0.0, abs=1e-12)
        assert tbl.loc["+x", "rate_cycle"] == pytest.approx(0.0, abs=1e-12)

    def test_has_aggregate_regime_and_n_bars_columns(
        self,
        fa_baseline_returns: pd.Series,
        fa_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import feature_ablation_table

        results = {"baseline": _result_from_returns(fa_baseline_returns)}
        tbl = feature_ablation_table(results, "baseline", fa_regime_labels)
        for col in ["aggregate", *REGIMES, "n_bars"]:
            assert col in tbl.columns, f"missing column {col!r}"
        assert tbl.loc["baseline", "n_bars"] == len(fa_baseline_returns)

    def test_unknown_baseline_raises(
        self,
        fa_baseline_returns: pd.Series,
        fa_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import feature_ablation_table

        results = {"baseline": _result_from_returns(fa_baseline_returns)}
        with pytest.raises(ValueError, match="nope"):
            feature_ablation_table(results, "nope", fa_regime_labels)


class TestFeatureAblationGate:
    def _strong_results(
        self,
        baseline: pd.Series,
        labels: pd.Series,
        n_strong: int,
    ) -> dict[str, BacktestResult]:
        """Baseline + n strong variants (large constant shift in 'covid')."""
        results = {"baseline": _result_from_returns(baseline)}
        for i in range(n_strong):
            variant = _shifted_variant(baseline, labels, "covid", 0.004 + 0.001 * i)
            results[f"+strong_{i}"] = _result_from_returns(variant)
        return results

    def test_gate_fires_at_exactly_three_qualifying(
        self,
        fa_baseline_returns: pd.Series,
        fa_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import feature_ablation_gate

        results = self._strong_results(fa_baseline_returns, fa_regime_labels, 3)
        gate = feature_ablation_gate(
            results, "baseline", fa_regime_labels, n_boot=200
        )
        assert gate["gate_passed"] is True
        assert len(gate["qualifying_features"]) == 3
        assert gate["n_candidates"] == 3
        for name, info in gate["qualifying_features"].items():
            assert info["regime"] == "covid", name
            assert info["lift"] >= 0.1

    def test_gate_does_not_fire_at_two(
        self,
        fa_baseline_returns: pd.Series,
        fa_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import feature_ablation_gate

        results = self._strong_results(fa_baseline_returns, fa_regime_labels, 2)
        # Add a hopeless variant: negligible shift → lift ≈ 0.
        weak = _shifted_variant(fa_baseline_returns, fa_regime_labels, "covid", 1e-7)
        results["+weak"] = _result_from_returns(weak)
        gate = feature_ablation_gate(
            results, "baseline", fa_regime_labels, n_boot=200
        )
        assert gate["gate_passed"] is False
        assert len(gate["qualifying_features"]) == 2
        assert "+weak" not in gate["qualifying_features"]

    def test_noise_guard_rejects_straddling_ci(
        self,
        fa_baseline_returns: pd.Series,
        fa_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import (
            feature_ablation_gate,
            feature_ablation_table,
        )

        # Independent noise in one regime: enough sample lift to clear 0.1,
        # but the paired bootstrap CI straddles 0 and the delta is positive
        # in only that one regime → no sign-consistency rescue.
        noisy = _noisy_variant(
            fa_baseline_returns, fa_regime_labels, "covid",
            noise_mean=0.0003, noise_vol=0.01, seed=202,
        )
        results = {
            "baseline": _result_from_returns(fa_baseline_returns),
            "+noisy": _result_from_returns(noisy),
        }
        # Precondition: the raw lift clears the threshold.
        tbl = feature_ablation_table(results, "baseline", fa_regime_labels)
        assert tbl.loc["+noisy", "covid"] >= 0.1, (
            f"test setup broken: lift {tbl.loc['+noisy', 'covid']:.3f} < 0.1 — retune seed"
        )
        gate = feature_ablation_gate(
            results, "baseline", fa_regime_labels, n_boot=300
        )
        assert "+noisy" not in gate["qualifying_features"]
        assert gate["gate_passed"] is False

    def test_noise_guard_false_accepts_raw_lift(
        self,
        fa_baseline_returns: pd.Series,
        fa_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import feature_ablation_gate

        noisy = _noisy_variant(
            fa_baseline_returns, fa_regime_labels, "covid",
            noise_mean=0.0003, noise_vol=0.01, seed=202,
        )
        results = {
            "baseline": _result_from_returns(fa_baseline_returns),
            "+noisy": _result_from_returns(noisy),
        }
        gate = feature_ablation_gate(
            results, "baseline", fa_regime_labels, noise_guard=False, n_boot=200
        )
        assert "+noisy" in gate["qualifying_features"]

    def test_sign_consistency_rescues_straddling_ci(
        self,
        fa_baseline_returns: pd.Series,
        fa_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import feature_ablation_gate

        # Same noisy lift in 'covid' (CI straddles 0), plus a small positive
        # constant shift in 'qe_bull' → delta > 0 in 2 regimes → qualifies
        # via cross-regime sign-consistency (branch (b) of the noise guard).
        variant = _noisy_variant(
            fa_baseline_returns, fa_regime_labels, "covid",
            noise_mean=0.0003, noise_vol=0.01, seed=202,
        )
        variant = _shifted_variant(variant, fa_regime_labels, "qe_bull", 0.0005)
        results = {
            "baseline": _result_from_returns(fa_baseline_returns),
            "+consistent": _result_from_returns(variant),
        }
        gate = feature_ablation_gate(
            results, "baseline", fa_regime_labels, n_boot=300
        )
        assert "+consistent" in gate["qualifying_features"]
        assert gate["qualifying_features"]["+consistent"]["sign_consistent"] is True

    def test_missing_regime_warns_not_crashes(
        self,
        fa_baseline_returns: pd.Series,
        fa_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import feature_ablation_gate

        # Extend regime_labels with a regime that lives entirely outside the
        # OOS index — it must warn and be skipped, not crash.
        extra_idx = pd.bdate_range("2030-01-02", periods=10)
        labels = pd.concat([
            fa_regime_labels,
            pd.Series("ghost_regime", index=extra_idx, dtype=object),
        ])
        results = self._strong_results(fa_baseline_returns, fa_regime_labels, 1)
        with pytest.warns(UserWarning, match="ghost_regime"):
            gate = feature_ablation_gate(results, "baseline", labels, n_boot=100)
        assert "gate_passed" in gate

    def test_gate_dict_shape(
        self,
        fa_baseline_returns: pd.Series,
        fa_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import feature_ablation_gate

        results = self._strong_results(fa_baseline_returns, fa_regime_labels, 1)
        gate = feature_ablation_gate(results, "baseline", fa_regime_labels, n_boot=100)
        assert set(gate.keys()) == {
            "gate_passed", "qualifying_features", "n_candidates", "thresholds",
        }
        assert gate["thresholds"]["min_lift"] == pytest.approx(0.1)
        assert gate["thresholds"]["min_features"] == 3
        assert gate["thresholds"]["noise_guard"] is True
        info = gate["qualifying_features"]["+strong_0"]
        assert set(info.keys()) == {
            "regime", "lift", "ci_low", "ci_high", "sign_consistent",
        }

    def test_unknown_baseline_raises(
        self,
        fa_baseline_returns: pd.Series,
        fa_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import feature_ablation_gate

        results = {"baseline": _result_from_returns(fa_baseline_returns)}
        with pytest.raises(ValueError, match="nope"):
            feature_ablation_gate(results, "nope", fa_regime_labels)


class TestFormatFeatureAblationReport:
    def test_returns_string_with_sections(
        self,
        fa_baseline_returns: pd.Series,
        fa_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import format_feature_ablation_report

        variant = _shifted_variant(fa_baseline_returns, fa_regime_labels, "covid", 0.004)
        results = {
            "baseline": _result_from_returns(fa_baseline_returns),
            "+x": _result_from_returns(variant),
        }
        out = format_feature_ablation_report(
            results, "baseline", fa_regime_labels, n_boot=100
        )
        assert isinstance(out, str)
        for name in results:
            assert name in out
        for regime in REGIMES:
            assert regime in out
        assert "PASSED" in out or "FAILED" in out

    def test_gate_kwargs_forwarded(
        self,
        fa_baseline_returns: pd.Series,
        fa_regime_labels: pd.Series,
    ) -> None:
        from quant.backtest.report import format_feature_ablation_report

        variant = _shifted_variant(fa_baseline_returns, fa_regime_labels, "covid", 0.004)
        results = {
            "baseline": _result_from_returns(fa_baseline_returns),
            "+x": _result_from_returns(variant),
        }
        # min_features=1 → this single strong feature passes the gate.
        out = format_feature_ablation_report(
            results, "baseline", fa_regime_labels, min_features=1, n_boot=100
        )
        assert "PASSED" in out
