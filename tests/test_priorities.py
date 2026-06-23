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
