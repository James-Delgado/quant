"""Drift tests for ``docs/PRIORITIES.yaml`` — the backlog's integrity contract.

This file is referenced as a binding drift test in two places that long
predate its existence:

  * ``docs/PRIORITIES.yaml`` ``execution_model.task_lifecycle`` — "A drift
    test (``tests/test_priorities.py``) enforces: (a) all status values are
    in the enum, (b) all depends_on / blocks references resolve to existing
    task ids, (c) at most one task is ``in_progress``, (d) ``done`` tasks
    have ``completed_at``."
  * ``docs/AGENT_OPERATION.md`` Step 5 — listed in the drift-test row of the
    verification table.

Three further invariants (added by ``A-PRIORITIES-TEST-TS``) cover the
timestamp fields the original four checks did not, but which
``docs/AGENT_OPERATION.md`` requires: Step 3 mandates ``started_at`` on any
claimed task, Step 8 mandates ``completed_at`` on ``done``:

  * (e) every ``in_progress`` or ``done`` task carries ``started_at``;
  * (f) ``completed_at >= started_at`` (date ordering) where both are present;
  * (g) ``started_at`` / ``completed_at``, where present, parse as ISO
    dates/datetimes.

Note on types: ``yaml.safe_load`` parses ``2026-06-17`` as ``datetime.date``
and ``2026-06-17T17:17:12Z`` as ``datetime.datetime`` (tz-aware), while the
synthetic fixtures below pass ISO strings. ``_to_date`` coerces all three.

It mirrors the ``tests/test_catalog.py`` / ``tests/test_ledger.py`` pattern:
small validators that raise ``ValueError`` *naming the offender*, exercised
in both directions (METHODOLOGY §6) — the committed file passes, synthetic
broken inputs raise.

Design note — what is deliberately NOT checked: bidirectional
``depends_on`` / ``blocks`` symmetry. The committed file intentionally
violates it — e.g. ``B1-PRD.blocks`` lists ``B1-M2``/``B1-M3`` (the
transitive descendants) while those tasks ``depend_on`` ``B1-M1``, not
``B1-PRD``. Enforcing symmetry would be a false positive on a valid file,
so reference *resolution* (every referenced id exists) is enforced, but not
edge symmetry.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIORITIES_PATH = PROJECT_ROOT / "docs" / "PRIORITIES.yaml"


# --------------------------------------------------------------------- #
# Loader / accessors
# --------------------------------------------------------------------- #

def load_priorities(path: Path = PRIORITIES_PATH) -> dict:
    """Parse PRIORITIES.yaml; raise if the top level is not a mapping."""
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("PRIORITIES.yaml top level must be a mapping")
    return data


def _tasks(data: dict) -> list[dict]:
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("`tasks` must be a YAML list")
    return tasks


def _status_enum(data: dict) -> set[str]:
    return set(data["schema"]["task_status"])


def _complexity_enum(data: dict) -> set[str]:
    return set(data["schema"]["complexity"])


# --------------------------------------------------------------------- #
# Validators — each raises ValueError naming the offending task(s)
# --------------------------------------------------------------------- #

def check_unique_ids(tasks: list[dict]) -> None:
    seen: set[str] = set()
    dupes: list[str] = []
    for t in tasks:
        tid = t["id"]
        if tid in seen:
            dupes.append(tid)
        seen.add(tid)
    if dupes:
        raise ValueError(f"duplicate task ids: {sorted(set(dupes))}")


def check_unique_ranks(tasks: list[dict]) -> None:
    seen: dict[int, str] = {}
    collisions: list[str] = []
    for t in tasks:
        rank = t["rank"]
        if rank in seen:
            collisions.append(f"rank {rank}: {seen[rank]} & {t['id']}")
        else:
            seen[rank] = t["id"]
    if collisions:
        raise ValueError(f"duplicate ranks: {collisions}")


def check_status_values(tasks: list[dict], enum: set[str]) -> None:
    """(a) every task's status is in the schema enum."""
    bad = [(t["id"], t.get("status")) for t in tasks if t.get("status") not in enum]
    if bad:
        raise ValueError(
            f"tasks with status outside enum {sorted(enum)}: {bad}"
        )


def check_complexity_values(tasks: list[dict], enum: set[str]) -> None:
    bad = [
        (t["id"], t.get("est_complexity"))
        for t in tasks
        if t.get("est_complexity") not in enum
    ]
    if bad:
        raise ValueError(
            f"tasks with est_complexity outside enum {sorted(enum)}: {bad}"
        )


def check_references_resolve(tasks: list[dict]) -> None:
    """(b) every depends_on / blocks reference resolves to an existing id."""
    ids = {t["id"] for t in tasks}
    dangling: list[str] = []
    for t in tasks:
        for field in ("depends_on", "blocks"):
            for ref in t.get(field) or []:
                if ref not in ids:
                    dangling.append(f"{t['id']}.{field} -> {ref}")
    if dangling:
        raise ValueError(f"dangling references: {dangling}")


def check_single_in_progress(tasks: list[dict]) -> None:
    """(c) at most one task is in_progress."""
    in_progress = [t["id"] for t in tasks if t.get("status") == "in_progress"]
    if len(in_progress) > 1:
        raise ValueError(f"more than one task in_progress: {in_progress}")


def check_done_have_completed_at(tasks: list[dict]) -> None:
    """(d) done tasks carry a completed_at."""
    missing = [
        t["id"]
        for t in tasks
        if t.get("status") == "done" and not t.get("completed_at")
    ]
    if missing:
        raise ValueError(f"done tasks missing completed_at: {missing}")


def _to_date(value: object) -> dt.date:
    """Coerce a YAML timestamp to a ``date``; raise ValueError if unparseable.

    Handles the three forms a task timestamp can take: ``datetime.datetime``
    and ``datetime.date`` (what ``yaml.safe_load`` yields for ISO timestamps),
    and ``str`` (the synthetic fixtures). The trailing ``Z`` that the
    ``started_at`` convention uses is normalised to ``+00:00`` for
    ``fromisoformat``.
    """
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        s = value.strip()
        try:
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return dt.date.fromisoformat(s)
            except ValueError:
                raise ValueError(f"unparseable timestamp: {value!r}") from None
    raise ValueError(
        f"timestamp must be a date/datetime/ISO string, got "
        f"{type(value).__name__}: {value!r}"
    )


_TIMESTAMPED_STATUSES = {"in_progress", "done"}


def check_started_at_present(tasks: list[dict]) -> None:
    """(e) every in_progress or done task carries started_at."""
    missing = [
        t["id"]
        for t in tasks
        if t.get("status") in _TIMESTAMPED_STATUSES and not t.get("started_at")
    ]
    if missing:
        raise ValueError(f"in_progress/done tasks missing started_at: {missing}")


def check_timestamps_parse(tasks: list[dict]) -> None:
    """(g) started_at / completed_at, where present, parse as ISO dates."""
    bad: list[str] = []
    for t in tasks:
        for field in ("started_at", "completed_at"):
            value = t.get(field)
            if value is None:
                continue
            try:
                _to_date(value)
            except ValueError:
                bad.append(f"{t['id']}.{field}={value!r}")
    if bad:
        raise ValueError(f"unparseable timestamps: {bad}")


def check_completed_after_started(tasks: list[dict]) -> None:
    """(f) completed_at >= started_at (date ordering) where both present.

    Parse failures are the concern of :func:`check_timestamps_parse`; this
    check skips any value it cannot coerce so the two validators report
    independent failures rather than masking each other.
    """
    violations: list[str] = []
    for t in tasks:
        started = t.get("started_at")
        completed = t.get("completed_at")
        if not started or not completed:
            continue
        try:
            s = _to_date(started)
            c = _to_date(completed)
        except ValueError:
            continue
        if c < s:
            violations.append(
                f"{t['id']}: completed_at {completed} < started_at {started}"
            )
    if violations:
        raise ValueError(f"completed_at precedes started_at: {violations}")


def validate_priorities(data: dict) -> None:
    """Run every drift check. Raises the first ValueError encountered."""
    tasks = _tasks(data)
    status_enum = _status_enum(data)
    complexity_enum = _complexity_enum(data)
    check_unique_ids(tasks)
    check_unique_ranks(tasks)
    check_status_values(tasks, status_enum)
    check_complexity_values(tasks, complexity_enum)
    check_references_resolve(tasks)
    check_single_in_progress(tasks)
    check_done_have_completed_at(tasks)
    check_started_at_present(tasks)
    check_timestamps_parse(tasks)
    check_completed_after_started(tasks)


# --------------------------------------------------------------------- #
# Synthetic-fixture helpers (negative paths)
# --------------------------------------------------------------------- #

def _task(**overrides: object) -> dict:
    """A minimal valid task dict; override any field via kwargs."""
    base: dict = {
        "id": "X-1",
        "rank": 1,
        "title": "t",
        "project": "A",
        "sub_project": "s",
        "status": "ready",
        "depends_on": [],
        "blocks": [],
        "est_complexity": "small",
    }
    base.update(overrides)
    return base


_ENUM = {"ready", "blocked", "in_progress", "done", "skipped"}
_COMPLEXITY = {"small", "medium", "large"}


# --------------------------------------------------------------------- #
# Loader behaviour
# --------------------------------------------------------------------- #

class TestLoadPriorities:
    def test_real_file_loads(self):
        data = load_priorities()
        assert isinstance(data, dict)
        assert _tasks(data)  # non-empty

    def test_non_mapping_top_level_rejected(self, tmp_path: Path):
        path = tmp_path / "p.yaml"
        path.write_text("- just\n- a\n- list\n")
        with pytest.raises(ValueError, match="must be a mapping"):
            load_priorities(path)

    def test_tasks_must_be_a_list(self):
        with pytest.raises(ValueError, match="must be a YAML list"):
            _tasks({"tasks": {"not": "a list"}})

    def test_schema_enums_present_and_nonempty(self):
        data = load_priorities()
        assert _status_enum(data) == _ENUM
        assert _complexity_enum(data) == _COMPLEXITY


# --------------------------------------------------------------------- #
# (a) status enum
# --------------------------------------------------------------------- #

class TestStatusValues:
    def test_valid_statuses_pass(self):
        check_status_values([_task(status="ready"), _task(id="X-2", status="done")], _ENUM)

    def test_unknown_status_rejected(self):
        with pytest.raises(ValueError, match=r"X-9.*frozen"):
            check_status_values([_task(id="X-9", status="frozen")], _ENUM)


# --------------------------------------------------------------------- #
# (b) reference resolution
# --------------------------------------------------------------------- #

class TestReferencesResolve:
    def test_resolving_references_pass(self):
        tasks = [
            _task(id="A", blocks=["B"]),
            _task(id="B", rank=2, depends_on=["A"]),
        ]
        check_references_resolve(tasks)

    def test_dangling_depends_on_rejected(self):
        tasks = [_task(id="A", depends_on=["GHOST"])]
        with pytest.raises(ValueError, match=r"A\.depends_on -> GHOST"):
            check_references_resolve(tasks)

    def test_dangling_blocks_rejected(self):
        tasks = [_task(id="A", blocks=["GHOST"])]
        with pytest.raises(ValueError, match=r"A\.blocks -> GHOST"):
            check_references_resolve(tasks)

    def test_missing_reference_lists_tolerated(self):
        # A task with neither depends_on nor blocks keys must not crash.
        check_references_resolve([{"id": "A"}])


# --------------------------------------------------------------------- #
# (c) single in_progress
# --------------------------------------------------------------------- #

class TestSingleInProgress:
    def test_zero_in_progress_ok(self):
        check_single_in_progress([_task(status="ready"), _task(id="X-2", status="done")])

    def test_one_in_progress_ok(self):
        check_single_in_progress([_task(status="in_progress"), _task(id="X-2", status="ready")])

    def test_two_in_progress_rejected(self):
        tasks = [
            _task(id="A", status="in_progress"),
            _task(id="B", rank=2, status="in_progress"),
        ]
        with pytest.raises(ValueError, match=r"in_progress.*\['A', 'B'\]"):
            check_single_in_progress(tasks)


# --------------------------------------------------------------------- #
# (d) done tasks have completed_at
# --------------------------------------------------------------------- #

class TestDoneHaveCompletedAt:
    def test_done_with_completed_at_passes(self):
        check_done_have_completed_at([_task(status="done", completed_at="2026-06-23")])

    def test_done_without_completed_at_rejected(self):
        with pytest.raises(ValueError, match=r"missing completed_at.*X-7"):
            check_done_have_completed_at([_task(id="X-7", status="done")])

    def test_non_done_without_completed_at_tolerated(self):
        check_done_have_completed_at([_task(status="ready"), _task(id="X-2", status="blocked")])


# --------------------------------------------------------------------- #
# (e) in_progress / done tasks carry started_at
# --------------------------------------------------------------------- #

class TestStartedAtPresent:
    def test_in_progress_with_started_at_passes(self):
        check_started_at_present(
            [_task(status="in_progress", started_at="2026-06-28T21:30:00Z")]
        )

    def test_done_with_started_at_passes(self):
        check_started_at_present(
            [_task(status="done", started_at="2026-06-23", completed_at="2026-06-23")]
        )

    def test_in_progress_without_started_at_rejected(self):
        with pytest.raises(ValueError, match=r"missing started_at.*X-5"):
            check_started_at_present([_task(id="X-5", status="in_progress")])

    def test_done_without_started_at_rejected(self):
        with pytest.raises(ValueError, match=r"missing started_at.*X-6"):
            check_started_at_present([_task(id="X-6", status="done", completed_at="2026-06-23")])

    def test_ready_blocked_without_started_at_tolerated(self):
        check_started_at_present([_task(status="ready"), _task(id="X-2", status="blocked")])


# --------------------------------------------------------------------- #
# (f) completed_at >= started_at
# --------------------------------------------------------------------- #

class TestCompletedAfterStarted:
    def test_completed_after_started_passes(self):
        check_completed_after_started(
            [_task(status="done", started_at="2026-06-23T10:00:00Z", completed_at="2026-06-24")]
        )

    def test_same_day_completion_passes(self):
        # The common case: claimed and finished the same day (datetime vs date).
        check_completed_after_started(
            [_task(status="done", started_at="2026-06-23T17:17:12Z", completed_at="2026-06-23")]
        )

    def test_completed_before_started_rejected(self):
        with pytest.raises(ValueError, match=r"X-8.*completed_at.*< started_at"):
            check_completed_after_started(
                [_task(id="X-8", status="done", started_at="2026-06-24", completed_at="2026-06-23")]
            )

    def test_only_started_at_tolerated(self):
        check_completed_after_started([_task(status="in_progress", started_at="2026-06-23T00:00:00Z")])

    def test_neither_timestamp_tolerated(self):
        check_completed_after_started([_task(status="ready")])


# --------------------------------------------------------------------- #
# (g) timestamps parse
# --------------------------------------------------------------------- #

class TestTimestampsParse:
    def test_iso_string_forms_pass(self):
        check_timestamps_parse(
            [_task(status="done", started_at="2026-06-23T17:17:12Z", completed_at="2026-06-23")]
        )

    def test_native_date_and_datetime_pass(self):
        # The forms yaml.safe_load actually produces from the real file.
        check_timestamps_parse(
            [
                _task(
                    status="done",
                    started_at=dt.datetime(2026, 6, 23, 17, 17, 12, tzinfo=dt.timezone.utc),
                    completed_at=dt.date(2026, 6, 23),
                )
            ]
        )

    def test_unparseable_started_at_rejected(self):
        with pytest.raises(ValueError, match=r"unparseable timestamps.*X-4.*started_at"):
            check_timestamps_parse([_task(id="X-4", status="done", started_at="not-a-date")])

    def test_unparseable_completed_at_rejected(self):
        with pytest.raises(ValueError, match=r"unparseable timestamps.*X-4.*completed_at"):
            check_timestamps_parse([_task(id="X-4", status="done", completed_at="2026-13-99")])

    def test_missing_timestamps_tolerated(self):
        check_timestamps_parse([_task(status="ready")])


class TestToDate:
    def test_datetime_to_date(self):
        assert _to_date(dt.datetime(2026, 6, 23, 17, 0, tzinfo=dt.timezone.utc)) == dt.date(2026, 6, 23)

    def test_date_passthrough(self):
        assert _to_date(dt.date(2026, 6, 23)) == dt.date(2026, 6, 23)

    def test_iso_datetime_with_z(self):
        assert _to_date("2026-06-23T17:17:12Z") == dt.date(2026, 6, 23)

    def test_iso_date_string(self):
        assert _to_date("2026-06-23") == dt.date(2026, 6, 23)

    def test_bad_string_raises(self):
        with pytest.raises(ValueError, match="unparseable timestamp"):
            _to_date("nope")

    def test_wrong_type_raises(self):
        with pytest.raises(ValueError, match="date/datetime/ISO string"):
            _to_date(12345)


# --------------------------------------------------------------------- #
# Integrity extras (satisfied by the committed file; valuable to hold)
# --------------------------------------------------------------------- #

class TestUniqueIdsAndRanks:
    def test_unique_ids_pass(self):
        check_unique_ids([_task(id="A"), _task(id="B", rank=2)])

    def test_duplicate_ids_rejected(self):
        with pytest.raises(ValueError, match=r"duplicate task ids.*A"):
            check_unique_ids([_task(id="A"), _task(id="A", rank=2)])

    def test_unique_ranks_pass(self):
        check_unique_ranks([_task(id="A", rank=1), _task(id="B", rank=2)])

    def test_duplicate_ranks_rejected(self):
        with pytest.raises(ValueError, match=r"rank 1: A & B"):
            check_unique_ranks([_task(id="A", rank=1), _task(id="B", rank=1)])


class TestComplexityValues:
    def test_valid_complexity_passes(self):
        check_complexity_values([_task(est_complexity="medium")], _COMPLEXITY)

    def test_unknown_complexity_rejected(self):
        with pytest.raises(ValueError, match=r"X-3.*epic"):
            check_complexity_values([_task(id="X-3", est_complexity="epic")], _COMPLEXITY)


# --------------------------------------------------------------------- #
# The real file — the drift test's teeth
# --------------------------------------------------------------------- #

class TestRealPriorities:
    """Validate the committed docs/PRIORITIES.yaml end-to-end."""

    def test_validate_priorities_passes(self):
        validate_priorities(load_priorities())

    def test_every_task_has_required_keys(self):
        required = {"id", "rank", "title", "project", "status", "est_complexity"}
        for t in _tasks(load_priorities()):
            missing = required - set(t)
            assert not missing, f"{t.get('id', '<no id>')} missing keys: {sorted(missing)}"

    def test_done_tasks_have_completed_at(self):
        check_done_have_completed_at(_tasks(load_priorities()))

    def test_at_most_one_in_progress(self):
        check_single_in_progress(_tasks(load_priorities()))

    def test_all_references_resolve(self):
        check_references_resolve(_tasks(load_priorities()))

    def test_in_progress_and_done_have_started_at(self):
        check_started_at_present(_tasks(load_priorities()))

    def test_timestamps_parse(self):
        check_timestamps_parse(_tasks(load_priorities()))

    def test_completion_not_before_start(self):
        check_completed_after_started(_tasks(load_priorities()))
