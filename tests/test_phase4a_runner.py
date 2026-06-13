"""Tests for the Phase 4A Milestone 6 headless runner.

Scope: plumbing only — argparse, idempotency, --force override, the
sample-weight-parity-audit metadata field, config-hash determinism, and
an end-to-end ``--smoke arima`` run on a synthetic 3-symbol panel that
completes in seconds.

These tests do NOT exercise the full-panel arm runs — those are run
manually via the script and consumed by nb09.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from quant.backtest.harness import BacktestResult

# Load the runner script as a module without making ``scripts/`` a
# package. setuptools.packages.find is scoped to ``src/`` (see
# pyproject.toml), so we deliberately avoid adding an __init__.py under
# scripts/. importlib.util gives us a real, monkeypatchable module
# object — equivalent to ``import scripts.run_phase4a_arms`` for the
# purposes of these tests.
_RUNNER_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "run_phase4a_arms.py"
)
_spec = importlib.util.spec_from_file_location("phase4a_runner", _RUNNER_PATH)
assert _spec is not None and _spec.loader is not None
runner = importlib.util.module_from_spec(_spec)
sys.modules["phase4a_runner"] = runner
_spec.loader.exec_module(runner)


# ─── argparse ───────────────────────────────────────────────────────────────


class TestArgparse:
    """Parser shape: required args, choices, defaults."""

    def test_parse_signed_with_output_dir(self, tmp_path: Path) -> None:
        parser = runner.build_parser()
        ns = parser.parse_args(["--arm", "signed", "--output-dir", str(tmp_path)])
        assert ns.arm == "signed"
        assert ns.output_dir == tmp_path
        assert ns.smoke is False
        assert ns.force is False

    def test_missing_arm_raises_systemexit(self) -> None:
        parser = runner.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_invalid_arm_rejected(self) -> None:
        parser = runner.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--arm", "nonsense"])

    def test_all_four_arms_accepted(self) -> None:
        parser = runner.build_parser()
        for arm in ("signed", "vol_scaled", "triple_barrier", "arima"):
            ns = parser.parse_args(["--arm", arm])
            assert ns.arm == arm

    def test_default_output_dir(self) -> None:
        parser = runner.build_parser()
        ns = parser.parse_args(["--arm", "arima"])
        assert ns.output_dir == Path("data/phase4a")

    def test_smoke_and_force_flags(self) -> None:
        parser = runner.build_parser()
        ns = parser.parse_args(["--arm", "arima", "--smoke", "--force"])
        assert ns.smoke is True
        assert ns.force is True


# ─── Config-hash determinism ────────────────────────────────────────────────


class TestConfigHash:
    """SHA-256 over a pickled dict — identical args → identical hash."""

    def test_identical_args_produce_identical_hash(self) -> None:
        cfg1 = runner._build_run_config(arm="signed", label_horizon=1)
        cfg2 = runner._build_run_config(arm="signed", label_horizon=1)
        assert runner._hash_config(cfg1) == runner._hash_config(cfg2)

    def test_different_arms_produce_different_hashes(self) -> None:
        cfg_signed = runner._build_run_config(arm="signed", label_horizon=1)
        cfg_arima = runner._build_run_config(arm="arima", label_horizon=1)
        assert runner._hash_config(cfg_signed) != runner._hash_config(cfg_arima)

    def test_different_horizons_produce_different_hashes(self) -> None:
        # triple_barrier (h=5) vs signed (h=1) must hash differently even
        # if the arm string were equal — the horizon is part of the
        # GBMModel construction config.
        cfg_h1 = runner._build_run_config(arm="signed", label_horizon=1)
        cfg_h5 = runner._build_run_config(arm="signed", label_horizon=5)
        assert runner._hash_config(cfg_h1) != runner._hash_config(cfg_h5)

    def test_hash_is_64_hex_chars(self) -> None:
        cfg = runner._build_run_config(arm="signed", label_horizon=1)
        h = runner._hash_config(cfg)
        assert len(h) == 64
        int(h, 16)  # raises if not pure hex


# ─── Idempotency / --force override ─────────────────────────────────────────


class TestIdempotency:
    """A pre-existing metadata.json gates the run; --force overrides."""

    def _make_dummy_metadata(self, arm_dir: Path) -> Path:
        arm_dir.mkdir(parents=True, exist_ok=True)
        meta = arm_dir / "metadata.json"
        meta.write_text(json.dumps({"arm": "arima", "stub": True}))
        return meta

    def test_existing_checkpoint_skips_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Use smoke=True so the arm subdirectory is "smoke_arima" —
        # matches what _run_arm checks when --smoke is set. The stub
        # metadata must live in the SAME directory the runner looks at.
        arm_dir = tmp_path / "smoke_arima"
        self._make_dummy_metadata(arm_dir)

        called: dict[str, int] = {"n": 0}

        def _fail(*args: Any, **kwargs: Any) -> BacktestResult:
            called["n"] += 1
            raise AssertionError(
                "run_portfolio_backtest must not be called when a "
                "checkpoint already exists and --force is not set"
            )

        monkeypatch.setattr(runner, "run_portfolio_backtest", _fail)
        rc = runner._run_arm(arm="arima", output_dir=tmp_path, smoke=True, force=False)
        assert rc == 0
        assert called["n"] == 0
        # The stub metadata is preserved untouched.
        meta = json.loads((arm_dir / "metadata.json").read_text())
        assert meta == {"arm": "arima", "stub": True}

    def test_force_overrides_idempotency(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # NB: smoke directory is "smoke_arima", not "arima". To prove
        # --force triggers the run path even when a checkpoint exists,
        # we pre-create the smoke_arima dir + metadata.
        arm_dir = tmp_path / "smoke_arima"
        self._make_dummy_metadata(arm_dir)

        called: dict[str, int] = {"n": 0}
        real_run = runner.run_portfolio_backtest

        def _counting(*args: Any, **kwargs: Any) -> BacktestResult:
            called["n"] += 1
            return real_run(*args, **kwargs)

        monkeypatch.setattr(runner, "run_portfolio_backtest", _counting)
        rc = runner._run_arm(arm="arima", output_dir=tmp_path, smoke=True, force=True)
        assert rc == 0
        assert called["n"] == 1, "--force must cause exactly one run"
        # Metadata was overwritten with the real result, not the stub.
        meta = json.loads((arm_dir / "metadata.json").read_text())
        assert meta.get("stub") is None
        assert meta["arm"] == "arima"
        assert "config_hash" in meta


# ─── Smoke end-to-end ───────────────────────────────────────────────────────


class TestSmokeArima:
    """End-to-end smoke run on the synthetic panel — fast (< 60s)."""

    @pytest.fixture(scope="class")
    def smoke_dir(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        out = tmp_path_factory.mktemp("phase4a_smoke")
        t0 = time.monotonic()
        rc = runner._run_arm(arm="arima", output_dir=out, smoke=True, force=False)
        elapsed = time.monotonic() - t0
        assert rc == 0
        assert elapsed < 60.0, (
            f"smoke run exceeded 60s budget ({elapsed:.1f}s) — investigate "
            "before letting it land"
        )
        return out / "smoke_arima"

    def test_artifacts_written(self, smoke_dir: Path) -> None:
        assert (smoke_dir / "metadata.json").exists()
        assert (smoke_dir / "oos_returns.parquet").exists()
        assert (smoke_dir / "oos_forecast_errors.parquet").exists()

    def test_oos_returns_parquet_loads(self, smoke_dir: Path) -> None:
        df = pd.read_parquet(smoke_dir / "oos_returns.parquet")
        assert "oos_returns" in df.columns
        assert len(df) > 0
        assert isinstance(df.index, pd.DatetimeIndex)

    def test_metadata_required_keys(self, smoke_dir: Path) -> None:
        meta = json.loads((smoke_dir / "metadata.json").read_text())
        required = {
            "arm",
            "smoke",
            "git_sha",
            "started_at",
            "finished_at",
            "elapsed_seconds",
            "config_hash",
            "run_config",
            "sample_weight_parity_audit",
            "n_oos_bars",
            "n_folds",
            "oos_start",
            "oos_end",
            "aggregate_sharpe",
            "aggregate_max_dd",
            "label_horizon",
        }
        missing = required - set(meta.keys())
        assert not missing, f"metadata missing required keys: {missing}"

    def test_sample_weight_parity_audit_is_recorded(self, smoke_dir: Path) -> None:
        meta = json.loads((smoke_dir / "metadata.json").read_text())
        audit = meta["sample_weight_parity_audit"]
        assert isinstance(audit, str)
        # Verbatim match — guarantees the report's reproducibility
        # appendix can quote the runner's own audit text.
        assert audit == runner.SAMPLE_WEIGHT_PARITY_AUDIT
        # Sanity: the audit text mentions the key facts the plan asked
        # the runner to record.
        assert "compute_sample_weights" in audit
        assert "label_horizon" in audit

    def test_smoke_metadata_marks_arm_correctly(self, smoke_dir: Path) -> None:
        meta = json.loads((smoke_dir / "metadata.json").read_text())
        assert meta["arm"] == "arima"
        assert meta["smoke"] is True
        assert meta["label_horizon"] == 1  # ARIMA → signed_returns labels


# ─── Final feature contract ─────────────────────────────────────────────────


class TestFinalFeatureColumns:
    """The 25-column contract is fixed and order-sensitive."""

    def test_column_count_is_25(self) -> None:
        assert len(runner.FINAL_FEATURE_COLUMNS) == 25

    def test_columns_are_unique(self) -> None:
        cols = runner.FINAL_FEATURE_COLUMNS
        assert len(set(cols)) == len(cols), "duplicate columns in FINAL_FEATURE_COLUMNS"

    def test_mom_21d_at_index_5(self) -> None:
        # The portfolio harness + nb02's MomentumBaseline depend on
        # mom_21d being at index 5 — protect the positional contract.
        assert runner.FINAL_FEATURE_COLUMNS[5] == "mom_21d"

    def test_sentiment_and_xs_rank_present(self) -> None:
        cols = set(runner.FINAL_FEATURE_COLUMNS)
        assert {"sentiment_score", "doc_count", "has_coverage"} <= cols
        assert "xs_rank_vol_21d" in cols
        assert {"vix_regime", "curve_inverted", "vol_regime_ratio", "trend_regime"} <= cols
