"""Trial-count ledger (METHODOLOGY.md §12 — "Trial-count ledger as a code artifact").

`data/ledger.yaml` is the append-only registry of every pre-registered,
executed, verdict-reported comparison the project has run. The cumulative
trial count `N` (sum of `n_comparisons`) is what the Bailey-López de Prado
deflated-Sharpe deflation reads from — it must come from this file, not from
hand-counting after the fact.

Schema is pre-committed in `docs/METHODOLOGY.md` §"Reference schemas" (12
fields, see `LedgerEntry`); this module is the code half of that contract and
`tests/test_ledger.py` is the drift enforcement.

Two invariants this module enforces and `tests/test_ledger.py` re-checks:

  * **Append-only.** `append_ledger_entry` only ever appends bytes (open mode
    "a"); it never rewrites existing entries. The CI test additionally walks
    the file's git history and asserts each older revision is a content-prefix
    of every newer one, so a modification/reorder/deletion of a committed
    entry fails CI naming the offender.
  * **Monotonically dated, unique ids.** Entries are ordered by
    non-decreasing `started_at`; ids are globally unique. Both the loader and
    the writer enforce this and name the offending entries on violation.

Convention: `n_comparisons` may be **0** for infrastructure milestones that
registered no statistical comparison (e.g. building the regime harness or the
feature catalog). Such entries keep the milestone audit trail without
inflating the deflation `N` — `N` is the sum and they contribute nothing.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Repo root = two levels up from src/quant/ledger.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data" / "ledger.yaml"


class LedgerEntry(BaseModel):
    """One trial — one pre-registered, executed, verdict-reported comparison.

    Field set is pinned in `docs/METHODOLOGY.md` §"Reference schemas". Extra
    fields are rejected so a typo can't silently add an unaudited column.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    prd: str
    milestone: str
    agent: Literal["human", "R", "F", "M"]
    preregistration: str
    config_hash: str
    n_comparisons: int = Field(ge=0)
    started_at: datetime
    completed_at: datetime
    verdict: Literal["gate_passed", "gate_failed", "inconclusive"]
    artifacts: list[str] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def _check_timestamps(self) -> "LedgerEntry":
        # Naive (timezone-less) timestamps make cross-entry ordering ambiguous —
        # the schema example uses a UTC `Z` suffix, so require an offset.
        for field in ("started_at", "completed_at"):
            if getattr(self, field).tzinfo is None:
                raise ValueError(
                    f"{self.id}: {field} must be timezone-aware (ISO-8601 with offset, e.g. ...Z)"
                )
        if self.completed_at < self.started_at:
            raise ValueError(
                f"{self.id}: completed_at ({self.completed_at}) precedes started_at ({self.started_at})"
            )
        return self


def _parse_entries(raw: object) -> list[LedgerEntry]:
    """Validate a parsed-YAML payload into an ordered list of `LedgerEntry`.

    Raises ValueError naming the offenders when:
      * the top-level document is not a list,
      * any `id` is duplicated,
      * `started_at` is not non-decreasing in list order.
    Raises pydantic ValidationError on per-entry schema problems.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"ledger top-level must be a YAML list, got {type(raw).__name__}"
        )

    entries = [LedgerEntry(**e) for e in raw]

    # Duplicate-id check — list every duplicate (not just the first).
    seen: dict[str, int] = {}
    for entry in entries:
        seen[entry.id] = seen.get(entry.id, 0) + 1
    duplicates = sorted(eid for eid, count in seen.items() if count > 1)
    if duplicates:
        raise ValueError(f"ledger has duplicate ids: {duplicates}")

    # Monotonic dating — started_at must be non-decreasing in file order.
    out_of_order: list[str] = []
    for prev, cur in zip(entries, entries[1:]):
        if cur.started_at < prev.started_at:
            out_of_order.append(
                f"{cur.id} ({cur.started_at}) < {prev.id} ({prev.started_at})"
            )
    if out_of_order:
        raise ValueError(
            f"ledger entries are not monotonically dated by started_at: {out_of_order}"
        )

    return entries


def load_ledger(path: Path | str = DEFAULT_LEDGER_PATH) -> list[LedgerEntry]:
    """Parse `ledger.yaml`, validate, and return entries in file order.

    A missing or empty file is a valid empty ledger (returns `[]`) — the very
    first runner to append creates it.
    """
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r") as f:
        raw = yaml.safe_load(f)
    return _parse_entries(raw)


def _dump_entry_fragment(entry: LedgerEntry) -> str:
    """Serialize one entry as a one-item YAML list fragment (`- id: ...`)."""
    payload = entry.model_dump(mode="json")
    return yaml.safe_dump(
        [payload], sort_keys=False, default_flow_style=False, allow_unicode=True
    )


def append_ledger_entry(
    record: LedgerEntry | dict,
    path: Path | str = DEFAULT_LEDGER_PATH,
) -> LedgerEntry:
    """Append one validated entry to the ledger, append-only.

    The new entry is validated against the *existing* ledger (unique id,
    `started_at` not earlier than the last entry) and then its YAML text is
    appended to the file — existing bytes are never rewritten, which is the
    file-level guarantee behind the append-only invariant.

    Raises ValueError if the id already exists or the new `started_at` would
    break monotonic dating. Raises pydantic ValidationError if `record` is a
    dict that fails the schema.
    """
    entry = record if isinstance(record, LedgerEntry) else LedgerEntry(**record)
    path = Path(path)

    existing = load_ledger(path)
    existing_ids = {e.id for e in existing}
    if entry.id in existing_ids:
        raise ValueError(f"ledger already contains id {entry.id!r}; ids must be unique")
    if existing and entry.started_at < existing[-1].started_at:
        raise ValueError(
            f"new entry {entry.id} started_at ({entry.started_at}) precedes the last "
            f"ledger entry {existing[-1].id} ({existing[-1].started_at}); the ledger "
            "is append-only and monotonically dated"
        )

    fragment = _dump_entry_fragment(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_newline = path.exists() and path.stat().st_size > 0
    if needs_newline:
        existing_text = path.read_text(encoding="utf-8")
        needs_newline = not existing_text.endswith("\n")
    with path.open("a", encoding="utf-8") as f:
        if needs_newline:
            f.write("\n")
        f.write(fragment)
    return entry


def cumulative_trial_count(
    entries: Sequence[LedgerEntry] | None = None,
    path: Path | str = DEFAULT_LEDGER_PATH,
) -> int:
    """Sum `n_comparisons` across the ledger — the deflated-Sharpe `N`.

    Pass `entries` to count an already-loaded ledger; otherwise the default
    ledger is loaded from `path`.
    """
    if entries is None:
        entries = load_ledger(path)
    return sum(e.n_comparisons for e in entries)
