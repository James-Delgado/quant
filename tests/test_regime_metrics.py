"""Tests for src/quant/backtest/regime_metrics.py."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest.harness import BacktestResult
from quant.backtest.regime_metrics import (
    compute_regime_metrics,
    phase4a_gate_report,
    regime_dm_test,
)
from quant.backtest.statistics import DMResult


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _returns(values: list[float], start: str = "2020-01-02") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def _labels(values: list[str], start: str = "2020-01-02") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=object)


def _make_result(
    oos_returns: pd.Series,
    forecast_errors: pd.Series | None = None,
) -> BacktestResult:
    """Build a minimal BacktestResult for testing — most fields stubbed."""
    return BacktestResult(
        oos_metrics={"sharpe": 0.0},
        is_metrics={"sharpe": 0.0},
        equity_curve=pd.Series(dtype=float),
        trade_log=pd.DataFrame(),
        oos_returns=oos_returns,
        oos_forecast_errors=(
            forecast_errors
            if forecast_errors is not None
            else pd.Series(dtype=float)
        ),
    )


# ─── compute_regime_metrics ──────────────────────────────────────────────────


class TestComputeRegimeMetrics:
    def test_groups_returns_by_regime(self) -> None:
        returns = _returns([0.01, 0.02, -0.01, -0.02, 0.005, 0.015])
        labels = _labels(["bull", "bull", "bear", "bear", "bull", "bull"])
        per_regime = compute_regime_metrics(returns, labels)
        assert set(per_regime.keys()) == {"bull", "bear"}

    def test_each_regime_has_full_metric_dict(self) -> None:
        returns = _returns([0.01, 0.02, -0.01, -0.02])
        labels = _labels(["a", "a", "b", "b"])
        per_regime = compute_regime_metrics(returns, labels)
        for regime in per_regime:
            for key in ("sharpe", "sortino", "max_drawdown", "total_return"):
                assert key in per_regime[regime]

    def test_only_regimes_in_labels_appear(self) -> None:
        returns = _returns([0.01, 0.02])
        labels = _labels(["a", "a"])
        per_regime = compute_regime_metrics(returns, labels)
        assert "a" in per_regime
        assert "b" not in per_regime

    def test_random_returns_near_zero_sharpe_per_regime(self) -> None:
        """A random no-drift series must show ~0 Sharpe in every regime."""
        rng = np.random.default_rng(0)
        n = 500
        returns = pd.Series(
            rng.normal(0.0, 0.01, n),
            index=pd.bdate_range("2020-01-02", periods=n),
        )
        labels = pd.Series(
            rng.choice(["a", "b"], size=n),
            index=returns.index,
        )
        per_regime = compute_regime_metrics(returns, labels)
        for regime, m in per_regime.items():
            assert abs(m["sharpe"]) < 1.5, (
                f"random model regime {regime!r} Sharpe={m['sharpe']:.2f}"
            )

    def test_positive_drift_returns_positive_sharpe(self) -> None:
        n = 100
        returns = pd.Series(
            np.full(n, 0.001),
            index=pd.bdate_range("2020-01-02", periods=n),
        )
        labels = pd.Series("a", index=returns.index)
        per_regime = compute_regime_metrics(returns, labels)
        assert per_regime["a"]["sharpe"] > 0.0

    def test_mismatched_indices_raise(self) -> None:
        returns = _returns([0.01, 0.02, 0.03], start="2020-01-02")
        labels = _labels(["a", "a", "a"], start="2021-01-04")
        with pytest.raises(ValueError, match="index"):
            compute_regime_metrics(returns, labels)


# ─── regime_dm_test ──────────────────────────────────────────────────────────


class TestRegimeDMTest:
    def test_runs_dm_per_regime(self) -> None:
        rng = np.random.default_rng(7)
        n = 200
        idx = pd.bdate_range("2020-01-02", periods=n)
        errors_a = pd.Series(rng.normal(0.0, 0.1, n), index=idx)
        errors_b = pd.Series(rng.normal(0.0, 1.0, n), index=idx)
        labels = pd.Series(rng.choice(["a", "b"], size=n), index=idx)
        out = regime_dm_test(errors_a, errors_b, labels)
        assert set(out.keys()) == {"a", "b"}
        for dm in out.values():
            assert dm is None or isinstance(dm, DMResult)

    def test_better_model_yields_low_p_value(self) -> None:
        rng = np.random.default_rng(11)
        n = 300
        idx = pd.bdate_range("2020-01-02", periods=n)
        errors_a = pd.Series(rng.normal(0.0, 0.05, n), index=idx)
        errors_b = pd.Series(rng.normal(0.0, 1.0, n), index=idx)
        labels = pd.Series("a", index=idx)
        out = regime_dm_test(errors_a, errors_b, labels)
        assert out["a"] is not None
        assert out["a"].p_value < 0.05

    def test_thin_regime_returns_none(self) -> None:
        """A regime with fewer than 4 observations cannot be DM-tested
        — return None rather than raising."""
        idx = pd.bdate_range("2020-01-02", periods=5)
        errors_a = pd.Series([0.0, 0.0, 0.0, 0.0, 0.0], index=idx)
        errors_b = pd.Series([0.1, 0.2, 0.1, 0.2, 0.15], index=idx)
        labels = pd.Series(["thin", "thin", "thin", "wide", "wide"], index=idx)
        out = regime_dm_test(errors_a, errors_b, labels)
        assert out["thin"] is None  # n=3 < 4 → cannot DM-test
        assert out["wide"] is None  # n=2 < 4 → cannot DM-test


# ─── phase4a_gate_report ─────────────────────────────────────────────────────


class TestPhase4aGateReport:
    def test_gate_passes_when_gbm_beats_arima_in_two_of_three_regimes(self) -> None:
        rng = np.random.default_rng(21)
        n = 600
        idx = pd.bdate_range("2010-01-04", periods=n)
        labels = pd.Series("qe_bull", index=idx, dtype=object)
        labels.iloc[400:500] = "covid"
        labels.iloc[500:] = "rate_cycle"

        # GBM positive drift in qe_bull + rate_cycle; flat in covid.
        gbm_returns = pd.Series(rng.normal(0.0, 0.005, n), index=idx)
        gbm_returns.loc[labels == "qe_bull"] += 0.002
        gbm_returns.loc[labels == "rate_cycle"] += 0.002

        arima_returns = pd.Series(rng.normal(0.0, 0.005, n), index=idx)

        # GBM forecast errors smaller in qe_bull + rate_cycle, larger in covid.
        gbm_errors = pd.Series(rng.normal(0.0, 0.05, n), index=idx)
        covid_mask = (labels == "covid").to_numpy()
        gbm_errors.loc[covid_mask] = rng.normal(0.0, 1.0, covid_mask.sum())
        arima_errors = pd.Series(rng.normal(0.0, 1.0, n), index=idx)

        gbm = _make_result(gbm_returns, gbm_errors)
        arima = _make_result(arima_returns, arima_errors)

        report = phase4a_gate_report(gbm, arima, labels)
        assert report["pass_count"] >= 2
        assert report["gate_passed"] is True
        assert set(report["per_regime"].keys()) >= {"qe_bull", "covid", "rate_cycle"}

    def test_gate_fails_when_gbm_loses_in_two_of_three_regimes(self) -> None:
        rng = np.random.default_rng(22)
        n = 600
        idx = pd.bdate_range("2010-01-04", periods=n)
        labels = pd.Series("qe_bull", index=idx, dtype=object)
        labels.iloc[400:500] = "covid"
        labels.iloc[500:] = "rate_cycle"

        # ARIMA positive drift in qe_bull + rate_cycle; GBM flat.
        gbm_returns = pd.Series(rng.normal(0.0, 0.005, n), index=idx)
        arima_returns = pd.Series(rng.normal(0.0, 0.005, n), index=idx)
        arima_returns.loc[labels == "qe_bull"] += 0.002
        arima_returns.loc[labels == "rate_cycle"] += 0.002

        gbm = _make_result(gbm_returns, pd.Series(rng.normal(0.0, 1.0, n), index=idx))
        arima = _make_result(arima_returns, pd.Series(rng.normal(0.0, 0.1, n), index=idx))

        report = phase4a_gate_report(gbm, arima, labels)
        assert report["pass_count"] <= 1
        assert report["gate_passed"] is False

    def test_report_shape(self) -> None:
        n = 100
        idx = pd.bdate_range("2010-01-04", periods=n)
        rng = np.random.default_rng(33)
        labels = pd.Series("qe_bull", index=idx, dtype=object)
        labels.iloc[60:80] = "covid"
        labels.iloc[80:] = "rate_cycle"

        ret = pd.Series(rng.normal(0.0, 0.005, n), index=idx)
        err = pd.Series(rng.normal(0.0, 0.5, n), index=idx)
        gbm = _make_result(ret, err)
        arima = _make_result(ret + 0.001, err * 2)

        report = phase4a_gate_report(gbm, arima, labels)
        for key in ("per_regime", "gate_passed", "pass_count", "dm_p_values", "regimes_required"):
            assert key in report
