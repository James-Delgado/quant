"""Feedback service layer (Project E1-M6).

Two responsibilities, both pure-ish and unit-tested without a network:

1. **Issue construction** — turn a :class:`FeedbackReport` (the exact payload the
   console's "Report an issue" modal captures) into a pre-filled GitHub
   ``issues/new`` URL + body. The frontend mirrors this construction in
   ``frontend/src/lib/feedback.ts``; the shared payload schema is what keeps the
   two in sync (METHODOLOGY §6). E2 will swap the client-side ``window.open`` for
   a ``POST /feedback`` that reuses *this* module server-side.

2. **Promotion** — turn a ``feedback``-labeled GitHub issue into a
   ``docs/PRIORITIES.yaml`` task with a back-link, so a bug found while using the
   console flows into the work queue (PRD §6, DECISIONS #11). The GitHub read is
   injectable (:func:`fetch_issue_via_gh` by default) so tests mock it and the
   path degrades gracefully when ``gh`` is absent or unauthenticated.

There is **no user-facing tracker** — the set of ``feedback`` issues *is* the
tracker, visible only to engineers/agents (DECISIONS #11).

The ``feedback`` label must exist in the repository for the labeled-issue
contract to hold. It was created once in the canonical repo
``James-Delgado/quant`` (E1-M6-FEEDBACK-LABEL, 2026-06-28: ``color B60205``,
``description "Reported via the Research Console"``). A fork or fresh repo must
recreate it once::

    gh label create feedback --repo <owner>/<repo> \\
        --description "Reported via the Research Console" --color B60205

(GitHub silently drops an unknown ``labels=`` query param, so a missing label
degrades to an *unlabeled* issue rather than an error — create it to keep the
tracker query ``label:feedback`` complete.)
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode

import yaml

from quant.console.sources import DEFAULT_REPO_URL

# The label every reported issue carries (DECISIONS #11). Pinned per METHODOLOGY §1.
FEEDBACK_LABEL = "feedback"

# Allowed enum values for the capture form — pinned to match the modal's <select>
# options (frontend/src/lib/feedback.ts) and the PRD §6 capture schema.
VALID_TYPES: tuple[str, ...] = ("bug", "idea", "data")
VALID_SEVERITIES: tuple[str, ...] = ("low", "med", "high")

# Promoted tasks land under Project E, sub-project "feedback".
PROMOTED_PROJECT = "E"
PROMOTED_SUB_PROJECT = "feedback"

# Repo slug used by the default `gh` reader (owner/name).
DEFAULT_REPO_SLUG = "James-Delgado/quant"

# The backlog file promotion appends to. feedback.py lives at
# src/quant/console/feedback.py → parents[3] is the repo root.
DEFAULT_PRIORITIES_PATH = Path(__file__).resolve().parents[3] / "docs" / "PRIORITIES.yaml"

# An injectable reader: issue number → the issue's JSON dict.
IssueFetcher = Callable[[int], dict]


# ── Capture payload ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FeedbackReport:
    """The "Report an issue" payload — modal fields + auto-captured context.

    Mirrors the TypeScript ``FeedbackReport`` in ``frontend/src/lib/feedback.ts``.
    The four user fields come from the modal; the four context fields are
    captured at submit time (panel route title, build SHA, ISO timestamp, app
    version).
    """

    title: str
    type: str
    severity: str
    description: str
    panel: str
    build_sha: str
    timestamp: str
    app_version: str

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("feedback title must not be empty")
        if self.type not in VALID_TYPES:
            raise ValueError(f"type {self.type!r} not in {VALID_TYPES}")
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(f"severity {self.severity!r} not in {VALID_SEVERITIES}")


# ── Issue construction (shared with the frontend) ────────────────────────────


def issue_title(report: FeedbackReport) -> str:
    """The GitHub issue title — the report title, verbatim (trimmed)."""
    return report.title.strip()


def issue_body(report: FeedbackReport) -> str:
    """Markdown issue body: the description plus the auto-captured context block.

    Kept in step with ``buildIssueBody`` in ``frontend/src/lib/feedback.ts`` so a
    console-filed issue and a service-filed issue read identically (E2 reuses
    this server-side).
    """
    return (
        f"**Type:** {report.type} · **Severity:** {report.severity}\n\n"
        f"{report.description.strip()}\n\n"
        "---\n\n"
        "**Context** (auto-captured)\n\n"
        f"- Panel: {report.panel}\n"
        f"- Build: {report.build_sha}\n"
        f"- App version: {report.app_version}\n"
        f"- Reported: {report.timestamp}\n\n"
        '_Submitted via the Research Console "Report an issue" button._'
    )


def issue_url(report: FeedbackReport, *, repo_url: str = DEFAULT_REPO_URL) -> str:
    """A pre-filled ``issues/new`` URL: title + body + the ``feedback`` label.

    E1's no-backend submission path — the frontend opens this in a new tab.
    """
    query = urlencode(
        {
            "title": issue_title(report),
            "body": issue_body(report),
            "labels": FEEDBACK_LABEL,
        },
        quote_via=quote,
    )
    return f"{repo_url}/issues/new?{query}"


# ── Label helpers (guard arbitrary-issue promotion) ──────────────────────────


def issue_labels(issue: dict) -> list[str]:
    """Extract label *names* from an issue JSON dict.

    Tolerates both shapes we encounter: the ``gh issue view --json labels`` form
    (a list of ``{"name": ...}`` objects) and a plain list of label strings.
    Unknown/empty entries are skipped.
    """
    names: list[str] = []
    for label in issue.get("labels") or []:
        if isinstance(label, dict):
            name = label.get("name")
            if name:
                names.append(name)
        elif isinstance(label, str):
            names.append(label)
    return names


def has_feedback_label(issue: dict) -> bool:
    """True iff the issue carries the :data:`FEEDBACK_LABEL` (DECISIONS #11)."""
    return FEEDBACK_LABEL in issue_labels(issue)


# ── GitHub read (injectable, degrades without `gh`) ──────────────────────────


def fetch_issue_via_gh(
    issue_number: int,
    *,
    repo: str = DEFAULT_REPO_SLUG,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    """Read an issue via the GitHub CLI: ``gh issue view <n> --json …``.

    Raises a clear, actionable :class:`RuntimeError` when ``gh`` is missing or
    the call fails (e.g. unauthenticated), rather than silently degrading —
    promotion is an explicit engineer action that should fail loudly. Tests
    inject a fake fetcher instead of patching ``gh``.
    """
    if shutil.which("gh") is None:
        raise RuntimeError(
            "GitHub CLI `gh` not found. Install it and run `gh auth login`, or "
            "pass an explicit issue_fetcher. See feedback.py module docstring."
        )
    result = runner(
        ["gh", "issue", "view", str(issue_number), "--repo", repo, "--json",
         "number,title,body,url,state,labels"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"`gh issue view {issue_number}` failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return json.loads(result.stdout)


# ── GitHub write (one-click direct submission, opt-in) ───────────────────────


def submit_issue_via_gh(
    report: FeedbackReport,
    *,
    repo: str = DEFAULT_REPO_SLUG,
    label: str = FEEDBACK_LABEL,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str:
    """File the report directly via ``gh issue create``, returning the issue URL.

    The one-step alternative to :func:`issue_url`: that function builds a
    pre-filled ``issues/new`` page the user still has to open and submit (two
    clicks); this files the issue in a single call when a GitHub token is present
    locally (``gh`` authenticated). E2 swaps this for a server-side ``POST`` that
    reuses the same :func:`issue_title` / :func:`issue_body` construction.

    Unlike the URL path — where GitHub *silently drops* an unknown ``labels=``
    query param — ``gh issue create --label`` fails loudly if ``label`` does not
    exist in ``repo``, so a successful return guarantees the issue is labeled
    (the ``feedback`` label exists in the canonical repo; a fork must recreate it
    — see the module docstring). Raises :class:`RuntimeError` when ``gh`` is
    missing or the call fails; tests inject a fake ``runner``.
    """
    if shutil.which("gh") is None:
        raise RuntimeError(
            "GitHub CLI `gh` not found. Install it and run `gh auth login` to "
            "submit directly, or use the pre-filled issue_url() path instead. "
            "See feedback.py module docstring."
        )
    result = runner(
        ["gh", "issue", "create", "--repo", repo,
         "--title", issue_title(report),
         "--body", issue_body(report),
         "--label", label],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"`gh issue create` failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


# ── Issue → PRIORITIES task ──────────────────────────────────────────────────


@dataclass(frozen=True)
class PromotedTask:
    """The PRIORITIES task a promoted issue becomes."""

    id: str
    rank: int
    title: str
    issue_url: str
    body: str
    status: str = "ready"
    project: str = PROMOTED_PROJECT
    sub_project: str = PROMOTED_SUB_PROJECT


def build_task_record(issue: dict, *, rank: int, status: str = "ready") -> PromotedTask:
    """Map a GitHub issue JSON dict to a :class:`PromotedTask`.

    ``id`` is ``FEEDBACK-<number>`` (stable, links the task to its issue). The
    back-link URL falls back to a constructed ``/issues/<n>`` if the issue JSON
    omits ``url`` (it always carries one in practice).
    """
    number = issue["number"]
    url = issue.get("url") or f"{DEFAULT_REPO_URL}/issues/{number}"
    return PromotedTask(
        id=f"FEEDBACK-{number}",
        rank=rank,
        title=issue.get("title") or f"Feedback issue #{number}",
        issue_url=url,
        body=(issue.get("body") or "").strip(),
        status=status,
    )


def _yaml_quote(text: str) -> str:
    """Double-quote a scalar for inline YAML, escaping ``\\`` and ``"``."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _issue_number(task: PromotedTask) -> str:
    return task.id.split("-", 1)[1]


def format_task_block(task: PromotedTask) -> str:
    """Render a :class:`PromotedTask` as a PRIORITIES ``tasks`` list item.

    Matches the file's 2-space indentation and block-scalar ``notes`` style, so
    the appended text round-trips through ``yaml.safe_load`` and passes the
    ``tests/test_priorities.py`` drift checks.
    """
    number = _issue_number(task)
    note_lines = [
        f"Promoted from feedback issue #{number} via `console feedback promote`.",
        f"Issue: {task.issue_url}",
    ]
    if task.body:
        note_lines.append("")
        note_lines.append("Reported context:")
        note_lines.extend(task.body.splitlines())
    notes = "\n".join(f"      {line}".rstrip() for line in note_lines)
    return (
        f"  - id: {task.id}\n"
        f"    rank: {task.rank}\n"
        f"    title: {_yaml_quote(task.title)}\n"
        f"    project: {task.project}\n"
        f"    sub_project: {task.sub_project}\n"
        f"    status: {task.status}\n"
        f"    depends_on: []\n"
        f"    blocks: []\n"
        f"    references:\n"
        f"      issue: {task.issue_url}\n"
        f"    est_complexity: small\n"
        f"    notes: |\n"
        f"{notes}\n"
    )


def append_task_to_priorities(
    path: Path | str,
    block: str,
    *,
    today: str | None = None,
) -> None:
    """Append a rendered task block at EOF and (optionally) bump ``last_updated``.

    ``tasks:`` is the final top-level section of ``docs/PRIORITIES.yaml``, so a
    plain text append continues the list while preserving every comment a YAML
    round-trip would strip. ``today`` (ISO date) updates the ``last_updated``
    header line when provided.
    """
    p = Path(path)
    text = p.read_text()
    if today is not None:
        text = re.sub(
            r"^last_updated:.*$",
            f"last_updated: {today}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
    if not text.endswith("\n"):
        text += "\n"
    text += "\n" + block
    p.write_text(text)


def promote(
    issue_number: int,
    *,
    priorities_path: Path | str = DEFAULT_PRIORITIES_PATH,
    issue_fetcher: IssueFetcher | None = None,
    today: str | None = None,
    status: str = "ready",
    require_label: bool = True,
) -> PromotedTask:
    """Read issue ``issue_number`` and append it to PRIORITIES as a task.

    Returns the :class:`PromotedTask`. Raises :class:`ValueError` if a task for
    this issue already exists (idempotency guard) or — when ``require_label`` is
    True (the default) — if the fetched issue does not carry the ``feedback``
    label, so an arbitrary issue number cannot be promoted by mistake (DECISIONS
    #11: the ``feedback`` issues *are* the tracker). Pass ``require_label=False``
    to override deliberately. ``issue_fetcher`` defaults to
    :func:`fetch_issue_via_gh`, resolved lazily so the module-level reader can be
    monkeypatched in tests.
    """
    if issue_fetcher is None:
        issue_fetcher = fetch_issue_via_gh
    issue = issue_fetcher(issue_number)
    if require_label and not has_feedback_label(issue):
        raise ValueError(
            f"issue #{issue['number']} does not carry the {FEEDBACK_LABEL!r} "
            f"label (labels: {issue_labels(issue) or 'none'}); refusing to "
            "promote. Pass require_label=False to override."
        )
    data = yaml.safe_load(Path(priorities_path).read_text())
    tasks = data.get("tasks") or []
    existing_ids = {t["id"] for t in tasks}
    new_id = f"FEEDBACK-{issue['number']}"
    if new_id in existing_ids:
        raise ValueError(f"task {new_id} already exists in {priorities_path}")
    next_rank = max((t["rank"] for t in tasks), default=0) + 1
    task = build_task_record(issue, rank=next_rank, status=status)
    append_task_to_priorities(priorities_path, format_task_block(task), today=today)
    return task
