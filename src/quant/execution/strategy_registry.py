"""Strategy registry (Project C6 — Milestone 1).

The **deployment-side** analog of ``features/catalog.{py,yaml}``: a YAML
registry of *deployable strategies* + a typed loader + a bidirectional drift
test (``tests/test_strategy_registry.py``). A strategy is not just a model — it
is the full pipeline spec the daily executor needs to turn a forecast into a
paper order: ``model + feature_set + target + universe + decision_rule +
sizing_policy + confidence_gate + risk_limits + enabled + provenance``.

Why this exists (PRD ``.claude/prds/c6-strategy-registry.prd.md`` §Problem)
--------------------------------------------------------------------------
C2 wired exactly one *hardcoded* strategy into ``lean_bridge.daily_signal``.
This registry makes "deploy a model" a registry entry, not a source edit, and
is the **contract C3 (sizing), C4 (confidence), the C6-M2 executor, and the
Project E console all consume** — so it lands *before* any of them
(contract-before-consumer, METHODOLOGY §4; the central rationale for C6-M1's
rank ahead of C3/C4).

The registry *references* the existing catalogs (the model classes in
``quant.models``, the feature catalog in ``features/catalog.yaml``, and the
target catalog ``TARGET_CATALOG`` in ``features/targets.py``) — it does **not**
duplicate them (DRY; §4). The G1 gate (:func:`registry_drift_report`) resolves
every reference and enforces the provenance gate.

Placeholder fields until C3/C4 (PRD §Scope "Out of scope")
----------------------------------------------------------
``sizing_policy``, ``confidence_gate``, and ``risk_limits`` are typed sub-models
whose enums are pinned to a **single placeholder value** for C6:
``fully_invested_equal_weight`` sizing, ``always_pass`` (inert) confidence,
permissive risk limits. C3/C4 *extend the Literal* with their real methods — a
deliberate contract change those milestones own. No strategy is gated out on
confidence in C6.

The provenance gate (METHODOLOGY guardrail → carried into deployment)
--------------------------------------------------------------------
A strategy may not be ``enabled: true`` without ``provenance`` pointing at a
passing gate verdict. ``provenance`` is either the literal ``placeholder`` (the
one sanctioned exception — infrastructure, not an edge claim) or a
``ledger-<id>`` reference that must resolve to a ``gate_passed`` entry in
``data/ledger.yaml``. This is Phase-4A's "no edge without a pre-committed gate"
applied to deployment.

Schema / drift contract (METHODOLOGY §6)
----------------------------------------
``load_registry`` raises on *structural* problems (bad top-level key, schema
violation, duplicate id) — mirroring ``load_catalog``. :func:`registry_drift_report`
owns the two G1 axes (unresolved external references + provenance violations),
returning lists that must both be empty to pass. The drift test exercises both
directions: registry→code (refs resolve) and code→code (``MODEL_REGISTRY`` paths
import, ``TARGET_CATALOG`` keys stay resolvable).

Scope: C6-M1 ships the contract only. The multi-strategy allocator + daily cron
executor are C6-M2. This module touches **no** walk-forward split logic
(``backtest/CLAUDE.md``).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from quant.features.catalog import FeatureRecord, load_catalog
from quant.features.targets import TARGET_CATALOG

DEFAULT_REGISTRY_PATH = Path(__file__).parent / "strategy_registry.yaml"

# ─── Reference universes the loader resolves against ────────────────────────────

#: Known deployable models, ref → "module:Class". Resolution checks membership;
#: :func:`resolve_model_class` imports lazily so loading the registry does not
#: pull XGBoost (via ``models.gbm``) into memory. Adding a model means adding a
#: line here; the code↔code drift test asserts every path still imports.
MODEL_REGISTRY: dict[str, str] = {
    "arima_baseline": "quant.models.arima_baseline:ARIMABaseline",
    "gbm": "quant.models.gbm:GBMModel",
    "buyandhold_baseline": "quant.models.buyandhold_baseline:BuyAndHoldBaseline",
}

#: The literal ``provenance`` value that exempts a strategy from needing a
#: ledger-backed gate verdict — the one sanctioned exception (infrastructure,
#: ``n_comparisons = 0``; PRD §Provenance gate).
PLACEHOLDER_PROVENANCE: str = "placeholder"

#: Built-in prediction targets that predate the B1 ``TARGET_CATALOG`` reframing
#: work. ``next_bar_return`` is the Phase-1/2 forward-return label produced by
#: ``features.labels.generate_labels(prices, horizon=1)`` — the target the ARIMA
#: placeholder forecasts (``lean_bridge.daily_signal``). The B1 catalog targets
#: (drawdown/vol/directional) *supplement* it, they do not replace it, so a
#: deployable strategy may reference either family.
BUILTIN_TARGETS: frozenset[str] = frozenset({"next_bar_return"})


def known_targets() -> set[str]:
    """The resolvable target-ref set: built-ins ∪ the B1 target catalog keys."""
    return set(BUILTIN_TARGETS) | set(TARGET_CATALOG)


def _validate_iso8601_utc(field: str, value: str) -> None:
    """Assert *value* parses as a timezone-aware ISO-8601 instant.

    Registry timestamps are documented as ISO-8601 UTC strings (mirroring the
    ledger / position-state convention). Stored as free strings, an unparseable
    or naive value would only surface downstream at execution time; validating at
    load time fails fast — mirrors the ``A-PRIORITIES-TEST-TS`` check for
    ``PRIORITIES.yaml`` and ``ledger._check_timestamps``'s tz-aware requirement.
    A trailing ``Z`` is normalised to ``+00:00`` (``fromisoformat`` accepts ``Z``
    natively only on Python ≥ 3.11, so the replace keeps it portable).
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{field} must be an ISO-8601 timestamp, got {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"{field} must be timezone-aware (ISO-8601 with offset, e.g. ...Z), "
            f"got {value!r}"
        )


def resolve_model_class(ref: str) -> type:
    """Import and return the model class registered under *ref*.

    Lazy import (the registry stays light at load time). Raises ``KeyError`` for
    an unknown ref — callers in C6-M2 use this to instantiate a strategy's model.
    """
    if ref not in MODEL_REGISTRY:
        raise KeyError(
            f"unknown model_ref {ref!r}; known: {sorted(MODEL_REGISTRY)}"
        )
    module_path, _, class_name = MODEL_REGISTRY[ref].partition(":")
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, class_name)


# ─── Schema — the full pipeline spec per strategy ───────────────────────────────


class SizingPolicy(BaseModel):
    """How a strategy turns target positions into sized holdings.

    C6 pins the placeholder ``fully_invested_equal_weight`` (equal notional
    across the strategy's universe). C3 extends ``method`` with real policies
    (e.g. ``vol_target``) and populates the parameters; the field exists now so
    C3 is built *into* this contract rather than retrofitted (§4).
    """

    model_config = ConfigDict(extra="forbid")

    method: Literal["fully_invested_equal_weight"] = "fully_invested_equal_weight"


class ConfidenceGate(BaseModel):
    """Whether/how a strategy is gated on calibrated confidence.

    C6 pins ``always_pass`` — the gate is **inert** until C4 supplies calibrated
    confidence (PRD §Out of scope: no strategy is gated out on confidence in C6).
    """

    model_config = ConfigDict(extra="forbid")

    method: Literal["always_pass"] = "always_pass"


class RiskLimits(BaseModel):
    """Per-strategy risk caps. Permissive placeholders until C3.

    ``max_position`` is the per-symbol target-position magnitude cap (1.0 = the
    full long/short unit the C2 parity rule emits). ``max_drawdown_stop`` is
    ``None`` (no stop) until C3 populates it.
    """

    model_config = ConfigDict(extra="forbid")

    max_position: float = 1.0
    max_drawdown_stop: float | None = None

    @field_validator("max_position")
    @classmethod
    def _positive_cap(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"max_position must be > 0, got {v}")
        return v


class StrategySpec(BaseModel):
    """One deployable strategy — the full pipeline spec.

    References (resolved by :func:`registry_drift_report`):

    * ``model_ref`` → a key of :data:`MODEL_REGISTRY`.
    * ``feature_set_ref`` → a subset of ``features/catalog.yaml`` names (empty
      for label-only models like the ARIMA placeholder).
    * ``target_ref`` → :func:`known_targets` (a built-in or a B1 catalog target).
    * ``provenance`` → ``placeholder`` or a ``ledger-<id>`` in ``data/ledger.yaml``.

    Timestamps are ISO-8601 strings (mirroring the ledger / position-state
    format); ``enabled_at`` is ``None`` for a never-enabled strategy.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    description: str
    model_ref: str
    feature_set_ref: list[str] = Field(default_factory=list)
    target_ref: str
    universe: list[str]
    decision_rule: Literal["sign"]
    sizing_policy: SizingPolicy = Field(default_factory=SizingPolicy)
    confidence_gate: ConfidenceGate = Field(default_factory=ConfidenceGate)
    risk_limits: RiskLimits = Field(default_factory=RiskLimits)
    cadence: Literal["daily"]
    broker: Literal["alpaca_paper"]
    enabled: bool
    provenance: str
    created_at: str
    enabled_at: str | None = None

    @field_validator("id", "display_name", "description", "model_ref", "target_ref")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("field must be a non-empty string")
        return v

    @field_validator("universe")
    @classmethod
    def _universe_non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("universe must list at least one symbol")
        return v

    @field_validator("created_at")
    @classmethod
    def _created_at_iso8601(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("created_at must be a non-empty ISO-8601 string")
        _validate_iso8601_utc("created_at", v)
        return v

    @field_validator("enabled_at")
    @classmethod
    def _enabled_at_iso8601(cls, v: str | None) -> str | None:
        # None = never enabled (the documented default); only validate a value.
        if v is None:
            return v
        _validate_iso8601_utc("enabled_at", v)
        return v


def load_registry(
    path: Path | str = DEFAULT_REGISTRY_PATH,
) -> dict[str, StrategySpec]:
    """Parse ``strategy_registry.yaml``, validate each entry, return ``{id: spec}``.

    Raises ``ValueError`` (mirroring ``load_catalog``) when:
      - Top-level is not ``{"strategies": [...]}``
      - ``strategies`` is not a list
      - Duplicate ``id`` entries exist (every duplicate named)
    Raises pydantic ``ValidationError`` on per-entry schema problems (unknown
    field, missing required field, invalid enum value, empty universe).

    External-reference resolution and the provenance gate are **not** checked
    here — they are the two G1 drift axes owned by :func:`registry_drift_report`
    (the ``validate_catalog_coverage`` analog).
    """
    path = Path(path)
    with path.open("r") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict) or set(raw.keys()) != {"strategies"}:
        got = list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__
        raise ValueError(
            f"registry top-level must be {{'strategies': [...]}}, got top-level keys: {got}"
        )

    entries = raw["strategies"]
    if not isinstance(entries, list):
        raise ValueError(
            f"registry 'strategies' must be a list, got {type(entries).__name__}"
        )

    specs: list[StrategySpec] = [StrategySpec(**e) for e in entries]

    seen: dict[str, int] = {}
    for s in specs:
        seen[s.id] = seen.get(s.id, 0) + 1
    duplicates = sorted(sid for sid, count in seen.items() if count > 1)
    if duplicates:
        raise ValueError(f"registry has duplicate strategy ids: {duplicates}")

    return {s.id: s for s in specs}


# ─── G1 gate: reference resolution + provenance ─────────────────────────────────


def _load_ledger_passed_ids() -> set[str]:
    """Ids of ``gate_passed`` ledger entries — the valid provenance targets."""
    from quant.ledger import load_ledger

    return {e.id for e in load_ledger() if e.verdict == "gate_passed"}


def lake_symbols() -> set[str]:
    """Distinct tradeable symbols present in the **local** processed price lake.

    This is a local DuckDB read over the Parquet lake (``quant.storage.catalog``
    over ``realtime.PRICE_DATASET`` — the adjusted-EOD dataset the backtest and
    live paths both consume) — **never a network call**. It is the resolution
    target for universe-symbol drift: a strategy whose ``universe`` lists a symbol
    absent here would pass the C6-M1 G1 reference gate yet silently emit no signal
    at execution time (C6-M2).

    Returns an **empty set** when the price dataset has never been written
    (``IOException`` / ``CatalogException``), so the gate stays tolerant in a
    lake-less checkout — :func:`registry_drift_report` then skips universe
    resolution rather than flagging every symbol (an environmental property, not
    a contract violation).

    Imported lazily (mirroring :func:`_load_ledger_passed_ids`) so loading the
    registry does not pull DuckDB / credential-loaded ``Settings`` into memory;
    callers that cannot load ``Settings`` (no ``.env``) inject
    ``tradeable_symbols`` into the gate instead.
    """
    import duckdb

    from quant.storage.catalog import query, table
    from quant.storage.realtime import PRICE_DATASET

    try:
        df = query(f"SELECT DISTINCT symbol FROM {table(PRICE_DATASET)}")
    except (duckdb.IOException, duckdb.CatalogException):
        return set()  # dataset never written — tolerant empty result
    return {str(s) for s in df["symbol"].tolist()}


@dataclass(frozen=True)
class RegistryDriftReport:
    """Verdict of the G1 registry-contract gate (PRD §Pre-committed gate G1).

    ``unresolved`` names every strategy reference (``model_ref`` /
    ``feature_set_ref`` member / ``target_ref`` / ``universe`` member) with no
    matching catalog/lake entry. ``provenance_violations`` names every ``enabled``
    strategy lacking a valid provenance. A PASS requires **both lists empty**
    (0 unresolved, 0 violations — the pinned G1 threshold).
    """

    unresolved: list[str]
    provenance_violations: list[str]

    @property
    def passed(self) -> bool:
        return not self.unresolved and not self.provenance_violations


def _provenance_violation(
    spec: StrategySpec, passed_ledger_ids: set[str]
) -> str | None:
    """Return a violation message for *spec*, or ``None`` if its provenance is OK.

    A disabled strategy needs no provenance. An enabled one must be either the
    sanctioned ``placeholder`` or a ``ledger-<id>`` resolving to a ``gate_passed``
    entry (PRD §Provenance gate).
    """
    if not spec.enabled:
        return None
    prov = spec.provenance.strip()
    if not prov:
        return f"{spec.id}: enabled but provenance is empty"
    if prov == PLACEHOLDER_PROVENANCE:
        return None
    if prov.startswith("ledger-"):
        if prov not in passed_ledger_ids:
            return (
                f"{spec.id}: provenance {prov!r} does not resolve to a "
                "gate_passed ledger entry"
            )
        return None
    return (
        f"{spec.id}: provenance {prov!r} is neither 'placeholder' nor a "
        "'ledger-<id>' reference"
    )


def registry_drift_report(
    registry: dict[str, StrategySpec],
    *,
    catalog: dict[str, FeatureRecord] | None = None,
    valid_targets: Iterable[str] | None = None,
    valid_models: Iterable[str] | None = None,
    passed_ledger_ids: set[str] | None = None,
    tradeable_symbols: Iterable[str] | None = None,
) -> RegistryDriftReport:
    """G1 gate: every reference resolves and the provenance gate holds.

    For each strategy, resolves ``model_ref`` against *valid_models*
    (default :data:`MODEL_REGISTRY`), every ``feature_set_ref`` member against
    *catalog* (default the feature catalog), ``target_ref`` against
    *valid_targets* (default :func:`known_targets`), and every ``universe`` symbol
    against *tradeable_symbols* (default :func:`lake_symbols` — the symbols present
    in the local price lake); then checks the provenance gate against
    *passed_ledger_ids* (default the ``gate_passed`` ids in ``data/ledger.yaml``).
    All five inputs are injectable so the drift test can drive crafted
    positive/negative cases without touching the real catalogs, lake, or ledger.

    **Universe-resolution tolerance:** when the resolved tradeable set is empty
    (lake never ingested / unavailable), universe resolution is **skipped** — the
    gate does not flag every symbol on an environmental property. When the set is
    non-empty, each universe symbol absent from it is reported as unresolved
    (``"<id>.universe -> <symbol>"``), closing the C6-M1 gap where a strategy
    whose universe lists a never-ingested symbol passed G1 yet emitted no signal.

    Returns a :class:`RegistryDriftReport`; ``.passed`` is ``True`` iff both
    lists are empty.
    """
    if catalog is None:
        catalog = load_catalog()
    valid_model_set = (
        set(valid_models) if valid_models is not None else set(MODEL_REGISTRY)
    )
    valid_target_set = (
        set(valid_targets) if valid_targets is not None else known_targets()
    )
    if passed_ledger_ids is None:
        passed_ledger_ids = _load_ledger_passed_ids()
    tradeable_set = (
        set(tradeable_symbols) if tradeable_symbols is not None else lake_symbols()
    )

    feature_names = set(catalog.keys())
    unresolved: list[str] = []
    provenance_violations: list[str] = []

    for spec in registry.values():
        if spec.model_ref not in valid_model_set:
            unresolved.append(f"{spec.id}.model_ref -> {spec.model_ref}")
        for feat in spec.feature_set_ref:
            if feat not in feature_names:
                unresolved.append(f"{spec.id}.feature_set_ref -> {feat}")
        if spec.target_ref not in valid_target_set:
            unresolved.append(f"{spec.id}.target_ref -> {spec.target_ref}")
        # Tolerant universe resolution: skip when the lake reports nothing.
        if tradeable_set:
            for sym in spec.universe:
                if sym not in tradeable_set:
                    unresolved.append(f"{spec.id}.universe -> {sym}")

        violation = _provenance_violation(spec, passed_ledger_ids)
        if violation is not None:
            provenance_violations.append(violation)

    return RegistryDriftReport(
        unresolved=unresolved, provenance_violations=provenance_violations
    )


# ─── Serializable view-model for the Project E console ──────────────────────────


def _provenance_summary(provenance: str) -> str:
    """A short human-readable provenance label for the console panel."""
    prov = provenance.strip()
    if prov == PLACEHOLDER_PROVENANCE:
        return "Placeholder (infrastructure — no edge claim)"
    if prov.startswith("ledger-"):
        return f"Gate-verified ({prov})"
    return prov or "(none)"


def strategy_view_models(
    registry: dict[str, StrategySpec],
) -> list[dict]:
    """JSON-serializable per-strategy view for the console (``E-STRATEGIES-PANEL``).

    Exposes display fields, ``status`` (``enabled``/``idle``), the equal-weight
    ``allocation_pct`` (1/N across enabled strategies; 0 for idle ones — the PRD-
    pinned capital budget), and a provenance summary. The exact panel rendering
    is a Project E task; C6-M1 owns this view-model contract.
    """
    enabled_ids = [s.id for s in registry.values() if s.enabled]
    n_enabled = len(enabled_ids)
    alloc_pct = round(100.0 / n_enabled, 4) if n_enabled else 0.0

    views: list[dict] = []
    for spec in registry.values():
        views.append(
            {
                "id": spec.id,
                "display_name": spec.display_name,
                "description": spec.description,
                "model_ref": spec.model_ref,
                "target_ref": spec.target_ref,
                "universe": list(spec.universe),
                "cadence": spec.cadence,
                "broker": spec.broker,
                "enabled": spec.enabled,
                "status": "enabled" if spec.enabled else "idle",
                "allocation_pct": alloc_pct if spec.enabled else 0.0,
                "provenance": spec.provenance,
                "provenance_summary": _provenance_summary(spec.provenance),
            }
        )
    return views


def enabled_strategies(
    registry: dict[str, StrategySpec],
) -> list[StrategySpec]:
    """The enabled subset, registry order — the set the daily executor runs (C6-M2)."""
    return [s for s in registry.values() if s.enabled]


__all__: Sequence[str] = [
    "DEFAULT_REGISTRY_PATH",
    "MODEL_REGISTRY",
    "BUILTIN_TARGETS",
    "PLACEHOLDER_PROVENANCE",
    "SizingPolicy",
    "ConfidenceGate",
    "RiskLimits",
    "StrategySpec",
    "RegistryDriftReport",
    "known_targets",
    "lake_symbols",
    "resolve_model_class",
    "load_registry",
    "registry_drift_report",
    "strategy_view_models",
    "enabled_strategies",
]
