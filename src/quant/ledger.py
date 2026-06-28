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

import json
import math
import statistics
from collections.abc import Mapping, Sequence
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
    # Headline annualised Sharpe of this trial's strategy arm (project convention,
    # `compute_metrics`). OPTIONAL and back-compat: older entries and infrastructure
    # milestones omit it (default None / absent in YAML). When present across trials,
    # `observed_sharpe_std` reads it to estimate the empirical cross-trial dispersion
    # V̂[{SR_n}] the Bailey-López de Prado expected-max benchmark deflates against —
    # the data-driven alternative to the pinned `statistics.DEFAULT_SHARPE_STD` scalar
    # (METHODOLOGY §13).
    sharpe: float | None = None
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
        # A NaN/Inf Sharpe would silently poison the empirical dispersion estimate;
        # reject it loudly (METHODOLOGY §9) rather than record an unusable value.
        if self.sharpe is not None and not math.isfinite(self.sharpe):
            raise ValueError(
                f"{self.id}: sharpe must be a finite number, got {self.sharpe!r}"
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
    """Serialize one entry as a one-item YAML list fragment (`- id: ...`).

    ``exclude_none`` keeps the optional ``sharpe`` field out of the YAML when it
    was not recorded, so sharpe-less entries serialize byte-identically to the
    pre-``sharpe`` format — the append-only git-history drift test stays green.
    Every required field has a non-None default (``artifacts=[]``, ``notes=""``),
    so this only ever drops ``sharpe``.
    """
    payload = entry.model_dump(mode="json", exclude_none=True)
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


def observed_sharpe_std(
    entries: Sequence[LedgerEntry] | None = None,
    path: Path | str = DEFAULT_LEDGER_PATH,
    *,
    sample: bool = True,
    min_trials: int = 2,
) -> float | None:
    """Empirical cross-trial Sharpe dispersion V̂[{SR_n}]^(1/2) from the ledger.

    Computes the standard deviation of the recorded per-trial ``sharpe`` values —
    the data-driven estimate of the cross-trial Sharpe spread the Bailey-López de
    Prado expected-max benchmark deflates against (METHODOLOGY §13). Entries with
    no ``sharpe`` (the field is optional; older / infrastructure entries omit it)
    are skipped.

    Returns ``None`` when fewer than ``min_trials`` entries carry a ``sharpe`` — a
    standard deviation over <2 points is undefined/unreliable. The caller (e.g.
    ``regime_metrics.dsr_aware_gate_report``) is expected to fall back to the pinned
    ``statistics.DEFAULT_SHARPE_STD`` scalar in that case; check ``is None``
    explicitly rather than truthiness, because a genuine zero-dispersion ledger
    (every recorded Sharpe identical) returns ``0.0``, not ``None``.

    Parameters
    ----------
    entries:
        Pre-loaded ledger to measure; ``None`` loads the default ledger from
        ``path``.
    sample:
        ``True`` (default) uses the unbiased sample std (ddof=1, the V̂ estimator);
        ``False`` uses the population std (ddof=0).
    min_trials:
        Minimum number of sharpe-carrying entries required to return a value.

    Returns
    -------
    The std as a float, or ``None`` if too few trials carry a Sharpe.
    """
    if entries is None:
        entries = load_ledger(path)
    sharpes = [e.sharpe for e in entries if e.sharpe is not None]
    if len(sharpes) < min_trials:
        return None
    std = statistics.stdev(sharpes) if sample else statistics.pstdev(sharpes)
    return float(std)


def next_ledger_id(date: str, entries: Sequence[LedgerEntry]) -> str:
    """Return the next free `ledger-<date>-NNNN` id for `date` (``YYYY-MM-DD``).

    Scans `entries` for ids already using that date prefix and returns the
    max sequence + 1, zero-padded to four digits (0001 if none exist).
    """
    prefix = f"ledger-{date}-"
    seqs = [
        int(e.id[len(prefix):])
        for e in entries
        if e.id.startswith(prefix) and e.id[len(prefix):].isdigit()
    ]
    nxt = (max(seqs) + 1) if seqs else 1
    return f"{prefix}{nxt:04d}"


def record_run(
    metadata: Mapping[str, object] | dict | Path | str,
    *,
    prd: str,
    milestone: str,
    preregistration: str,
    n_comparisons: int,
    verdict: str,
    agent: str = "human",
    entry_id: str | None = None,
    artifacts: Sequence[str] | None = None,
    notes: str = "",
    sharpe: float | None = None,
    path: Path | str = DEFAULT_LEDGER_PATH,
    skip_if_exists: bool = True,
) -> LedgerEntry | None:
    """Map a runner's `metadata.json` to a `LedgerEntry` and append it.

    This is the integration point METHODOLOGY §12 asks for: every runner
    records its trial in the ledger instead of the ledger being populated by
    hand. `metadata` may be a dict or a path to a `metadata.json` written by a
    runner (e.g. `scripts/run_phase4a_arms.py`). The run-specific fields
    (`config_hash`, `started_at`, `finished_at`) come from the metadata; the
    pre-registration fields (`prd`, `milestone`, `preregistration`,
    `n_comparisons`, `verdict`) are supplied by the caller because they are not
    knowable from run mechanics alone — in particular `verdict` is decided by
    the gate, which runs *after* the arm produces its returns.

    The optional `sharpe` (the trial's headline annualised Sharpe) defaults to the
    metadata's `aggregate_sharpe` when the caller doesn't pass one; it is stored so
    `observed_sharpe_std` can later estimate the empirical DSR dispersion. Runners
    that record no Sharpe leave it None (back-compat).

    Idempotency: with `skip_if_exists=True` (default), if the ledger already
    contains an entry with the same `config_hash` this is a no-op returning
    `None` — so re-running a runner (or running one whose trial was backfilled,
    as with Phase 4A M1-M6) does not double-count `N`.

    `entry_id` defaults to the next free `ledger-<completed-date>-NNNN`.
    Returns the appended `LedgerEntry`, or `None` if skipped.
    """
    if isinstance(metadata, (str, Path)):
        with Path(metadata).open("r", encoding="utf-8") as f:
            metadata = json.load(f)

    config_hash = metadata["config_hash"]
    started_at = metadata["started_at"]
    completed_at = metadata.get("finished_at", metadata.get("completed_at"))
    if completed_at is None:
        raise KeyError(
            "run metadata is missing a completion timestamp "
            "('finished_at' or 'completed_at')"
        )

    # Pull the trial's headline Sharpe from metadata when the caller didn't pass
    # one explicitly — `run_phase4a_arms.py` writes `aggregate_sharpe`. Optional
    # and back-compat: runners that omit it leave `sharpe` None (METHODOLOGY §12).
    # A non-finite metadata Sharpe (a runner writes float("nan") when an arm
    # produced no returns) is treated as absent, not recorded — keeps `record_run`
    # working for empty arms while the LedgerEntry validator still rejects an
    # explicitly-passed NaN as caller error.
    if sharpe is None:
        meta_sharpe = metadata.get("aggregate_sharpe")
        if meta_sharpe is not None and math.isfinite(meta_sharpe):
            sharpe = float(meta_sharpe)

    path = Path(path)
    existing = load_ledger(path)
    if skip_if_exists and any(e.config_hash == config_hash for e in existing):
        return None

    if entry_id is None:
        date = str(completed_at)[:10]  # YYYY-MM-DD prefix of the ISO timestamp
        entry_id = next_ledger_id(date, existing)

    entry = LedgerEntry(
        id=entry_id,
        prd=prd,
        milestone=milestone,
        agent=agent,
        preregistration=preregistration,
        config_hash=config_hash,
        n_comparisons=n_comparisons,
        sharpe=sharpe,
        started_at=started_at,
        completed_at=completed_at,
        verdict=verdict,
        artifacts=list(artifacts or []),
        notes=notes,
    )
    return append_ledger_entry(entry, path)
