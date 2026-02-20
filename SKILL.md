---
name: agent-implementation-skill
description: Multi-model agent implementation workflow for software development. Orchestrates research, evaluation, design baseline, implementation, RCA, auditing, constraint discovery, model selection, and tiered Stage 3 codemap construction (Tier 1 structural scan, Tier 2 concurrent Opus region exploration with GLM file reads, Tier 3 synthesis) across external AI models (GPT, GLM, Claude). Use when implementing features through a structured multi-phase pipeline with worktrees, dynamic scheduling, and file-based agent coordination.
---

# Development Workflow

Single entry point for the full development lifecycle. Read this file,
determine what phase you're in or what the user needs, then read the
relevant sub-file from this directory.

## Paths

Everything lives in this skill folder. WORKFLOW_HOME is: !`dirname "$(grep -rl '^name: agent-implementation-skill' ~/.claude/skills/*/SKILL.md .claude/skills/*/SKILL.md 2>/dev/null | head -1)" 2>/dev/null`

When dispatching scripts or agents, export `WORKFLOW_HOME` with the path
above. Scripts also self-locate via `dirname` as a fallback when invoked
directly.

```
$WORKFLOW_HOME/
  SKILL.md              # this file — entry point
  implement.md          # multi-model implementation pipeline
  research.md           # exploration → alignment → proposal
  rca.md                # root cause analysis
  evaluate.md           # proposal review
  baseline.md           # constraint extraction
  audit.md              # structured task decomposition
  constraints.md        # constraint discovery
  models.md             # model selection guide
  scripts/
    workflow.sh         # schedule driver ([wait]/[run]/[done]/[fail])
    mailbox.sh          # file-based message passing
    scan.sh             # Stage 3 coordinator: runs Tier 1 scan, invokes Tier 2-3 codemap build, then downstream per-section exploration
    codemap_build.py    # workflow-owned Tier 2-3 builder: dispatches uv run --frozen agents for Opus region exploration + GLM file characterization, then synthesizes codemap.md
    section-loop.py     # strategic section-loop orchestrator: integration proposals, strategic implementation, cross-section communication, global coordination (Stages 4-5 of implement.md)
  tools/
    extract-docstring-py  # extract Python module docstrings
    extract-summary-md    # extract YAML frontmatter from markdown
    README.md             # tool interface spec (for Opus to write new tools)
  agents/
    orchestrator.md     # event-driven step dispatcher (model: claude-opus)
    monitor.md          # task-level pipeline monitor — detects cycles/stuck (model: glm)
    agent-monitor.md    # per-agent loop detector — watches narration (model: glm)
    state-detector.md   # workspace state reporter (model: claude-opus)
    exception-handler.md # RCA on failed steps (model: claude-opus)
  templates/
    implement-proposal.md   # 10-step implementation schedule
    research-cycle.md       # 7-step research schedule
    rca-cycle.md            # 6-step RCA schedule
```

Workspaces live on native filesystem for performance, separate from project:
- **Planspace**: `~/.claude/workspaces/<task-slug>/` — schedule, state, log, artifacts, mailboxes
- **Codespace**: project root or worktree — where source code lives

Clean up planspace when workflow is fully complete (`rm -rf` the workspace dir).

## Phase Detection

Check these in order:

1. **User explicitly requested an action** → Read the matching file
2. **Test failures need investigation** → `rca.md`
3. **Proposal exists, not yet evaluated** → `evaluate.md`
4. **Proposal evaluated, no baseline** → `baseline.md`
5. **Baseline exists, implementation needed** → `implement.md`
6. **No proposal exists** → `research.md`
7. **Something feels wrong about a change** → `constraints.md`
8. **Need to pick a model** → `models.md`
9. **Need structured task decomposition** → `audit.md`

## Files

| File | What It Does |
|------|-------------|
| `research.md` | Exploration → alignment → proposal → refinement |
| `evaluate.md` | Proposal alignment review (Accept / Reject / Push Back) |
| `baseline.md` | Atomize proposal into constraints / patterns / tradeoffs |
| `implement.md` | Multi-model implementation with worktrees + dynamic scheduling |
| `rca.md` | Root cause analysis + architectural fix for test failures |
| `audit.md` | General structured task decomposition + delegation |
| `constraints.md` | Surface implicit constraints, validate design principles |
| `models.md` | Model selection guide for multi-model workflows |

## The Full Lifecycle

```
Exploration → Alignment → Proposal → Review → Baseline → Implementation → Verification
  (research.md)           (evaluate.md) (baseline.md) (implement.md)    (rca.md)
```

Phases iterate: Review may loop back to Research. Implementation may
trigger tangent research cycles. Verification may reveal architectural
issues requiring RCA.

## Artifact Flow

```
[Raw Idea]
    ↓
[Exploration Notes]              ← research.md Phase A
    ↓
[Alignment Document]             ← research.md Phase B
    ↓
[Proposal]                       ← research.md Phase C
    ↓
[Evaluation Report]              ← evaluate.md (iterate if REJECT/PUSH BACK)
    ↓
[Design Baseline]                ← baseline.md (constraints/, patterns/, TRADEOFFS.md)
    ↓
[Section Files → Integration Proposals → Strategic Implementation → Code]  ← implement.md
    ↓
[Tests → Debug → Audit → Lint → Commit]             ← implement.md + rca.md
```

## Workflow Orchestration

For multi-step workflows, use the orchestration system instead of running
everything from memory.

### Dispatch: All Agents via `uv run agents`

**CRITICAL**: All step dispatch goes through `uv run agents` via Bash.
Never use Claude's Task tool to spawn sub-agents — it causes "sibling"
errors and reliability issues. The agent runner automatically unsets
`CLAUDECODE` so sibling Claude sessions can launch.

```bash
# Sequential dispatch — model directly with prompt file
uv run agents --model <model> --file <planspace>/artifacts/step-N-prompt.md \
  > <planspace>/artifacts/step-N-output.md 2>&1

# Agent file dispatch — agent instructions prepended to prompt
uv run agents --agent-file "$WORKFLOW_HOME/agents/exception-handler.md" \
  --file <planspace>/artifacts/exception-prompt.md

# Parallel dispatch with mailbox coordination
(uv run agents --model gpt-5.3-codex-high --file <prompt-A.md> && \
  bash "$WORKFLOW_HOME/scripts/mailbox.sh" send <planspace> orchestrator "done:block-A") &
(uv run agents --model gpt-5.3-codex-high --file <prompt-B.md> && \
  bash "$WORKFLOW_HOME/scripts/mailbox.sh" send <planspace> orchestrator "done:block-B") &
bash "$WORKFLOW_HOME/scripts/mailbox.sh" recv <planspace> orchestrator
bash "$WORKFLOW_HOME/scripts/mailbox.sh" recv <planspace> orchestrator

# Tier 2 nested dispatch pattern (concurrent Opus regions, GLM file reads inside each region)
uv run agents --model claude-opus --project <codespace> \
  --file <planspace>/artifacts/codemap-region-<region>-prompt.md \
  > <planspace>/artifacts/codemap-region-<region>-output.md 2>&1
# Inside that region prompt/session: dispatch per-file characterization with GLM
uv run agents --model glm --project <codespace> \
  --file <planspace>/artifacts/codemap-file-<region>-<file>-prompt.md
```

### Schedule Templates

Pre-built schedules in `$WORKFLOW_HOME/templates/`. Each step specifies its model:
```
[wait] 1. step-name | model-name -- description (skill-section-reference)
```
- `implement-proposal.md` — full 10-step implementation pipeline
- `research-cycle.md` — research → evaluate → propose → refine
- `rca-cycle.md` — investigate → plan fix → apply → verify

### Stage 3 Codemap Orchestration

Stage 3 runs in this order:
1. Tier 1 structural scan completes.
2. Tier 2 dispatches one `claude-opus` region exploration agent per region (parallel where possible).
3. Inside each region flow, `glm` handles per-file characterization/file reads.
4. Tier 3 synthesizes region summaries into `<planspace>/artifacts/codemap.md`.
5. Per-section exploration begins only after codemap synthesis succeeds.

Control and recovery:
- If `codemap.md` already exists, codemap construction can be skipped.
- Region-level cache reuse is allowed when policy/config enables it.
- Region failures degrade gracefully: continue synthesis with successful regions.
- Hard-fail when synthesis cannot proceed (for example, no usable region summaries).
- Non-zero codemap construction exit stops Stage 3 before downstream section exploration.

### Model Roles

| Model | Used For |
|-------|----------|
| `claude-opus` | Section setup (excerpt extraction), alignment checks (shape/direction), decomposition, codemap region exploration, codemap synthesis |
| `gpt-5.3-codex-high` | Integration proposals, strategic implementation, coordinated fixes, extraction, investigation |
| `gpt-5.3-codex-high2` | Constraint audit (same capability, different quota) |
| `gpt-5.3-codex-xhigh` | Deep architectural synthesis, proposal drafting |
| `glm` | Test running, verification, quick commands, codemap per-file characterization/read support, semantic impact analysis, sub-agent exploration during integration proposals |

### Prompt Files

Step agents receive self-contained prompt files (they cannot read
`$WORKFLOW_HOME`). The orchestrator builds each prompt from:
1. **Skill section text** — copied verbatim from the referenced skill file
2. **Planspace path** — so the agent can read/write state and artifacts
3. **Codespace path** — so the agent knows where source code lives
4. **Context** — relevant content from `state.md`
5. **Output contract** — what the agent should return on success/failure

Written to: `<planspace>/artifacts/step-N-prompt.md`

### Workspace Structure

Each workflow gets a planspace at `~/.claude/workspaces/<task-slug>/`:
- `schedule.md` — task queue with status markers (copied from template)
- `state.md` — current position + accumulated facts
- `log.md` — append-only execution log
- `artifacts/` — prompt files, output files, working files for steps
  - `artifacts/sections/` — section excerpts (proposal + alignment excerpts)
  - `artifacts/proposals/` — integration proposals per section
  - `artifacts/snapshots/` — post-completion file snapshots per section
  - `artifacts/notes/` — cross-section consequence notes
  - `artifacts/coordination/` — global coordinator state and fix prompts
  - `artifacts/decisions/` — accumulated parent decisions per section (from pause/resume)
  - `artifacts/summary-stream.log` — append-only log of all lifecycle messages (monitor reads this)
- `constraints/` — discovered constraints (promote later)
- `tradeoffs/` — discovered tradeoffs (promote later)
- `mailboxes/<name>/` — per-agent message queues
- `.registry/<name>` — agent registry entries

### Mailbox System

File-based message passing for agent coordination.

```bash
# Send a message to an agent
bash "$WORKFLOW_HOME/scripts/mailbox.sh" send <planspace> <target> "message text"

# Block until a message arrives (agent sleeps, no busy-loop)
bash "$WORKFLOW_HOME/scripts/mailbox.sh" recv <planspace> <name> [timeout_seconds]

# Check pending count (non-blocking)
bash "$WORKFLOW_HOME/scripts/mailbox.sh" check <planspace> <name>

# Read all pending messages
bash "$WORKFLOW_HOME/scripts/mailbox.sh" drain <planspace> <name>

# Agent lifecycle
bash "$WORKFLOW_HOME/scripts/mailbox.sh" register <planspace> <name> [pid]
bash "$WORKFLOW_HOME/scripts/mailbox.sh" unregister <planspace> <name>
bash "$WORKFLOW_HOME/scripts/mailbox.sh" agents <planspace>
bash "$WORKFLOW_HOME/scripts/mailbox.sh" cleanup <planspace> [name]
```

**Key patterns**:
- Orchestrator blocks on `recv` waiting for parallel step results
- Step agents send `done:<step>:<summary>` or `fail:<step>:<error>` when finished
- Section-loop sends `summary:setup:`, `summary:proposal:`, `summary:proposal-align:`, `summary:impl:`, `summary:impl-align:`, `status:coordination:` messages; `complete` only on full success; `fail:<num>:coordination_exhausted:<summary>` on coordination timeout
- Mailbox is required for orchestrator/step coordination boundaries
- Codemap region concurrency may be implemented internally by the codemap tool (for example `codemap_build.py`), not only through mailbox-driven fan-out
- When mailbox-backed codemap region dispatch is used, terminal status contract is explicit: `done:<region>` on success or `fail:<region>:<error>` on failure, with exactly one terminal message per dispatched region
- Agents needing user input send `ask:<step>:<question>`, then block on their own mailbox
- User or orchestrator can send `abort` to any agent to trigger graceful shutdown
- `agents` command shows who's registered and who's waiting — detect stuck agents

## Cross-Cutting Tools

- **audit.md** — Structured decomposition + delegation for any large task
- **constraints.md** — Before implementation or when something feels wrong
- **models.md** — Which external model to use for any given task
