"""Idempotent static-JSON export of the console view-models (PRD §4.2).

``build_export`` calls every reader and returns ``{filename: jsonable}``;
``write_export`` validates each payload against :mod:`quant.console.schemas` and
writes it deterministically (sorted keys, rounded floats, no embedded
timestamp) so re-running over unchanged artifacts produces byte-identical files.
The React app (E1-M2+) fetches these static files; when E2 adds FastAPI the same
readers back live endpoints with no logic change.
"""
from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path
from typing import Any

from quant.console import readers, schemas
from quant.console.sources import ConsoleSources

# Round floats so re-export is byte-stable regardless of trailing ULPs.
FLOAT_PRECISION = 6

DEFAULT_EXPORT_DIR = Path(__file__).resolve().parent / "export"


def _sanitize(obj: Any) -> Any:
    """Recursively make a value JSON-safe and deterministic.

    Converts dataclasses to dicts, rounds floats, maps NaN/Inf to ``None``
    (JSON has no NaN), and normalises ``-0.0`` to ``0.0``.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        obj = dataclasses.asdict(obj)
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        rounded = round(obj, FLOAT_PRECISION)
        return 0.0 if rounded == 0 else rounded
    return obj


def build_export(sources: ConsoleSources | None = None) -> dict[str, Any]:
    """Run every reader and return ``{export_path: jsonable_payload}``."""
    sources = sources or ConsoleSources.default()

    strategies = readers.load_strategies(sources)
    export: dict[str, Any] = {
        "strategies.json": _sanitize(strategies),
        "portfolio.json": _sanitize(readers.load_portfolio(sources)),
        "conditions.json": _sanitize(readers.load_conditions(sources)),
        "catalog.json": _sanitize(readers.load_catalog(sources)),
        "ledger.json": _sanitize(readers.load_ledger(sources)),
        "data_status.json": _sanitize(readers.data_status(sources)),
        "market.json": _sanitize(readers.market_snapshot(sources)),
    }

    # Per-strategy fan-out: detail + provenance share the strategy id namespace.
    for card in strategies:
        detail = readers.load_strategy(card.id, sources)
        if detail is not None:
            export[f"strategy/{card.id}.json"] = _sanitize(detail)
        prov = readers.load_provenance(card.id, sources)
        if prov is not None:
            export[f"provenance/{card.id}.json"] = _sanitize(prov)

    return export


def _schema_for_path(path: str) -> dict | None:
    if path in schemas.EXPORT_SCHEMAS:
        return schemas.EXPORT_SCHEMAS[path]
    if path.startswith("strategy/"):
        return schemas.STRATEGY_DETAIL_SCHEMA
    if path.startswith("provenance/"):
        return schemas.PROVENANCE_SCHEMA
    return None


def validate_export(export: dict[str, Any]) -> dict[str, list[str]]:
    """Validate each payload against its schema; return ``{path: errors}``."""
    problems: dict[str, list[str]] = {}
    for path, data in export.items():
        schema = _schema_for_path(path)
        if schema is None:
            problems[path] = ["no schema registered for this export path"]
            continue
        errors = schemas.validate(data, schema, name=path)
        if errors:
            problems[path] = errors
    return problems


def write_export(
    out_dir: Path | str | None = None,
    sources: ConsoleSources | None = None,
) -> list[Path]:
    """Build, validate, and write the export tree. Returns written paths.

    Raises ``ValueError`` if any payload fails schema validation (fail-fast —
    a malformed export must never reach the frontend).
    """
    out_dir = Path(out_dir) if out_dir is not None else DEFAULT_EXPORT_DIR
    export = build_export(sources)

    problems = validate_export(export)
    if problems:
        lines = [f"  {path}: {errs}" for path, errs in sorted(problems.items())]
        raise ValueError("export failed schema validation:\n" + "\n".join(lines))

    written: list[Path] = []
    for path in sorted(export):
        target = out_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(export[path], indent=2, sort_keys=True, ensure_ascii=False)
        target.write_text(text + "\n", encoding="utf-8")
        written.append(target)
    return written
