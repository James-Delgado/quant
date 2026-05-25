# CLAUDE.md — quant project

## Session logging (required)

A living session log lives at:
`~/.claude/projects/-Users-jamesdelgado-Projects-quant/sessions/YYYY-MM-DD.md`

**When to write a session entry:** At the end of any session where significant
work was done, OR when the context window is approaching its limit (you will
see a system reminder about compaction). Do NOT wait to be asked.

**Format — append this block to today's log file:**

```markdown
## HH:MM UTC — [one-line goal]
**Goal:** What the session set out to accomplish
**Status:** Complete | In Progress | Blocked
**Commits:** short hash(es), or "none"
**Key changes:** bullet list of files or modules touched
**Summary:** 2-4 sentences on what was done and why
**Next:** What the next agent/session should do first
```

Write it with the Write or Edit tool directly to the sessions file.
The Stop hook writes the mechanical git state automatically; you supply the narrative.

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Architecture review → invoke plan-eng-review
