"""Tests for src/quant/ledger.py + the trial-count ledger drift checks.

Three jobs:
  * schema/loader/writer unit behaviour (``TestLedgerEntry``,
    ``TestLoadLedger``, ``TestAppendLedger``);
  * the *real* ``data/ledger.yaml`` is schema-valid, uniquely-id'd,
    monotonically dated, and sums to the PHASE_4A_REPORT §7 trial count
    (``TestRealLedger``);
  * append-only across git history — every committed revision of the ledger
    is a content-prefix of every later one (``TestAppendOnlyAcrossCommits``).
"""
from __future__ import annotations

import statistics as stdlib_statistics
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from quant.ledger import (
    DEFAULT_LEDGER_PATH,
    PROJECT_ROOT,
    LedgerEntry,
    _parse_entries,
    append_ledger_entry,
    cumulative_trial_count,
    load_ledger,
    next_ledger_id,
    observed_sharpe_std,
    record_run,
)

UTC = timezone.utc


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #

def _entry_dict(**overrides: object) -> dict:
    """A minimal valid entry dict; override any field via kwargs."""
    base = {
        "id": "ledger-2026-01-01-0001",
        "prd": "test-prd",
        "milestone": "M1",
        "agent": "human",
        "preregistration": "docs/somewhere.md",
        "config_hash": "deadbeef",
        "n_comparisons": 1,
        "started_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T01:00:00Z",
        "verdict": "gate_failed",
        "artifacts": ["data/x/"],
        "notes": "n",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------- #

class TestLedgerEntry:
    def test_valid_entry_constructs(self):
        entry = LedgerEntry(**_entry_dict())
        assert entry.id == "ledger-2026-01-01-0001"
        assert entry.started_at.tzinfo is not None

    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError):
            LedgerEntry(**_entry_dict(surprise="nope"))

    def test_bad_verdict_enum_rejected(self):
        with pytest.raises(ValidationError):
            LedgerEntry(**_entry_dict(verdict="maybe"))

    def test_bad_agent_enum_rejected(self):
        with pytest.raises(ValidationError):
            LedgerEntry(**_entry_dict(agent="robot"))

    def test_negative_n_comparisons_rejected(self):
        with pytest.raises(ValidationError):
            LedgerEntry(**_entry_dict(n_comparisons=-1))

    def test_zero_n_comparisons_allowed(self):
        # Infrastructure milestones register 0 comparisons.
        entry = LedgerEntry(**_entry_dict(n_comparisons=0))
        assert entry.n_comparisons == 0

    def test_naive_timestamp_rejected(self):
        with pytest.raises(ValidationError, match="timezone-aware"):
            LedgerEntry(**_entry_dict(started_at="2026-01-01T00:00:00"))

    def test_completed_before_started_rejected(self):
        with pytest.raises(ValidationError, match="precedes started_at"):
            LedgerEntry(
                **_entry_dict(
                    started_at="2026-01-01T05:00:00Z",
                    completed_at="2026-01-01T01:00:00Z",
                )
            )

    def test_missing_required_field_rejected(self):
        d = _entry_dict()
        del d["config_hash"]
        with pytest.raises(ValidationError):
            LedgerEntry(**d)

    def test_sharpe_optional_defaults_none(self):
        # Back-compat: an entry without a sharpe is valid; the field is absent.
        entry = LedgerEntry(**_entry_dict())
        assert entry.sharpe is None

    def test_sharpe_present_stored_as_float(self):
        entry = LedgerEntry(**_entry_dict(sharpe=-0.12))
        assert entry.sharpe == pytest.approx(-0.12)

    def test_sharpe_nan_rejected(self):
        with pytest.raises(ValidationError, match="sharpe must be a finite number"):
            LedgerEntry(**_entry_dict(sharpe=float("nan")))

    def test_sharpe_inf_rejected(self):
        with pytest.raises(ValidationError, match="sharpe must be a finite number"):
            LedgerEntry(**_entry_dict(sharpe=float("inf")))


# --------------------------------------------------------------------- #
# Loader
# --------------------------------------------------------------------- #

class TestLoadLedger:
    def test_missing_file_is_empty_ledger(self, tmp_path: Path):
        assert load_ledger(tmp_path / "nope.yaml") == []

    def test_empty_file_is_empty_ledger(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        path.write_text("# just a comment, no entries\n")
        assert load_ledger(path) == []

    def test_roundtrip(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        path.write_text(yaml.safe_dump([_entry_dict()]))
        entries = load_ledger(path)
        assert len(entries) == 1
        assert isinstance(entries[0], LedgerEntry)

    def test_non_list_top_level_rejected(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        path.write_text("entries: {}\n")
        with pytest.raises(ValueError, match="must be a YAML list"):
            load_ledger(path)

    def test_duplicate_ids_rejected(self):
        dup = _entry_dict(id="ledger-2026-01-01-0001")
        dup2 = _entry_dict(
            id="ledger-2026-01-01-0001",
            started_at="2026-01-02T00:00:00Z",
            completed_at="2026-01-02T01:00:00Z",
        )
        with pytest.raises(ValueError, match=r"duplicate ids.*ledger-2026-01-01-0001"):
            _parse_entries([dup, dup2])

    def test_non_monotonic_started_at_rejected(self):
        first = _entry_dict(
            id="a", started_at="2026-01-02T00:00:00Z",
            completed_at="2026-01-02T01:00:00Z",
        )
        second = _entry_dict(
            id="b", started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T01:00:00Z",
        )
        with pytest.raises(ValueError, match="not monotonically dated"):
            _parse_entries([first, second])

    def test_equal_started_at_is_allowed(self):
        # Same-instant entries (e.g. two arms logged together) are fine.
        a = _entry_dict(id="a")
        b = _entry_dict(id="b")
        entries = _parse_entries([a, b])
        assert [e.id for e in entries] == ["a", "b"]


# --------------------------------------------------------------------- #
# Writer
# --------------------------------------------------------------------- #

class TestAppendLedger:
    def test_append_to_new_file_creates_it(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        append_ledger_entry(_entry_dict(), path)
        assert path.exists()
        assert len(load_ledger(path)) == 1

    def test_append_preserves_existing_bytes(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        header = "# header comment kept verbatim\n"
        path.write_text(header)
        append_ledger_entry(_entry_dict(id="a"), path)
        text = path.read_text()
        assert text.startswith(header)  # never rewrote the header
        append_ledger_entry(
            _entry_dict(
                id="b", started_at="2026-01-02T00:00:00Z",
                completed_at="2026-01-02T01:00:00Z",
            ),
            path,
        )
        text2 = path.read_text()
        # Appending the second entry left the first entry's bytes untouched.
        assert text2.startswith(text)
        assert [e.id for e in load_ledger(path)] == ["a", "b"]

    def test_append_accepts_model_instance(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        returned = append_ledger_entry(LedgerEntry(**_entry_dict()), path)
        assert isinstance(returned, LedgerEntry)

    def test_sharpe_omitted_from_yaml_when_absent(self, tmp_path: Path):
        # A sharpe-less entry must serialize byte-identically to the pre-sharpe
        # format — no `sharpe:` key in the file (back-compat / append-only safety).
        path = tmp_path / "ledger.yaml"
        append_ledger_entry(_entry_dict(), path)
        assert "sharpe" not in path.read_text()
        assert load_ledger(path)[0].sharpe is None

    def test_sharpe_written_and_reloaded_when_present(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        append_ledger_entry(_entry_dict(sharpe=1.25), path)
        assert "sharpe: 1.25" in path.read_text()
        assert load_ledger(path)[0].sharpe == pytest.approx(1.25)

    def test_duplicate_id_rejected(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        append_ledger_entry(_entry_dict(id="dup"), path)
        with pytest.raises(ValueError, match=r"already contains id 'dup'"):
            append_ledger_entry(
                _entry_dict(
                    id="dup", started_at="2026-02-01T00:00:00Z",
                    completed_at="2026-02-01T01:00:00Z",
                ),
                path,
            )

    def test_out_of_order_date_rejected(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        append_ledger_entry(
            _entry_dict(
                id="late", started_at="2026-03-01T00:00:00Z",
                completed_at="2026-03-01T01:00:00Z",
            ),
            path,
        )
        with pytest.raises(ValueError, match="append-only and monotonically dated"):
            append_ledger_entry(
                _entry_dict(
                    id="early", started_at="2026-01-01T00:00:00Z",
                    completed_at="2026-01-01T01:00:00Z",
                ),
                path,
            )


class TestCumulativeTrialCount:
    def test_sums_n_comparisons(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        append_ledger_entry(_entry_dict(id="a", n_comparisons=3), path)
        append_ledger_entry(
            _entry_dict(
                id="b", n_comparisons=4, started_at="2026-01-02T00:00:00Z",
                completed_at="2026-01-02T01:00:00Z",
            ),
            path,
        )
        assert cumulative_trial_count(path=path) == 7

    def test_accepts_preloaded_entries(self):
        entries = [LedgerEntry(**_entry_dict(id="a", n_comparisons=2))]
        assert cumulative_trial_count(entries) == 2


class TestObservedSharpeStd:
    @staticmethod
    def _entries(sharpes: list[float | None]) -> list[LedgerEntry]:
        """Build entries with the given per-trial sharpes (None = field absent)."""
        return [
            LedgerEntry(**_entry_dict(id=f"e{i}", sharpe=s))
            for i, s in enumerate(sharpes)
        ]

    def test_sample_std_of_recorded_sharpes(self):
        # statistics.stdev([0.0, 1.0, 2.0]) == 1.0 (ddof=1).
        entries = self._entries([0.0, 1.0, 2.0])
        assert observed_sharpe_std(entries) == pytest.approx(1.0)

    def test_population_std_when_sample_false(self):
        # statistics.pstdev([0.0, 1.0, 2.0]) == sqrt(2/3).
        entries = self._entries([0.0, 1.0, 2.0])
        assert observed_sharpe_std(entries, sample=False) == pytest.approx(
            (2.0 / 3.0) ** 0.5
        )

    def test_skips_entries_without_sharpe(self):
        # Only the two sharpe-carrying entries feed the std; the None is ignored.
        entries = self._entries([1.0, None, 3.0])
        assert observed_sharpe_std(entries) == pytest.approx(
            stdlib_statistics.stdev([1.0, 3.0])
        )

    def test_none_when_fewer_than_min_trials(self):
        assert observed_sharpe_std(self._entries([0.5])) is None
        assert observed_sharpe_std(self._entries([None, None])) is None
        assert observed_sharpe_std([]) is None

    def test_identical_sharpes_returns_zero_not_none(self):
        # A genuine zero-dispersion ledger is 0.0, not None — callers must check
        # `is None`, not truthiness, to distinguish "no data" from "no spread".
        result = observed_sharpe_std(self._entries([0.7, 0.7, 0.7]))
        assert result == 0.0
        assert result is not None

    def test_custom_min_trials(self):
        entries = self._entries([1.0, 2.0])
        assert observed_sharpe_std(entries, min_trials=3) is None
        assert observed_sharpe_std(entries, min_trials=2) == pytest.approx(
            stdlib_statistics.stdev([1.0, 2.0])
        )

    def test_reads_from_path(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        append_ledger_entry(_entry_dict(id="a", sharpe=0.0), path)
        append_ledger_entry(
            _entry_dict(
                id="b", sharpe=2.0, started_at="2026-01-02T00:00:00Z",
                completed_at="2026-01-02T01:00:00Z",
            ),
            path,
        )
        assert observed_sharpe_std(path=path) == pytest.approx(
            stdlib_statistics.stdev([0.0, 2.0])
        )


# --------------------------------------------------------------------- #
# Runner integration: next_ledger_id + record_run
# --------------------------------------------------------------------- #

def _meta(
    config_hash: str = "abc123",
    started: str = "2026-07-01T00:00:00+00:00",
    finished: str = "2026-07-01T01:00:00+00:00",
) -> dict:
    """Synthetic runner metadata.json payload."""
    return {
        "config_hash": config_hash,
        "started_at": started,
        "finished_at": finished,
        "n_folds": 10,
        "aggregate_sharpe": -0.1,
    }


class TestNextLedgerId:
    def test_first_id_for_date(self):
        assert next_ledger_id("2026-07-01", []) == "ledger-2026-07-01-0001"

    def test_increments_within_date(self):
        entries = [LedgerEntry(**_entry_dict(id="ledger-2026-07-01-0001"))]
        assert next_ledger_id("2026-07-01", entries) == "ledger-2026-07-01-0002"

    def test_independent_per_date(self):
        entries = [LedgerEntry(**_entry_dict(id="ledger-2026-07-01-0003"))]
        assert next_ledger_id("2026-07-02", entries) == "ledger-2026-07-02-0001"


class TestRecordRun:
    REG = dict(
        prd="b1",
        milestone="B1-M2",
        preregistration="docs/x.md",
        n_comparisons=4,
        verdict="gate_failed",
    )

    def test_maps_metadata_fields(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        entry = record_run(_meta(), **self.REG, artifacts=["data/run/"], path=path)
        assert entry is not None
        assert entry.config_hash == "abc123"
        assert entry.started_at.isoformat() == "2026-07-01T00:00:00+00:00"
        assert entry.completed_at.isoformat() == "2026-07-01T01:00:00+00:00"
        assert entry.n_comparisons == 4
        assert entry.verdict == "gate_failed"
        assert entry.artifacts == ["data/run/"]
        # Round-trips through the file.
        assert [e.id for e in load_ledger(path)] == [entry.id]

    def test_auto_id_from_completed_date(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        e1 = record_run(_meta(config_hash="h1"), **self.REG, path=path)
        e2 = record_run(
            _meta(
                config_hash="h2",
                started="2026-07-01T02:00:00+00:00",
                finished="2026-07-01T03:00:00+00:00",
            ),
            **self.REG,
            path=path,
        )
        assert e1.id == "ledger-2026-07-01-0001"
        assert e2.id == "ledger-2026-07-01-0002"

    def test_explicit_id_honored(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        entry = record_run(_meta(), **self.REG, entry_id="ledger-custom-9", path=path)
        assert entry.id == "ledger-custom-9"

    def test_idempotent_on_duplicate_config_hash(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        first = record_run(_meta(config_hash="dup"), **self.REG, path=path)
        assert first is not None
        second = record_run(
            _meta(
                config_hash="dup",
                started="2026-07-02T00:00:00+00:00",
                finished="2026-07-02T01:00:00+00:00",
            ),
            **self.REG,
            path=path,
        )
        assert second is None  # skipped — config_hash already recorded
        assert len(load_ledger(path)) == 1

    def test_reads_metadata_json_file(self, tmp_path: Path):
        meta_path = tmp_path / "metadata.json"
        import json as _json

        meta_path.write_text(_json.dumps(_meta(config_hash="fromfile")))
        path = tmp_path / "ledger.yaml"
        entry = record_run(meta_path, **self.REG, path=path)
        assert entry.config_hash == "fromfile"

    def test_missing_completion_timestamp_raises(self, tmp_path: Path):
        bad = {"config_hash": "x", "started_at": "2026-07-01T00:00:00+00:00"}
        with pytest.raises(KeyError, match="completion timestamp"):
            record_run(bad, **self.REG, path=tmp_path / "ledger.yaml")

    def test_sharpe_pulled_from_metadata(self, tmp_path: Path):
        # _meta() carries aggregate_sharpe=-0.1 — record_run reads it by default.
        path = tmp_path / "ledger.yaml"
        entry = record_run(_meta(), **self.REG, path=path)
        assert entry.sharpe == pytest.approx(-0.1)
        assert load_ledger(path)[0].sharpe == pytest.approx(-0.1)

    def test_explicit_sharpe_overrides_metadata(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        entry = record_run(_meta(), **self.REG, sharpe=0.9, path=path)
        assert entry.sharpe == pytest.approx(0.9)

    def test_nan_metadata_sharpe_recorded_as_none(self, tmp_path: Path):
        # A runner writes float("nan") when an arm produced no returns; record_run
        # must treat it as absent (not crash, not store a NaN).
        path = tmp_path / "ledger.yaml"
        meta = _meta()
        meta["aggregate_sharpe"] = float("nan")
        entry = record_run(meta, **self.REG, path=path)
        assert entry.sharpe is None

    def test_no_metadata_sharpe_leaves_none(self, tmp_path: Path):
        path = tmp_path / "ledger.yaml"
        meta = _meta()
        del meta["aggregate_sharpe"]
        entry = record_run(meta, **self.REG, path=path)
        assert entry.sharpe is None


# --------------------------------------------------------------------- #
# The real ledger
# --------------------------------------------------------------------- #

class TestRealLedger:
    """Validate the committed data/ledger.yaml."""

    EXPECTED_TOTAL_N = 75  # 62 Phase 4A (PHASE_4A_REPORT.md §7) + 12 B1-M3 (B1_REPORT.md §6: 4 arms × 3) + 1 B2-M2 (oos-attribution.md: n_comparisons=1, the single validated method).

    def test_loads_and_validates(self):
        entries = load_ledger()
        assert len(entries) >= 9  # M1, M2, M5, M3, M4 + 4 M6 arms.
        for e in entries:
            assert isinstance(e, LedgerEntry)

    def test_total_trial_count_matches_report(self):
        assert cumulative_trial_count() == self.EXPECTED_TOTAL_N

    def test_all_phase4a_milestones_present(self):
        milestones = {e.milestone for e in load_ledger() if e.prd == "phase-4a"}
        assert {"M1", "M2", "M3", "M4", "M5", "M6"} <= milestones

    def test_m6_arms_reference_checkpoint_dirs(self):
        m6 = [e for e in load_ledger() if e.milestone == "M6"]
        assert len(m6) == 4  # arima control + 3 GBM arms.
        for e in m6:
            assert e.artifacts, f"{e.id} has no artifacts"
            assert all(a.startswith("data/phase4a/") for a in e.artifacts)
            assert len(e.config_hash) == 64  # sha-256 runner hash.

    def test_preregistration_paths_exist_on_disk(self):
        """Committed prereg artifacts (.claude/plans, docs/) must resolve."""
        for e in load_ledger():
            rel = e.preregistration.split("#", 1)[0]  # drop any #anchor
            assert (PROJECT_ROOT / rel).exists(), (
                f"{e.id}: preregistration path not found: {rel}"
            )

    def test_default_path_is_under_data(self):
        assert DEFAULT_LEDGER_PATH == PROJECT_ROOT / "data" / "ledger.yaml"


# --------------------------------------------------------------------- #
# Append-only across git history
# --------------------------------------------------------------------- #

def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def _normalize(text: str) -> list[dict]:
    """Parse a ledger blob into a comparable, schema-validated list of dicts."""
    return [e.model_dump(mode="json") for e in _parse_entries(yaml.safe_load(text))]


class TestAppendOnlyAcrossCommits:
    """Every committed revision of the ledger is a content-prefix of the next.

    Caveat: in a shallow clone only the available history is checked. The
    test skips cleanly when git is unavailable or the ledger has not been
    committed yet (e.g. on the very first commit that introduces it).
    """

    REL = "data/ledger.yaml"

    def _revisions(self) -> list[str]:
        if _git("rev-parse", "--git-dir").returncode != 0:
            pytest.skip("not a git repository")
        log = _git("log", "--format=%H", "--", self.REL)
        if log.returncode != 0:
            pytest.skip("git log failed")
        return [line for line in log.stdout.splitlines() if line]

    def test_history_is_append_only(self):
        revs = self._revisions()  # newest-first
        if len(revs) < 2:
            pytest.skip("ledger has fewer than 2 committed revisions yet")
        # Walk oldest -> newest; each older list must prefix the newer one.
        chronological = list(reversed(revs))
        prev_entries: list[dict] | None = None
        prev_rev = ""
        for rev in chronological:
            blob = _git("show", f"{rev}:{self.REL}")
            if blob.returncode != 0:
                continue
            entries = _normalize(blob.stdout)
            if prev_entries is not None:
                assert entries[: len(prev_entries)] == prev_entries, (
                    f"ledger entries changed between {prev_rev[:8]} and {rev[:8]} — "
                    "the ledger is append-only; existing entries may not be "
                    "modified, reordered, or deleted"
                )
            prev_entries, prev_rev = entries, rev

    def test_working_tree_extends_last_commit(self):
        """Uncommitted edits may only append, never alter committed entries."""
        revs = self._revisions()
        if not revs:
            pytest.skip("ledger not committed yet")
        head_blob = _git("show", f"{revs[0]}:{self.REL}")
        if head_blob.returncode != 0:
            pytest.skip("could not read HEAD ledger blob")
        committed = _normalize(head_blob.stdout)
        working = _normalize(DEFAULT_LEDGER_PATH.read_text())
        assert working[: len(committed)] == committed, (
            "working-tree ledger modifies committed entries — append only"
        )


# --------------------------------------------------------------------- #
# Round-trip determinism (writer output re-loads identically)
# --------------------------------------------------------------------- #

def test_writer_output_reloads_identically(tmp_path: Path):
    path = tmp_path / "ledger.yaml"
    start = datetime(2026, 5, 1, tzinfo=UTC)
    for i in range(3):
        ts = start + timedelta(days=i)
        append_ledger_entry(
            _entry_dict(
                id=f"ledger-2026-05-0{i + 1}-0001",
                started_at=ts.isoformat(),
                completed_at=(ts + timedelta(hours=1)).isoformat(),
                n_comparisons=i,
            ),
            path,
        )
    entries = load_ledger(path)
    assert [e.id for e in entries] == [
        "ledger-2026-05-01-0001",
        "ledger-2026-05-02-0001",
        "ledger-2026-05-03-0001",
    ]
    assert cumulative_trial_count(path=path) == 0 + 1 + 2
