"""Tests for src/quant/backtest/regime_metrics.py."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest.harness import BacktestResult
from quant.backtest.regime_metrics import (
    b1_gate_report,
    compute_regime_metrics,
    dsr_aware_gate_report,
    phase4a_gate_report,
    regime_dm_test,
)
from quant.backtest.statistics import DMResult
from quant.features.targets import TARGET_CATALOG
from quant.ledger import cumulative_trial_count


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


# ─── dsr_aware_gate_report ───────────────────────────────────────────────────


def _stage1_pass_fixture() -> tuple[BacktestResult, BacktestResult, pd.Series]:
    """A stage-1-passing setup with a *modest* aggregate edge.

    GBM beats ARIMA Sharpe in qe_bull + rate_cycle (small +0.0003 drift) and has
    uniformly smaller forecast errors (DM-significant), so the regime gate
    passes. The aggregate Sharpe is deliberately modest (~0.8 annualized) so the
    DSR second stage can be flipped purely by the deflation N.
    """
    rng = np.random.default_rng(101)
    n = 600
    idx = pd.bdate_range("2010-01-04", periods=n)
    labels = pd.Series("qe_bull", index=idx, dtype=object)
    labels.iloc[400:500] = "covid"
    labels.iloc[500:] = "rate_cycle"

    gbm_returns = pd.Series(rng.normal(0.0, 0.005, n), index=idx)
    gbm_returns.loc[labels == "qe_bull"] += 0.0003
    gbm_returns.loc[labels == "rate_cycle"] += 0.0003
    arima_returns = pd.Series(rng.normal(0.0, 0.005, n), index=idx)

    gbm_errors = pd.Series(rng.normal(0.0, 0.05, n), index=idx)
    arima_errors = pd.Series(rng.normal(0.0, 1.0, n), index=idx)

    return (
        _make_result(gbm_returns, gbm_errors),
        _make_result(arima_returns, arima_errors),
        labels,
    )


class TestDsrAwareGateReport:
    def test_both_stages_pass(self) -> None:
        gbm, arima, labels = _stage1_pass_fixture()
        report = dsr_aware_gate_report(gbm, arima, labels, n_trials=1)
        assert report["stage1_passed"] is True
        assert report["dsr_passed"] is True
        assert report["gate_passed"] is True

    def test_stage1_passes_but_deflation_fails(self) -> None:
        """Same modest edge, but a huge trial count lifts the benchmark above it."""
        gbm, arima, labels = _stage1_pass_fixture()
        report = dsr_aware_gate_report(gbm, arima, labels, n_trials=1_000_000)
        assert report["stage1_passed"] is True
        assert report["dsr_passed"] is False
        assert report["gate_passed"] is False  # combined gate requires BOTH

    def test_stage1_failure_forces_overall_failure(self) -> None:
        """If the regime gate fails, the combined gate fails regardless of DSR."""
        rng = np.random.default_rng(202)
        n = 600
        idx = pd.bdate_range("2010-01-04", periods=n)
        labels = pd.Series("qe_bull", index=idx, dtype=object)
        labels.iloc[400:500] = "covid"
        labels.iloc[500:] = "rate_cycle"

        # ARIMA wins the Sharpe regimes → stage 1 fails.
        gbm_returns = pd.Series(rng.normal(0.0, 0.005, n), index=idx)
        arima_returns = pd.Series(rng.normal(0.0, 0.005, n), index=idx)
        arima_returns.loc[labels == "qe_bull"] += 0.002
        arima_returns.loc[labels == "rate_cycle"] += 0.002
        gbm = _make_result(gbm_returns, pd.Series(rng.normal(0.0, 1.0, n), index=idx))
        arima = _make_result(arima_returns, pd.Series(rng.normal(0.0, 0.1, n), index=idx))

        report = dsr_aware_gate_report(gbm, arima, labels, n_trials=1)
        assert report["stage1_passed"] is False
        assert report["gate_passed"] is False

    def test_n_trials_defaults_to_ledger(self) -> None:
        """With n_trials=None the gate deflates against the ledger's cumulative N."""
        gbm, arima, labels = _stage1_pass_fixture()
        report = dsr_aware_gate_report(gbm, arima, labels)
        assert report["n_trials"] == cumulative_trial_count()

    def test_report_carries_stage1_and_dsr_keys(self) -> None:
        gbm, arima, labels = _stage1_pass_fixture()
        report = dsr_aware_gate_report(gbm, arima, labels, n_trials=10)
        for key in (
            "per_regime", "gate_passed", "pass_count", "dm_p_values",
            "regimes_required", "stage1_passed", "dsr", "dsr_passed",
            "dsr_result", "n_trials", "sr_observed", "sr_benchmark",
        ):
            assert key in report
        assert 0.0 <= report["dsr"] <= 1.0

    def test_stage1_verdict_matches_phase4a_gate_report(self) -> None:
        """The wrapper must not perturb the stage-1 verdict."""
        gbm, arima, labels = _stage1_pass_fixture()
        stage1 = phase4a_gate_report(gbm, arima, labels)
        wrapped = dsr_aware_gate_report(gbm, arima, labels, n_trials=5)
        assert wrapped["stage1_passed"] == stage1["gate_passed"]
        assert wrapped["pass_count"] == stage1["pass_count"]


# ─── b1_gate_report ──────────────────────────────────────────────────────────


class TestB1GateReport:
    """The pre-committed B1 target-reframing gate (PRD b1-target-reframing)."""

    DRAWDOWN = TARGET_CATALOG["drawdown_21d"]          # 1 criterion: auc ΔAUC ≥ 0.02
    DIRECTIONAL = TARGET_CATALOG["directional_5d"]      # 2 criteria: auc + sharpe
    VOL = TARGET_CATALOG["realized_vol_21d"]            # 1 criterion: mae rel_reduction

    def _drawdown_pass_metrics(self) -> dict:
        return {
            "qe_bull": {"auc": {"variant": 0.60, "baseline": 0.50}},   # Δ 0.10 ✓
            "covid": {"auc": {"variant": 0.55, "baseline": 0.50}},     # Δ 0.05 ✓
            "rate_cycle": {"auc": {"variant": 0.50, "baseline": 0.51}},  # Δ -0.01 ✗
        }

    def _ci_one_excludes_zero(self) -> dict:
        return {
            "qe_bull": (0.01, 0.08),    # excludes 0
            "covid": (-0.02, 0.06),     # includes 0
            "rate_cycle": (-0.03, 0.02),  # includes 0
        }

    def test_all_stages_pass(self):
        report = b1_gate_report(
            self.DRAWDOWN,
            self._drawdown_pass_metrics(),
            self._ci_one_excludes_zero(),
            deflation_passed=True,
        )
        assert report["materiality_passed"] is True
        assert report["material_pass_count"] == 2
        assert report["significance_passed"] is True
        assert report["deflation_passed"] is True
        assert report["gate_passed"] is True
        assert report["target"] == "drawdown_21d"

    def test_materiality_fails_when_only_one_regime_meets(self):
        metrics = self._drawdown_pass_metrics()
        metrics["covid"]["auc"]["variant"] = 0.505  # Δ 0.005 < 0.02 now ✗
        report = b1_gate_report(
            self.DRAWDOWN, metrics, self._ci_one_excludes_zero(), deflation_passed=True
        )
        assert report["material_pass_count"] == 1
        assert report["materiality_passed"] is False
        assert report["gate_passed"] is False

    def test_significance_fails_when_no_ci_excludes_zero(self):
        ci = {
            "qe_bull": (-0.01, 0.08),
            "covid": (-0.02, 0.06),
            "rate_cycle": (-0.03, 0.02),
        }
        report = b1_gate_report(
            self.DRAWDOWN, self._drawdown_pass_metrics(), ci, deflation_passed=True
        )
        assert report["materiality_passed"] is True
        assert report["significance_passed"] is False
        assert report["gate_passed"] is False

    def test_deflation_fails_blocks_gate(self):
        report = b1_gate_report(
            self.DRAWDOWN,
            self._drawdown_pass_metrics(),
            self._ci_one_excludes_zero(),
            deflation_passed=False,
        )
        assert report["materiality_passed"] is True
        assert report["significance_passed"] is True
        assert report["deflation_passed"] is False
        assert report["gate_passed"] is False

    def test_directional_requires_both_auc_and_sharpe(self):
        # AUC clears in all three regimes, but Sharpe only in qe_bull -> a regime
        # counts as material only when BOTH criteria are met.
        metrics = {
            "qe_bull": {
                "auc": {"variant": 0.55, "baseline": 0.50},   # Δ 0.05 ✓
                "sharpe": {"variant": 0.40, "baseline": 0.20},  # Δ 0.20 ✓
            },
            "covid": {
                "auc": {"variant": 0.55, "baseline": 0.50},   # Δ 0.05 ✓
                "sharpe": {"variant": 0.25, "baseline": 0.20},  # Δ 0.05 < 0.10 ✗
            },
            "rate_cycle": {
                "auc": {"variant": 0.55, "baseline": 0.50},   # Δ 0.05 ✓
                "sharpe": {"variant": 0.22, "baseline": 0.20},  # Δ 0.02 < 0.10 ✗
            },
        }
        report = b1_gate_report(
            self.DIRECTIONAL,
            metrics,
            {"qe_bull": (0.01, 0.10)},
            deflation_passed=True,
        )
        assert report["material_pass_count"] == 1  # only qe_bull clears both
        assert report["materiality_passed"] is False
        assert report["gate_passed"] is False

    def test_rel_reduction_materiality_for_vol_target(self):
        # MAE lower is better: (baseline - variant)/baseline >= 0.05.
        metrics = {
            "qe_bull": {"mae": {"variant": 0.90, "baseline": 1.00}},   # 10% reduction ✓
            "covid": {"mae": {"variant": 0.94, "baseline": 1.00}},     # 6% reduction ✓
            "rate_cycle": {"mae": {"variant": 0.99, "baseline": 1.00}},  # 1% reduction ✗
        }
        report = b1_gate_report(
            self.VOL, metrics, {"qe_bull": (0.01, 0.2)}, deflation_passed=True
        )
        assert report["material_pass_count"] == 2
        assert report["gate_passed"] is True

    def test_per_regime_detail_records_delta_value(self):
        report = b1_gate_report(
            self.DRAWDOWN,
            self._drawdown_pass_metrics(),
            self._ci_one_excludes_zero(),
            deflation_passed=True,
        )
        qe = report["per_regime"]["qe_bull"]
        crit = qe["criteria"][0]
        assert crit["metric"] == "auc"
        assert crit["value"] == pytest.approx(0.10)
        assert crit["met"] is True
        assert qe["ci_excludes_zero"] is True

    def test_min_pass_override(self):
        # With min_pass=1 a single material regime is enough.
        metrics = self._drawdown_pass_metrics()
        metrics["covid"]["auc"]["variant"] = 0.505  # only qe_bull material now
        report = b1_gate_report(
            self.DRAWDOWN,
            metrics,
            self._ci_one_excludes_zero(),
            deflation_passed=True,
            min_pass=1,
        )
        assert report["material_pass_count"] == 1
        assert report["materiality_passed"] is True
        assert report["gate_passed"] is True

    def test_regimes_outside_required_are_ignored(self):
        metrics = self._drawdown_pass_metrics()
        metrics["low_vol"] = {"auc": {"variant": 0.99, "baseline": 0.50}}  # huge edge
        report = b1_gate_report(
            self.DRAWDOWN,
            metrics,
            self._ci_one_excludes_zero(),
            deflation_passed=True,
        )
        # low_vol is not in regimes_required -> does not change the count.
        assert report["material_pass_count"] == 2
        assert "low_vol" not in report["per_regime"]

    def test_missing_metric_is_not_material(self):
        metrics = {
            "qe_bull": {},  # no auc key -> criterion cannot be met
            "covid": {"auc": {"variant": 0.55, "baseline": 0.50}},
            "rate_cycle": {"auc": {"variant": 0.55, "baseline": 0.50}},
        }
        report = b1_gate_report(
            self.DRAWDOWN,
            metrics,
            {"covid": (0.01, 0.08)},
            deflation_passed=True,
        )
        assert report["per_regime"]["qe_bull"]["materiality_met"] is False
        assert report["material_pass_count"] == 2  # covid + rate_cycle
