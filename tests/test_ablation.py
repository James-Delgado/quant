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

from quant.backtest.ablation import LabelSchemeFn, run_label_ablation
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
