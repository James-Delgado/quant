"""Tests for the Project B1 Milestone 3 full-panel target-arm runner.

Scope: plumbing only — argparse, config-hash determinism, idempotency /
``--force`` / decoupled ledger logging, the 25-col M6 feature-set parity with the
Phase 4A runner (METHODOLOGY §6 drift contract), and end-to-end ``--smoke`` runs
on a synthetic panel that complete in seconds. These tests do NOT exercise the
full-panel arm runs — those run via the script and are consumed by nb11.
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


def _load_script(name: str, filename: str) -> Any:
    """Load a ``scripts/<filename>`` module by path (scripts/ is not a package)."""
    path = Path(__file__).resolve().parent.parent / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


runner = _load_script("b1_runner", "run_b1_arms.py")
phase4a_runner = _load_script("phase4a_runner_for_b1", "run_phase4a_arms.py")


# ─── argparse ───────────────────────────────────────────────────────────────


class TestArgparse:
    def test_parse_target_with_output_dir(self, tmp_path: Path) -> None:
        parser = runner.build_parser()
        ns = parser.parse_args(["--target", "directional_5d", "--output-dir", str(tmp_path)])
        assert ns.target == "directional_5d"
        assert ns.output_dir == tmp_path
        assert ns.smoke is False
        assert ns.force is False
        assert ns.log_ledger is False

    def test_missing_target_raises_systemexit(self) -> None:
        parser = runner.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_invalid_target_rejected(self) -> None:
        parser = runner.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--target", "nonsense"])

    def test_all_four_targets_accepted(self) -> None:
        parser = runner.build_parser()
        for target in ("drawdown_21d", "realized_vol_21d", "directional_5d", "directional_21d"):
            ns = parser.parse_args(["--target", target])
            assert ns.target == target

    def test_default_output_dir(self) -> None:
        parser = runner.build_parser()
        ns = parser.parse_args(["--target", "drawdown_21d"])
        assert ns.output_dir == Path("data/b1")

    def test_ledger_defaults(self) -> None:
        parser = runner.build_parser()
        ns = parser.parse_args(["--target", "drawdown_21d", "--log-ledger"])
        assert ns.log_ledger is True
        assert ns.ledger_prd == "b1"
        assert ns.ledger_milestone == "B1-M3"
        assert ns.ledger_n_comparisons == 3  # len(REQUIRED_REGIMES)
        assert ns.ledger_verdict == "inconclusive"


# ─── Config-hash determinism ────────────────────────────────────────────────


class TestConfigHash:
    def test_identical_args_produce_identical_hash(self) -> None:
        cfg1 = runner._build_run_config("directional_5d", label_horizon=5)
        cfg2 = runner._build_run_config("directional_5d", label_horizon=5)
        assert runner._hash_config(cfg1) == runner._hash_config(cfg2)

    def test_different_targets_produce_different_hashes(self) -> None:
        a = runner._build_run_config("directional_5d", label_horizon=5)
        b = runner._build_run_config("directional_21d", label_horizon=21)
        assert runner._hash_config(a) != runner._hash_config(b)

    def test_different_horizons_produce_different_hashes(self) -> None:
        h5 = runner._build_run_config("directional_5d", label_horizon=5)
        h21 = runner._build_run_config("directional_5d", label_horizon=21)
        assert runner._hash_config(h5) != runner._hash_config(h21)

    def test_hash_is_64_hex_chars(self) -> None:
        h = runner._hash_config(runner._build_run_config("drawdown_21d", label_horizon=21))
        assert len(h) == 64
        int(h, 16)


# ─── Feature-set parity (METHODOLOGY §6 drift contract) ──────────────────────


class TestFeatureParity:
    """B1 holds the feature set fixed at M6 — it must equal the Phase 4A runner's."""

    def test_b1_feature_columns_equal_phase4a(self) -> None:
        assert runner.FINAL_FEATURE_COLUMNS == phase4a_runner.FINAL_FEATURE_COLUMNS

    def test_column_count_is_25(self) -> None:
        assert len(runner.FINAL_FEATURE_COLUMNS) == 25

    def test_columns_are_unique(self) -> None:
        cols = runner.FINAL_FEATURE_COLUMNS
        assert len(set(cols)) == len(cols)

    def test_valid_targets_match_catalog(self) -> None:
        from quant.features.targets import TARGET_CATALOG

        assert set(runner.VALID_TARGETS) == set(TARGET_CATALOG)

    def test_self_comparison_count_is_twelve(self) -> None:
        # 4 targets × 3 required regimes — pinned before any result.
        assert runner.N_SELF_COMPARISONS == 12


# ─── Idempotency / --force / decoupled ledger logging ────────────────────────


class TestIdempotency:
    def _stub_meta(self, target_dir: Path) -> Path:
        target_dir.mkdir(parents=True, exist_ok=True)
        meta = target_dir / "metadata.json"
        meta.write_text(json.dumps({"target": "directional_5d", "stub": True}))
        return meta

    def test_existing_checkpoint_skips_fit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target_dir = tmp_path / "smoke_directional_5d"
        self._stub_meta(target_dir)

        def _fail(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("must not fit when a checkpoint exists and --force is off")

        monkeypatch.setattr(runner, "_build_predictions_frame", _fail)
        rc = runner._run_target("directional_5d", output_dir=tmp_path, smoke=True, force=False)
        assert rc == 0
        # Stub metadata preserved untouched.
        assert json.loads((target_dir / "metadata.json").read_text()) == {
            "target": "directional_5d",
            "stub": True,
        }

    def test_force_overrides_idempotency(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target_dir = tmp_path / "smoke_drawdown_21d"
        self._stub_meta(target_dir)

        called: dict[str, int] = {"n": 0}
        real = runner._build_predictions_frame

        def _counting(*args: Any, **kwargs: Any) -> Any:
            called["n"] += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(runner, "_build_predictions_frame", _counting)
        rc = runner._run_target("drawdown_21d", output_dir=tmp_path, smoke=True, force=True)
        assert rc == 0
        assert called["n"] == 1
        meta = json.loads((target_dir / "metadata.json").read_text())
        assert meta.get("stub") is None
        assert meta["target"] == "drawdown_21d"
        assert "config_hash" in meta

    def test_skipped_checkpoint_still_logs_ledger_without_fit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A non-smoke checkpoint with logging requested must log from metadata
        # without re-fitting (the decoupled post-gate logging path).
        target_dir = tmp_path / "directional_21d"
        target_dir.mkdir(parents=True)
        (target_dir / "metadata.json").write_text(
            json.dumps({"config_hash": "deadbeef", "started_at": "x", "finished_at": "y"})
        )

        def _fail(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("must not fit when logging an existing checkpoint")

        logged: dict[str, Any] = {}

        class _Entry:
            id = "ledger-2026-06-27-9999"

        def _fake_record_run(meta: Any, **kw: Any) -> object:
            logged["meta"] = meta
            logged["kw"] = kw
            return _Entry()

        monkeypatch.setattr(runner, "_build_predictions_frame", _fail)
        monkeypatch.setattr(runner, "record_run", _fake_record_run)
        rc = runner._run_target(
            "directional_21d",
            output_dir=tmp_path,
            smoke=False,
            force=False,
            ledger_meta={
                "prd": "b1",
                "milestone": "B1-M3",
                "preregistration": "x",
                "n_comparisons": 3,
                "verdict": "gate_failed",
                "agent": "human",
                "notes": "",
            },
        )
        assert rc == 0
        assert logged["kw"]["verdict"] == "gate_failed"
        assert logged["meta"] == target_dir / "metadata.json"


# ─── Smoke end-to-end (directional covers GBM + ARIMA + both Sharpe arms) ─────


class TestSmokeDirectional:
    @pytest.fixture(scope="class")
    def smoke_dir(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        out = tmp_path_factory.mktemp("b1_smoke")
        t0 = time.monotonic()
        rc = runner._run_target("directional_5d", output_dir=out, smoke=True, force=False)
        elapsed = time.monotonic() - t0
        assert rc == 0
        assert elapsed < 120.0, f"smoke run exceeded 120s budget ({elapsed:.1f}s)"
        return out / "smoke_directional_5d"

    def test_artifacts_written(self, smoke_dir: Path) -> None:
        assert (smoke_dir / "metadata.json").exists()
        assert (smoke_dir / "predictions.parquet").exists()
        # directional → both Sharpe-arm return series checkpointed.
        assert (smoke_dir / "returns_gbm.parquet").exists()
        assert (smoke_dir / "returns_arima.parquet").exists()

    def test_predictions_parquet_schema(self, smoke_dir: Path) -> None:
        df = pd.read_parquet(smoke_dir / "predictions.parquet")
        assert {"symbol", "y_true", "gbm_pred", "arima_pred"} <= set(df.columns)
        assert len(df) > 0
        assert isinstance(df.index, pd.DatetimeIndex)

    def test_metadata_required_keys(self, smoke_dir: Path) -> None:
        meta = json.loads((smoke_dir / "metadata.json").read_text())
        required = {
            "target", "milestone", "smoke", "git_sha", "started_at", "finished_at",
            "elapsed_seconds", "config_hash", "run_config", "invariant_parity_audit",
            "n_symbols_in_panel", "symbols", "n_oos_rows", "n_oos_dates",
            "label_horizon", "has_sharpe_arm", "drawdown_base_rate",
            "sharpe_arm_aggregate", "n_self_comparisons",
        }
        assert not (required - set(meta.keys()))
        assert meta["target"] == "directional_5d"
        assert meta["milestone"] == "B1-M3"
        assert meta["label_horizon"] == 5
        assert meta["has_sharpe_arm"] is True

    def test_invariant_parity_audit_recorded(self, smoke_dir: Path) -> None:
        meta = json.loads((smoke_dir / "metadata.json").read_text())
        assert meta["invariant_parity_audit"] == runner.INVARIANT_PARITY_AUDIT
        assert "walkforward_splits" in meta["invariant_parity_audit"]


class TestSmokeDrawdown:
    """The non-directional path: vol_proxy baseline, base rate, no Sharpe arms."""

    @pytest.fixture(scope="class")
    def smoke_dir(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        out = tmp_path_factory.mktemp("b1_smoke_dd")
        rc = runner._run_target("drawdown_21d", output_dir=out, smoke=True, force=False)
        assert rc == 0
        return out / "smoke_drawdown_21d"

    def test_predictions_has_vol_proxy_no_returns(self, smoke_dir: Path) -> None:
        df = pd.read_parquet(smoke_dir / "predictions.parquet")
        assert {"symbol", "y_true", "gbm_pred", "vol_proxy"} <= set(df.columns)
        assert not (smoke_dir / "returns_gbm.parquet").exists()

    def test_metadata_base_rate_and_no_sharpe(self, smoke_dir: Path) -> None:
        meta = json.loads((smoke_dir / "metadata.json").read_text())
        assert meta["has_sharpe_arm"] is False
        assert meta["label_horizon"] == 21
        # base rate is a probability in [0, 1].
        assert 0.0 <= float(meta["drawdown_base_rate"]) <= 1.0
