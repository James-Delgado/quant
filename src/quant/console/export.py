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
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

from quant.console import readers, schemas
from quant.console import viewmodels as vm
from quant.console.sources import ConsoleSources, discover_strategies

# Round floats so re-export is byte-stable regardless of trailing ULPs.
FLOAT_PRECISION = 6

DEFAULT_EXPORT_DIR = Path(__file__).resolve().parent / "export"

# The freshness-stamp side artifact (E1-M1-EXPORT-FRESHNESS-STAMP). Written by
# write_export OUTSIDE the deterministic payload set, so re-running over unchanged
# artifacts still produces a byte-identical PAYLOAD tree; only this manifest moves
# (it carries the export-run time). The leading underscore keeps it visually
# distinct from the data payloads in the export tree. Pinned per METHODOLOGY §1.
MANIFEST_FILENAME = "_manifest.json"

# Friendly source labels → no internal filesystem paths reach the UI
# (DECISIONS #5/#7). Each maps to an artifact whose mtime the manifest stamps.
LEDGER_SOURCE_LABEL = "Trial Registry"
CATALOG_SOURCE_LABEL = "Feature Catalog"
REGISTRY_SOURCE_LABEL = "Strategy Registry"
CHECKPOINTS_SOURCE_LABEL = "Strategy checkpoints"


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


def _iso_utc(value: dt.datetime) -> str:
    """Format a datetime as ``YYYY-MM-DDTHH:MM:SSZ`` (UTC, second precision)."""
    utc = value.astimezone(dt.timezone.utc) if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _artifact_mtime(path: Path | None) -> str | None:
    """ISO-8601 UTC mtime of ``path``, or ``None`` when it is absent/unreadable.

    Honest degrade (METHODOLOGY §9): a missing artifact stamps ``None`` rather
    than a fabricated time, so the UI can render "unknown" instead of a guess.
    """
    if path is None:
        return None
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return None
    return _iso_utc(dt.datetime.fromtimestamp(mtime, tz=dt.timezone.utc))


def _checkpoints_mtime(sources: ConsoleSources) -> str | None:
    """Newest mtime across the discovered strategy checkpoints, or ``None``.

    Uses the *latest* of every checkpoint's ``metadata.json`` /
    ``oos_returns.parquet`` so the stamp reflects the freshest run feeding the
    export. No discoverable checkpoint → ``None`` (honest "unknown").
    """
    newest: float | None = None
    for ck in discover_strategies(sources):
        for name in ("metadata.json", "oos_returns.parquet"):
            try:
                mtime = (ck.path / name).stat().st_mtime
            except OSError:
                continue
            newest = mtime if newest is None else max(newest, mtime)
    if newest is None:
        return None
    return _iso_utc(dt.datetime.fromtimestamp(newest, tz=dt.timezone.utc))


def _registry_path(sources: ConsoleSources) -> Path | None:
    """The registry artifact the Portfolio reader uses (None → committed default).

    Mirrors ``readers.load_portfolio``'s fallback so the manifest stamps the same
    file the portfolio view actually reads. The default is imported lazily (it can
    require settings) and any failure degrades the stamp to ``None``.
    """
    if sources.registry_path is not None:
        return sources.registry_path
    try:
        from quant.execution.strategy_registry import DEFAULT_REGISTRY_PATH

        return Path(DEFAULT_REGISTRY_PATH)
    except Exception:
        return None


def build_manifest(sources: ConsoleSources | None = None) -> dict[str, Any]:
    """Build the freshness manifest: export-run time + per-source artifact mtimes.

    ``generated_at`` comes from the injectable ``sources.now()`` clock (so tests
    are deterministic); each source is a friendly label (never a path,
    DECISIONS #5/#7) carrying the artifact's mtime or ``None`` when absent.
    """
    sources = sources or ConsoleSources.default()
    manifest = vm.ExportManifest(
        generated_at=_iso_utc(sources.now()),
        sources=[
            vm.ManifestSource(LEDGER_SOURCE_LABEL, _artifact_mtime(sources.ledger_path)),
            vm.ManifestSource(CATALOG_SOURCE_LABEL, _artifact_mtime(sources.catalog_path)),
            vm.ManifestSource(REGISTRY_SOURCE_LABEL, _artifact_mtime(_registry_path(sources))),
            vm.ManifestSource(CHECKPOINTS_SOURCE_LABEL, _checkpoints_mtime(sources)),
        ],
    )
    return _sanitize(manifest)


def write_manifest(
    out_dir: Path | str | None = None,
    sources: ConsoleSources | None = None,
) -> Path:
    """Build, validate, and write ``_manifest.json``; return its path.

    Raises ``ValueError`` if the manifest fails schema validation (fail-fast,
    same contract as the payloads).
    """
    out_dir = Path(out_dir) if out_dir is not None else DEFAULT_EXPORT_DIR
    manifest = build_manifest(sources)
    errors = schemas.validate(manifest, schemas.MANIFEST_SCHEMA, name=MANIFEST_FILENAME)
    if errors:
        raise ValueError(f"manifest failed schema validation: {errors}")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / MANIFEST_FILENAME
    text = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False)
    target.write_text(text + "\n", encoding="utf-8")
    return target


def write_export(
    out_dir: Path | str | None = None,
    sources: ConsoleSources | None = None,
) -> list[Path]:
    """Build, validate, and write the export tree. Returns written paths.

    Raises ``ValueError`` if any payload fails schema validation (fail-fast —
    a malformed export must never reach the frontend). The freshness manifest
    (``_manifest.json``) is written last, OUTSIDE the deterministic payload set:
    re-running over unchanged artifacts leaves every payload byte-identical and
    only the manifest's ``generated_at`` moves (E1-M1-EXPORT-FRESHNESS-STAMP).
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
    written.append(write_manifest(out_dir, sources))
    return written
