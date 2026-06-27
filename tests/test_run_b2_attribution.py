"""Tests for the Project B2 Milestone 2 OOS-attribution runner.

Scope: plumbing only — the 25-col M6 feature-set parity with the Phase 4A /
B1 runners (METHODOLOGY §6 drift contract), the 7-candidate G2/G3 surface, the
add-one baseline relationship, config-hash determinism, and argparse defaults.
These tests do NOT exercise the slice attribution run — that runs via the script
(``--force``) and is consumed by nb15 / B2-M3, checkpoint-only (METHODOLOGY §7).
The end-to-end ``--smoke`` plumbing is validated by running the script directly
(it fits real GBMs, so it is too slow for a unit test).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def _load_script(name: str, filename: str) -> Any:
    """Load a ``scripts/<filename>`` module by path (scripts/ is not a package)."""
    path = Path(__file__).resolve().parent.parent / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


runner = _load_script("b2_runner", "run_b2_attribution.py")
phase4a_runner = _load_script("phase4a_runner_for_b2", "run_phase4a_arms.py")


class TestFeatureSetParity:
    def test_m6_feature_set_matches_phase4a(self):
        # METHODOLOGY §6 — the B2 G1 surface must be the SAME frozen M6 25-col set
        # the Phase 4A runner uses, or the attribution scores a different model.
        assert runner.FINAL_FEATURE_COLUMNS == phase4a_runner.FINAL_FEATURE_COLUMNS

    def test_m6_set_is_25_columns(self):
        assert len(runner.FINAL_FEATURE_COLUMNS) == 25
        assert len(set(runner.FINAL_FEATURE_COLUMNS)) == 25

    def test_base_is_first_17_of_m6(self):
        assert len(runner.BASE_FEATURES_17) == 17
        assert runner.FINAL_FEATURE_COLUMNS[:17] == runner.BASE_FEATURES_17

    def test_candidates_are_seven_unique(self):
        assert len(runner.CANDIDATES) == 7
        assert len(set(runner.CANDIDATES)) == 7

    def test_candidates_resolve_after_xs_build(self):
        # The 3 xs-rank candidates require add_cross_sectional_features over
        # XS_COLUMNS; the 4 regime candidates are plain build_features columns.
        xs = {c for c in runner.CANDIDATES if c.startswith("xs_rank_")}
        assert xs == {"xs_rank_ret_21d", "xs_rank_ret_252d", "xs_rank_vol_21d"}
        for base_col in ("ret_21d", "ret_252d", "vol_21d"):
            assert base_col in runner.XS_COLUMNS


class TestConfigHash:
    def test_deterministic(self):
        assert runner._hash_config(runner._build_run_config(False)) == runner._hash_config(
            runner._build_run_config(False)
        )

    def test_smoke_and_real_differ(self):
        # Different GBM budgets → different config hash → no ledger collision.
        assert runner._hash_config(runner._build_run_config(True)) != runner._hash_config(
            runner._build_run_config(False)
        )

    def test_run_config_pins_slice_and_label(self):
        cfg = runner._build_run_config(False)
        assert cfg["slice"]["symbols"] == list(runner.DEMO_SYMBOLS)
        assert cfg["slice"]["start"] == runner.DEMO_START
        assert cfg["label"] == {"scheme": "signed_returns", "horizon": runner.LABEL_HORIZON}
        assert cfg["gbm_params"]["n_iter"] == runner.GBM_N_ITER


class TestArgparse:
    def test_defaults(self):
        args = runner.build_parser().parse_args([])
        assert args.output_dir == "data/b2"
        assert args.smoke is False
        assert args.force is False
        assert args.log_ledger is False

    def test_flags(self):
        args = runner.build_parser().parse_args(
            ["--smoke", "--force", "--log-ledger", "--output-dir", "/tmp/x"]
        )
        assert args.smoke and args.force and args.log_ledger
        assert args.output_dir == "/tmp/x"


class TestLedgerDiscipline:
    def test_n_comparisons_is_one(self):
        # B2 PRD: the single validated method (OOS permutation); ablation is the
        # reference, not a tested claim → minimal contribution to the deflation N.
        assert runner.N_COMPARISONS == 1
