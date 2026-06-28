"""Tests for src/quant/execution/strategy_registry.py + the G1 drift gate.

The drift gate (:func:`registry_drift_report`) is the milestone's teeth
(PRD ``.claude/prds/c6-strategy-registry.prd.md`` §Pre-committed gate G1): it
resolves every strategy reference and enforces the provenance gate, and must
report **0 unresolved references, 0 provenance violations** on the seeded
registry. The tests drive both the positive path (the real registry passes) and
crafted negatives (an unresolved model/feature/target, an enabled-without-
provenance strategy, a bad ledger ref) so a future edit that breaks the contract
fails CI naming the offender (METHODOLOGY §6 — both directions).
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from quant.execution.strategy_registry import (
    MODEL_REGISTRY,
    PLACEHOLDER_PROVENANCE,
    ConfidenceGate,
    RiskLimits,
    SizingPolicy,
    StrategySpec,
    enabled_strategies,
    known_targets,
    load_registry,
    registry_drift_report,
    resolve_model_class,
    strategy_view_models,
)
from quant.features.targets import TARGET_CATALOG


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #

def _spec(**overrides) -> StrategySpec:
    """A valid StrategySpec with the placeholder defaults, overridable per-field."""
    base = dict(
        id="s1",
        display_name="S1",
        description="a test strategy",
        model_ref="arima_baseline",
        feature_set_ref=[],
        target_ref="next_bar_return",
        universe=["SPY"],
        decision_rule="sign",
        cadence="daily",
        broker="alpaca_paper",
        enabled=True,
        provenance=PLACEHOLDER_PROVENANCE,
        created_at="2026-06-28T00:00:00Z",
    )
    base.update(overrides)
    return StrategySpec(**base)


def _registry(*specs: StrategySpec) -> dict[str, StrategySpec]:
    return {s.id: s for s in specs}


# Inputs that keep the drift report off the real catalogs/ledger for negatives.
_FAKE_CATALOG = {"ret_1d": None, "mom_21d": None}  # values unused; keys resolve
_TARGETS = {"next_bar_return", "directional_5d"}
_MODELS = {"arima_baseline", "gbm"}
_LEDGER_IDS = {"ledger-2026-06-13-0001"}


def _report(registry, **kw):
    """registry_drift_report with injected catalogs so it never hits real files."""
    defaults = dict(
        catalog=_FAKE_CATALOG,
        valid_targets=_TARGETS,
        valid_models=_MODELS,
        passed_ledger_ids=_LEDGER_IDS,
    )
    defaults.update(kw)
    return registry_drift_report(registry, **defaults)


# --------------------------------------------------------------------- #
# Loader behavior (mirrors tests/test_catalog.py)
# --------------------------------------------------------------------- #

class TestLoadRegistry:
    def test_default_registry_loads(self):
        registry = load_registry()
        assert isinstance(registry, dict)
        assert "arima_placeholder" in registry
        for sid, spec in registry.items():
            assert isinstance(spec, StrategySpec)
            assert spec.id == sid

    def test_seeded_placeholder_shape(self):
        registry = load_registry()
        ph = registry["arima_placeholder"]
        assert ph.enabled is True
        assert ph.provenance == PLACEHOLDER_PROVENANCE
        assert ph.model_ref == "arima_baseline"
        assert ph.feature_set_ref == []
        assert ph.target_ref == "next_bar_return"
        assert ph.decision_rule == "sign"
        assert ph.broker == "alpaca_paper"
        assert ph.sizing_policy.method == "fully_invested_equal_weight"
        assert ph.confidence_gate.method == "always_pass"

    def test_duplicate_ids_raise(self, tmp_path: Path):
        path = tmp_path / "dup.yaml"
        entry = textwrap.dedent(
            """
              - id: dup
                display_name: D
                description: d
                model_ref: arima_baseline
                target_ref: next_bar_return
                universe: [SPY]
                decision_rule: sign
                cadence: daily
                broker: alpaca_paper
                enabled: false
                provenance: ""
                created_at: "2026-06-28T00:00:00Z"
            """
        )
        path.write_text("strategies:\n" + entry + entry)
        with pytest.raises(ValueError, match=r"duplicate.*dup"):
            load_registry(path)

    def test_unknown_top_level_key_raises(self, tmp_path: Path):
        path = tmp_path / "bad_top.yaml"
        path.write_text("strategummies:\n  - id: x\n")
        with pytest.raises(ValueError, match=r"strategummies"):
            load_registry(path)

    def test_strategies_not_a_list_raises(self, tmp_path: Path):
        path = tmp_path / "not_list.yaml"
        path.write_text("strategies:\n  id: x\n")
        with pytest.raises(ValueError, match=r"must be a list"):
            load_registry(path)

    def test_unknown_field_raises(self, tmp_path: Path):
        path = tmp_path / "extra.yaml"
        path.write_text(
            textwrap.dedent(
                """
                strategies:
                  - id: x
                    display_name: X
                    description: d
                    model_ref: arima_baseline
                    target_ref: next_bar_return
                    universe: [SPY]
                    decision_rule: sign
                    cadence: daily
                    broker: alpaca_paper
                    enabled: false
                    provenance: ""
                    created_at: "2026-06-28T00:00:00Z"
                    surprise: nope
                """
            )
        )
        with pytest.raises(ValidationError):
            load_registry(path)

    def test_missing_required_field_raises(self, tmp_path: Path):
        path = tmp_path / "missing.yaml"
        path.write_text(
            textwrap.dedent(
                """
                strategies:
                  - id: x
                    display_name: X
                    description: d
                    model_ref: arima_baseline
                    target_ref: next_bar_return
                    universe: [SPY]
                    decision_rule: sign
                    cadence: daily
                    broker: alpaca_paper
                    enabled: false
                """
            )
        )
        with pytest.raises(ValidationError):
            load_registry(path)

    def test_bad_enum_value_raises(self, tmp_path: Path):
        path = tmp_path / "bad_enum.yaml"
        path.write_text(
            textwrap.dedent(
                """
                strategies:
                  - id: x
                    display_name: X
                    description: d
                    model_ref: arima_baseline
                    target_ref: next_bar_return
                    universe: [SPY]
                    decision_rule: coinflip
                    cadence: daily
                    broker: alpaca_paper
                    enabled: false
                    provenance: ""
                    created_at: "2026-06-28T00:00:00Z"
                """
            )
        )
        with pytest.raises(ValidationError):
            load_registry(path)

    def test_empty_universe_raises(self):
        with pytest.raises(ValidationError, match=r"universe"):
            _spec(universe=[])

    def test_empty_id_raises(self):
        with pytest.raises(ValidationError):
            _spec(id="  ")


# --------------------------------------------------------------------- #
# Sub-model placeholders pin the C6 contract
# --------------------------------------------------------------------- #

class TestPlaceholderSubModels:
    def test_sizing_defaults_to_equal_weight(self):
        assert SizingPolicy().method == "fully_invested_equal_weight"

    def test_confidence_gate_inert_by_default(self):
        assert ConfidenceGate().method == "always_pass"

    def test_risk_limits_defaults(self):
        rl = RiskLimits()
        assert rl.max_position == 1.0
        assert rl.max_drawdown_stop is None

    def test_non_positive_max_position_raises(self):
        with pytest.raises(ValidationError, match=r"max_position"):
            RiskLimits(max_position=0.0)

    def test_sizing_extra_field_forbidden(self):
        with pytest.raises(ValidationError):
            SizingPolicy(method="fully_invested_equal_weight", lookback=5)


# --------------------------------------------------------------------- #
# G1 gate — positive path on the real registry (the milestone's headline)
# --------------------------------------------------------------------- #

class TestG1OnRealRegistry:
    def test_real_registry_passes_g1(self):
        """The seeded registry resolves cleanly: 0 unresolved, 0 violations."""
        registry = load_registry()
        report = registry_drift_report(registry)  # real catalogs + ledger
        assert report.unresolved == [], report.unresolved
        assert report.provenance_violations == [], report.provenance_violations
        assert report.passed is True

    def test_known_targets_includes_builtin_and_catalog(self):
        kt = known_targets()
        assert "next_bar_return" in kt
        for target_id in TARGET_CATALOG:
            assert target_id in kt


# --------------------------------------------------------------------- #
# G1 gate — reference resolution negatives (registry -> code direction)
# --------------------------------------------------------------------- #

class TestReferenceResolution:
    def test_unknown_model_ref_unresolved(self):
        report = _report(_registry(_spec(model_ref="no_such_model")))
        assert report.unresolved == ["s1.model_ref -> no_such_model"]
        assert report.passed is False

    def test_unknown_feature_unresolved(self):
        report = _report(
            _registry(_spec(feature_set_ref=["ret_1d", "ghost_feature"]))
        )
        assert report.unresolved == ["s1.feature_set_ref -> ghost_feature"]

    def test_resolvable_features_pass(self):
        report = _report(_registry(_spec(feature_set_ref=["ret_1d", "mom_21d"])))
        assert report.unresolved == []

    def test_unknown_target_unresolved(self):
        report = _report(_registry(_spec(target_ref="lottery_numbers")))
        assert report.unresolved == ["s1.target_ref -> lottery_numbers"]

    def test_multiple_unresolved_all_reported(self):
        report = _report(
            _registry(
                _spec(
                    id="bad",
                    model_ref="nope",
                    feature_set_ref=["ghost"],
                    target_ref="nope_target",
                )
            )
        )
        assert set(report.unresolved) == {
            "bad.model_ref -> nope",
            "bad.feature_set_ref -> ghost",
            "bad.target_ref -> nope_target",
        }


# --------------------------------------------------------------------- #
# G1 gate — provenance gate (the deployment guardrail)
# --------------------------------------------------------------------- #

class TestProvenanceGate:
    def test_enabled_placeholder_ok(self):
        report = _report(_registry(_spec(provenance=PLACEHOLDER_PROVENANCE)))
        assert report.provenance_violations == []

    def test_enabled_without_provenance_violates(self):
        report = _report(_registry(_spec(provenance="")))
        assert report.provenance_violations == ["s1: enabled but provenance is empty"]
        assert report.passed is False

    def test_disabled_without_provenance_ok(self):
        # A disabled strategy needs no provenance — it is not deployed.
        report = _report(_registry(_spec(enabled=False, provenance="")))
        assert report.provenance_violations == []

    def test_enabled_with_resolving_ledger_ref_ok(self):
        report = _report(
            _registry(_spec(provenance="ledger-2026-06-13-0001"))
        )
        assert report.provenance_violations == []

    def test_enabled_with_unknown_ledger_ref_violates(self):
        report = _report(_registry(_spec(provenance="ledger-9999-99-99-9999")))
        assert report.provenance_violations == [
            "s1: provenance 'ledger-9999-99-99-9999' does not resolve to a "
            "gate_passed ledger entry"
        ]

    def test_enabled_with_garbage_provenance_violates(self):
        report = _report(_registry(_spec(provenance="trust me")))
        assert len(report.provenance_violations) == 1
        assert "neither 'placeholder' nor" in report.provenance_violations[0]


# --------------------------------------------------------------------- #
# Code <-> code drift direction (METHODOLOGY §6, the reverse axis)
# --------------------------------------------------------------------- #

class TestCodeContractDrift:
    def test_every_model_registry_entry_imports(self):
        """MODEL_REGISTRY must not drift from the actual model classes in src/."""
        offenders = []
        for ref in MODEL_REGISTRY:
            try:
                cls = resolve_model_class(ref)
                assert isinstance(cls, type)
            except (ImportError, AttributeError, KeyError) as exc:
                offenders.append(f"{ref}: {exc}")
        assert not offenders, f"unimportable model refs: {offenders}"

    def test_resolve_unknown_model_raises(self):
        with pytest.raises(KeyError, match=r"unknown model_ref"):
            resolve_model_class("not_a_model")

    def test_target_catalog_keys_are_resolvable(self):
        # A new B1 target must stay resolvable from the registry (no silent drift
        # where a catalog target cannot be referenced by a deployable strategy).
        kt = known_targets()
        missing = [t for t in TARGET_CATALOG if t not in kt]
        assert not missing, f"TARGET_CATALOG targets not in known_targets(): {missing}"


# --------------------------------------------------------------------- #
# View-model for the Project E console
# --------------------------------------------------------------------- #

class TestViewModel:
    def test_view_model_is_json_serializable(self):
        registry = load_registry()
        views = strategy_view_models(registry)
        # Round-trips through JSON without error (the console export contract).
        dumped = json.dumps(views)
        assert json.loads(dumped) == views

    def test_enabled_allocation_is_equal_weight(self):
        registry = _registry(
            _spec(id="a", enabled=True),
            _spec(id="b", enabled=True),
            _spec(id="c", enabled=False, provenance=""),
        )
        views = {v["id"]: v for v in strategy_view_models(registry)}
        assert views["a"]["allocation_pct"] == pytest.approx(50.0)
        assert views["b"]["allocation_pct"] == pytest.approx(50.0)
        assert views["c"]["allocation_pct"] == 0.0
        assert views["c"]["status"] == "idle"
        assert views["a"]["status"] == "enabled"

    def test_view_model_reports_provenance_summary(self):
        registry = load_registry()
        view = strategy_view_models(registry)[0]
        assert view["id"] == "arima_placeholder"
        assert "Placeholder" in view["provenance_summary"]

    def test_no_enabled_strategies_zero_allocation(self):
        registry = _registry(_spec(id="x", enabled=False, provenance=""))
        views = strategy_view_models(registry)
        assert views[0]["allocation_pct"] == 0.0

    def test_ledger_ref_provenance_summary_is_gate_verified(self):
        registry = _registry(_spec(provenance="ledger-2026-06-13-0001"))
        view = strategy_view_models(registry)[0]
        assert view["provenance_summary"] == "Gate-verified (ledger-2026-06-13-0001)"


# --------------------------------------------------------------------- #
# enabled_strategies helper (C6-M2's entry point)
# --------------------------------------------------------------------- #

class TestEnabledStrategies:
    def test_returns_only_enabled_in_order(self):
        registry = _registry(
            _spec(id="a", enabled=True),
            _spec(id="b", enabled=False, provenance=""),
            _spec(id="c", enabled=True),
        )
        result = enabled_strategies(registry)
        assert [s.id for s in result] == ["a", "c"]

    def test_real_registry_has_enabled_placeholder(self):
        registry = load_registry()
        result = enabled_strategies(registry)
        assert "arima_placeholder" in {s.id for s in result}
