---
description: Reports current workflow state from a workspace including agent registry and mailbox status.
model: claude-opus
---

# Workflow State Detector

Given a workspace, report exactly where the workflow stands.

## Paths

```bash
WORKFLOW_HOME="$HOME/.claude/skills/workflow"
```

## Input

The user provides a planspace path at `~/.claude/workspaces/<task-slug>/`.

## Process

1. Run `bash "$WORKFLOW_HOME/scripts/workflow.sh" status <planspace>` for counts
2. Read `schedule.md` — list all steps with their markers
3. Read `state.md` — current accumulated context and facts
4. Read `log.md` — recent execution history (last 10 entries)
5. Check `artifacts/` for any work-in-progress files
6. Run `bash "$WORKFLOW_HOME/scripts/mailbox.sh" agents <planspace>` — registered agents and status
7. For each agent, run `bash "$WORKFLOW_HOME/scripts/mailbox.sh" check <planspace> <name>` — pending count

## Output

Report concisely:
- **Schedule**: N of M steps complete, current step name and status
- **Failures**: Any `[fail]` steps and what went wrong (from log)
- **Agents**: Registered agents, status (running/waiting), pending messages
- **State**: Key facts accumulated so far
- **Next action**: What should happen next (resume, retry, escalate, send message to unblock)
