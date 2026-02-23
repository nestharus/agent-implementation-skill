---
description: Reports current workflow state from a workspace including agent registry and mailbox status.
model: claude-opus
---

# Workflow State Detector

Given a workspace, report exactly where the workflow stands.

## Paths

`$WORKFLOW_HOME` is the skill directory (containing SKILL.md). Set by the caller in your prompt or environment.

## Input

Your prompt includes:
- Planspace path

Set `PLANSPACE` from the planspace path provided in your prompt. Use
`$PLANSPACE` in all commands below. Do not invent or assume paths.

The planspace lives at `~/.claude/workspaces/<task-slug>/`.

<!-- ==========================================================================
TODO [sqlite-migration]: Replace 5 remaining file-based operations with DB queries

WHAT: 5 file-based operations remain after the db.sh mechanical swap:
1. workflow.sh status → db.sh query <db> schedule (aggregate counts)
2. schedule.md → db.sh query <db> schedule (list steps with status)
3. state.md → db.sh fetch <db> state latest
4. log.md → db.sh query <db> log --limit 10
5. artifacts/ listing → db.sh list <db> (by kind/section)

NOTE: schedule.md operations (#1-2) are Tier 2 — can defer until after
Tier 1 (mailbox + events). workflow.sh may be kept as-is initially.
========================================================================== -->

## Process

1. Run `bash "$WORKFLOW_HOME/scripts/workflow.sh" status $PLANSPACE` for counts
2. Read `schedule.md` — list all steps with their markers
3. Read `state.md` — current accumulated context and facts
4. Read `log.md` — recent execution history (last 10 entries)
5. Check `artifacts/` for any work-in-progress files
6. Run `bash "$WORKFLOW_HOME/scripts/db.sh" agents $PLANSPACE/run.db` — registered agents and status
7. For each agent, run `bash "$WORKFLOW_HOME/scripts/db.sh" check $PLANSPACE/run.db <name>` — pending count

## Output

Report concisely:
- **Schedule**: N of M steps complete, current step name and status
- **Failures**: Any `[fail]` steps and what went wrong (from log)
- **Agents**: Registered agents, status (running/waiting), pending messages
- **State**: Key facts accumulated so far
- **Next action**: What should happen next (resume, retry, escalate, send message to unblock)
