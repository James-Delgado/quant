"""Export-JSON schemas — the data contract reused verbatim by E2.

The schemas are *generated from* the view-model dataclasses in
:mod:`quant.console.viewmodels`, so the documented contract and the serialised
output cannot drift (both derive from one source). :func:`validate` is a small,
dependency-free recursive validator used by the export step and the drift test
(METHODOLOGY §6 — the export contract is checked in CI).

``EXPORT_SCHEMAS`` maps each export filename to its root schema.
"""
from __future__ import annotations

import dataclasses
import types
import typing
from typing import Any, Union, get_args, get_origin

from quant.console import viewmodels as vm

_PRIMITIVES: dict[type, str] = {
    str: "string",
    bool: "boolean",
    int: "integer",
    float: "number",
}


def _is_optional(tp: Any) -> tuple[bool, Any]:
    """Return ``(nullable, inner)`` for ``X | None`` / ``Optional[X]``."""
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(tp) if a is not type(None)]
        nullable = len(args) != len(get_args(tp))
        if len(args) == 1:
            return nullable, args[0]
        # Multiple non-None members: treat as a permissive any type.
        return nullable, args
    return False, tp


def schema_for(tp: Any) -> dict:
    """Build a JSON-schema-like dict for a type / dataclass (recursive)."""
    nullable, inner = _is_optional(tp)
    node = _schema_for_inner(inner)
    if nullable:
        node = {**node, "nullable": True}
    return node


def _schema_for_inner(tp: Any) -> dict:
    if isinstance(tp, list):  # union with several non-None members → any type
        return {"type": "any"}
    if dataclasses.is_dataclass(tp):
        hints = typing.get_type_hints(tp)
        properties = {f.name: schema_for(hints[f.name]) for f in dataclasses.fields(tp)}
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
        }
    origin = get_origin(tp)
    if origin in (list, tuple):
        args = get_args(tp)
        item = schema_for(args[0]) if args else {"type": "any"}
        return {"type": "array", "items": item}
    if origin is dict:
        return {"type": "object", "properties": {}, "required": []}
    if tp in _PRIMITIVES:
        return {"type": _PRIMITIVES[tp]}
    return {"type": "any"}


def _array_of(tp: Any) -> dict:
    return {"type": "array", "items": schema_for(tp)}


# Each export file → its root schema.
EXPORT_SCHEMAS: dict[str, dict] = {
    "strategies.json": _array_of(vm.StrategyCard),
    "conditions.json": schema_for(vm.ConditionsView),
    "catalog.json": schema_for(vm.CatalogView),
    "ledger.json": schema_for(vm.LedgerView),
    "data_status.json": schema_for(vm.DataStatusView),
    "market.json": schema_for(vm.MarketSnapshot),
}

# Files produced by per-run fan-out share one schema each.
STRATEGY_DETAIL_SCHEMA: dict = schema_for(vm.StrategyDetail)
PROVENANCE_SCHEMA: dict = schema_for(vm.ProvenanceView)


# ── Validator ────────────────────────────────────────────────────────────────


def _type_ok(value: Any, json_type: str) -> bool:
    if json_type == "any":
        return True
    if json_type == "object":
        return isinstance(value, dict)
    if json_type == "array":
        return isinstance(value, list)
    if json_type == "string":
        return isinstance(value, str)
    if json_type == "boolean":
        return isinstance(value, bool)
    if json_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if json_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _validate(value: Any, schema: dict, path: str, errors: list[str]) -> None:
    if value is None:
        if not schema.get("nullable"):
            errors.append(f"{path}: null not allowed")
        return

    json_type = schema.get("type", "any")
    if not _type_ok(value, json_type):
        errors.append(f"{path}: expected {json_type}, got {type(value).__name__}")
        return

    if json_type == "object" and schema.get("properties"):
        properties = schema["properties"]
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: required key missing")
        for key, sub in value.items():
            if key in properties:
                _validate(sub, properties[key], f"{path}.{key}", errors)
    elif json_type == "array":
        item_schema = schema.get("items", {"type": "any"})
        for i, item in enumerate(value):
            _validate(item, item_schema, f"{path}[{i}]", errors)


def validate(data: Any, schema: dict, *, name: str = "<root>") -> list[str]:
    """Return a list of human-readable validation errors (empty == valid)."""
    errors: list[str] = []
    _validate(data, schema, name, errors)
    return errors
