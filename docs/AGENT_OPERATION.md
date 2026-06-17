# Agent Operation Procedure

> **Audience**: every agent (and human contributor) picking up work in
> this repo.
>
> **Purpose**: codify the standard operating procedure so a fresh-context
> agent can execute any ready task end-to-end with a minimal prompt and
> no per-task instructions from the user.
>
> **Authority**: this document defines *how to operate*.
> [`METHODOLOGY.md`](METHODOLOGY.md) defines *how to be honest about
> what you build*. Together they cover the full contract.

---

## The minimal prompt

The user's standard invocation is some variant of:

> *"Pick up the next ready task from `docs/PRIORITIES.yaml`."*

That is sufficient. Do not ask "which task?", "what do you want me to
do?", or "should I plan first?". The procedure below is the answer.

If the user provides more guidance (a specific task ID, a constraint, a
priority change), honor it; otherwise follow the default flow exactly.

---

## The procedure (every task, every time)

### Step 1 — Orient

Read, in this order:

1. `CLAUDE.md` — auto-loaded; project conventions, env, codebase map.
2. `docs/PROJECT_ROADMAP.md` — current portfolio, ratified decisions.
3. `docs/METHODOLOGY.md` — binding contract (the 20 rules).
4. `docs/PRIORITIES.yaml` — find the lowest-`rank` task with
   `status: ready`. **That is your task.** Do not pick a different one.
5. The task's `references.primary` and `references.methodology` paths.
6. Any `docs/historical/` PRD or `docs/concepts/` doc the task references.

Do not skip orientation even on a small task. Methodology compliance
starts with reading the contract.

### Step 2 — Generate and surface an execution plan

Write a plan as your first user-visible message. It must cover:

- **Restatement** of the task's deliverable in your own words (proves
  you understood it).
- **Approach** — files to create or modify, design decisions made.
- **Methodology cross-check** — which `METHODOLOGY.md` rules this work
  has to honor. Always at least §1 (pre-commitment), §15
  (tests-with-code), §20 (post-task review). Usually §6 (drift
  contracts) and §17 (E2E notebook for cross-module changes).
- **Verification** — how you'll know the work is correct (tests, lint,
  drift checks).
- **Anticipated discovered follow-ups** — work you expect to surface
  during execution.
- **Decisions needing user input** — see §"When to pause" below. Empty
  if none.

If decisions are empty, **proceed to Step 3 without waiting for
approval**. The plan is for visibility, not gating. The user interrupts
if they want changes.

If decisions are non-empty, **stop and wait for user input** on those
specific points. Proceed once resolved.

### Step 3 — Commit the status change

Set the task's `status: in_progress` and `started_at: YYYY-MM-DDTHH:MM:SSZ`
(UTC) in `docs/PRIORITIES.yaml`. Commit this change **alone**, with a
message like:

```
chore(priorities): mark <TASK-ID> in_progress
```

A one-line commit with no source code. This exists for the audit trail
— so a reader of `git log` can see exactly when each task started and
how long it took. Do not bundle this with the deliverable.

### Step 4 — Execute the work

Build the deliverable per the plan. Tests land alongside the code
(METHODOLOGY §15). End-to-end notebooks land for cross-module changes
(§17).

Run as you go — don't batch debugging to the end. If a test fails,
fix it before moving on.

### Step 5 — Verify

Run, and **show the actual output** of the relevant checks:

| Check | Command | When |
|---|---|---|
| New-module tests | `.venv/bin/pytest tests/test_<module>.py -v` | Always when new tests landed |
| Full suite | `.venv/bin/pytest tests/` | When the change is cross-cutting; baseline is 467 passed / 4 skipped at Phase 4A close |
| Lint | `.venv/bin/ruff check src/ tests/ scripts/` | Always |
| Drift tests | `.venv/bin/pytest tests/test_catalog.py tests/test_ledger.py tests/test_priorities.py` (as they exist) | Always for the files they cover |
| Notebook execution | `.venv/bin/jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=N notebooks/<nb>.ipynb` | When an E2E notebook landed |

Do not paraphrase test results ("tests pass", "all green"). Quote the
output. METHODOLOGY §9 forbids verdict laundering — applies to test
results too.

### Step 6 — Post-task review (mandatory)

Before flipping `status` to `done`, run METHODOLOGY §20:

- **Re-read the deliverable as if reviewing someone else's code.** Look
  for limitations, edge cases, unintended behaviour changes,
  silent-fallback patterns.
- **Cross-check against the methodology rules you listed in your
  plan.** Be specific: "§1 — thresholds pinned in `<file>:<line>`"; "§6
  — drift test in `tests/test_<x>.py` covers both directions"; "§15 —
  tests cover X% of new lines per `pytest --cov`."
- **Identify discovered follow-up tasks** — gaps the work surfaced,
  missing tests, future improvements, edge cases deferred, scope items
  uncovered.
- **Note any methodology deviations** (e.g. a corner you cut for time,
  a test you skipped, a check you couldn't run). Be explicit. Silent
  corners are worse than declared ones (§9).

Surface the review as a user-visible message: a short markdown section
titled "Post-task review" with sub-sections "Cross-check", "Discovered
follow-ups", and "Deviations".

### Step 7 — Append discovered follow-ups

For each follow-up identified in Step 6:

1. Append a new task to `docs/PRIORITIES.yaml`'s `tasks` array per the
   `append_protocol` defined in that file's `execution_model` section.
2. Choose a unique `id`, e.g. `A-LEDGER-CI` or `B1-PRD-SCHEMA-V2`.
3. Set `status: ready` if it has no unmet dependencies, else `blocked`
   with `depends_on` populated.
4. Set `rank` to fit the priority order; renumber neighbours if needed
   (renumbering is cheap; the IDs are stable, the ranks are mutable).
5. Populate `notes` with the discovery context: link to the commit, PR,
   file/line, or test where the gap surfaced.

If no follow-ups surfaced, state that explicitly in the post-task
review: "Discovered follow-ups: none." Silence is not the same as no
findings.

### Step 8 — Mark done, walk the blocks list

In `docs/PRIORITIES.yaml`:

- Set `status: done` on the current task.
- Set `completed_at: YYYY-MM-DD`.
- For every ID in the task's `blocks` array: check whether all its
  `depends_on` are now `done`. If so, flip its `status: blocked` →
  `ready`. The next agent picks from this expanded ready-set.

### Step 9 — Commit the deliverable

One commit containing:

- The deliverable (code, tests, docs, notebooks, configs).
- The `PRIORITIES.yaml` updates from Steps 7 and 8.

Commit message format (per `CLAUDE.md` git workflow + this repo's
convention):

```
<type>(<scope>): <subject>

<bullet list of what changed and why>

Closes <TASK-ID>. Discovered follow-ups: <list of any new task IDs, or "none">.

Methodology cross-check: §<rule>, §<rule>, §<rule> satisfied.
Deviations: <list, or "none">.
```

Types per repo convention: `feat`, `fix`, `refactor`, `docs`, `test`,
`chore`, `perf`, `ci`.

**Do NOT include a `Co-Authored-By` trailer** — attribution is disabled
globally in `~/.claude/settings.json`.

### Step 10 — Session log

Append to today's session log at:

```
~/.claude/projects/-Users-jamesdelgado-Projects-quant/sessions/YYYY-MM-DD.md
```

Format (per `CLAUDE.md`'s session-logging section):

```markdown
## HH:MM UTC — <one-line goal>
**Goal:** What the session set out to accomplish
**Status:** Complete | In Progress | Blocked
**Commits:** short hash(es), or "none"
**Key changes:** bullet list of files or modules touched
**Summary:** 2-4 sentences on what was done and why
**Next:** What the next agent/session should do first
```

### Step 11 — Final user-facing report

One concise message at the end of the session:

- What task was completed (ID + title).
- Commits made (short hashes).
- Tests + lint status.
- Discovered follow-ups (with IDs).
- What's next on `PRIORITIES.yaml`.

This is what the user reads when they wake up the next session. Make it
scannable.

---

## When to pause for user approval

Proceed autonomously by default. Surface the decision in your plan
(Step 2) and **wait** if the task involves one of:

- **Design ambiguity** — the deliverable can be reasonably built in two
  or more meaningfully different ways and the task doesn't specify
  which. (e.g. for `A-LEDGER`: library function vs. CLI script vs. both
  as the writer API.)
- **Methodology deviation** — you cannot satisfy a methodology rule
  without compromise. (e.g. a test would take an hour and you have to
  skip it; a drift check is impractical for this case.)
- **Destructive operations beyond the task's stated scope** — anything
  that overwrites, deletes, or rewrites files outside the task's
  `deliverable` list.
- **New file/directory conventions** — adding a new top-level
  directory, a new module under `src/quant/`, or a new doc location not
  implied by the roadmap.
- **Scope creep** — the work would clearly benefit from doing N
  additional things that aren't in the task. **Default behaviour:
  surface them in Step 7 (append as follow-up tasks), don't do them
  now.** Only pause if you genuinely cannot complete the assigned task
  without them.

General rule: irreversible or convention-setting decisions wait;
reversible or convention-following decisions proceed.

---

## Red flags — what NOT to do

- ❌ Skip the orient step ("I already know the methodology"). Read the
  docs every time. They change.
- ❌ Skip the plan ("the task is too simple to plan"). The plan is the
  audit trail. Write it even if it's three lines.
- ❌ Commit `PRIORITIES.yaml` `in_progress` together with code. The
  in-progress commit is standalone (Step 3).
- ❌ Quote test results without running them ("tests would pass").
  Run them. Show the output.
- ❌ Mark `done` without the post-task review. The review is the gate,
  not a formality.
- ❌ Bundle multiple tasks into one session. One task per session, even
  if you "could just also do" the next one. The audit trail breaks
  otherwise.
- ❌ Drop discovered follow-ups on the floor because you "don't think
  they matter." Append them with a `notes` link. The user decides what
  matters.
- ❌ Add `Co-Authored-By` to commit messages.
- ❌ Use destructive git ops (`git reset --hard`, `git push --force`,
  branch deletion) without explicit user request.
- ❌ Skip the session log. Future sessions read it for context.
- ❌ Modify a methodology rule, ledger schema, walk-forward split logic,
  or any other pinned invariant without a roadmap-level decision. The
  Phase 5 hard guardrails (`PHASE_5_AGENTS.md`) apply to humans too.

---

## Standard session shape (what the user expects to see)

Roughly, in order:

1. Three to six `Read` tool calls — the entry-point docs + task
   references.
2. A planning message — your execution plan.
3. (Maybe) a pause for user input on decisions surfaced in the plan.
4. An `Edit` to `PRIORITIES.yaml` (set `in_progress`) + a `Bash` call to
   commit it standalone.
5. `Write` / `Edit` calls for the deliverable.
6. `Bash` calls for `pytest` / `ruff` with visible output.
7. A post-task-review markdown section in your output.
8. `Edit`(s) to `PRIORITIES.yaml` for status flips + appended
   follow-ups.
9. A `Bash` commit of the deliverable + priorities update.
10. A session-log append (Edit on today's session file).
11. A final report message (Step 11 above).

If a step is missing, that's a red flag — even if the deliverable looks
fine.

---

## What this document is *not*

- It is not a substitute for `METHODOLOGY.md`. It tells you how to
  operate; the methodology tells you how to be honest. Read both.
- It is not a substitute for the task itself. The task in
  `PRIORITIES.yaml` defines the deliverable; this doc defines the
  procedure for any deliverable.
- It is not an excuse to skip thinking. Plans must engage with the task,
  not just template-fill the headers.

---

*Status: ACTIVE — binding operating procedure for every agent and human
session in this repo. Updates require a docs PR and a roadmap-level
decision.*
