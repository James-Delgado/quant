"""Machine-readable feature catalog (Phase 4A Milestone 4).

`catalog.yaml` is the registry: one entry per column produced by
`build_features()` + `add_cross_sectional_features()`. The drift-enforcement
test in `tests/test_catalog.py` keeps the YAML and the code in lock-step —
adding a column without registering it, or retiring one without removing it
from the YAML, fails CI by naming the offender.

Schema is pre-committed (12 fields, see `FeatureRecord`); intentionally
*not* present: ownership, scheduling, prompts, or run history — those
belong to the Phase 5 continuous-agent runtime, not to a Phase 4A artifact.

Limitation: the drift test compares *set* membership, not column order.
`mom_21d`'s positional contract (index 5 — read by `MomentumBaseline`) is
enforced by a separate regression test in `tests/test_features.py`.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CATALOG_PATH = Path(__file__).parent / "catalog.yaml"


class FeatureRecord(BaseModel):
    """One entry per feature column in the model matrix."""

    model_config = ConfigDict(extra="forbid")

    name: str
    family: Literal[
        "price",
        "volume",
        "macro",
        "macro_derived",
        "sentiment",
        "cross_sectional",
        "regime",
    ]
    source: Literal["alpaca_ohlcv", "fred", "edgar_finbert", "derived"]
    formula: str
    lookback_bars: int
    publication_lag_days: int
    point_in_time_rule: str
    added_phase: str
    glossary_ref: str
    ablation_status: Literal[
        "untested", "tested_no_edge", "tested_edge", "retired"
    ]
    regime_notes: str | None = None
    depends_on: list[str] = Field(default_factory=list)


def load_catalog(
    path: Path | str = DEFAULT_CATALOG_PATH,
) -> dict[str, FeatureRecord]:
    """Parse `catalog.yaml`, validate each entry, return `{name: record}`.

    Raises ValueError with the offending names listed when:
      - Top-level is not `{"features": [...]}`
      - Duplicate `name` entries exist
      - Any `depends_on` reference names a feature not in the catalog
    Raises pydantic ValidationError on per-entry schema problems (unknown
    field, missing required field, invalid enum value).
    """
    path = Path(path)
    with path.open("r") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict) or set(raw.keys()) != {"features"}:
        got = list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__
        raise ValueError(
            f"catalog top-level must be {{'features': [...]}}, got top-level keys: {got}"
        )

    entries = raw["features"]
    if not isinstance(entries, list):
        raise ValueError(
            f"catalog 'features' must be a list, got {type(entries).__name__}"
        )

    records: list[FeatureRecord] = [FeatureRecord(**e) for e in entries]

    # Duplicate-name check — list every duplicate name (not just the first).
    seen: dict[str, int] = {}
    for r in records:
        seen[r.name] = seen.get(r.name, 0) + 1
    duplicates = sorted(name for name, count in seen.items() if count > 1)
    if duplicates:
        raise ValueError(f"catalog has duplicate feature names: {duplicates}")

    catalog: dict[str, FeatureRecord] = {r.name: r for r in records}

    # Referential integrity: every depends_on target must itself be registered.
    dangling: list[str] = []
    for r in records:
        for target in r.depends_on:
            if target not in catalog:
                dangling.append(f"{r.name} -> {target}")
    if dangling:
        raise ValueError(
            f"catalog depends_on references unregistered features: {dangling}"
        )

    return catalog


def validate_catalog_coverage(
    produced_columns: Iterable[str],
    catalog: dict[str, FeatureRecord] | None = None,
) -> None:
    """Assert that the produced column set matches the catalog name set.

    Raises ValueError naming both sides of the drift:
      - `unregistered`: produced by code but missing from the catalog
      - `phantom`: registered in the catalog but never produced by code

    `catalog=None` loads the default catalog from `DEFAULT_CATALOG_PATH`.
    """
    if catalog is None:
        catalog = load_catalog()
    produced = set(produced_columns)
    registered = set(catalog.keys())
    unregistered = sorted(produced - registered)
    phantom = sorted(registered - produced)
    if unregistered or phantom:
        raise ValueError(
            "feature catalog drift detected: "
            f"unregistered={unregistered}, phantom={phantom}"
        )
