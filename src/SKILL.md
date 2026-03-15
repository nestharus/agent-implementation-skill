---
name: agent-implementation-skill
description: Multi-model agent implementation workflow for software development. Orchestrates research, evaluation, design baseline, implementation, RCA, structured decomposition, constraint discovery, model selection, and agent-driven Stage 3 codemap exploration across external AI models (GPT, GLM, Claude). Use when implementing features through a structured multi-phase pipeline with planspace/codespace separation, dynamic scheduling, and SQLite-backed agent coordination.
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
  audit.md              # concern-based problem decomposition
  constraints.md        # constraint discovery
  models.md             # model selection guide
  scripts/
    workflow.sh         # schedule state markers ([wait]/[run]/[done]/[fail]) — internal, not the entry point
    db.sh               # SQLite-backed coordination database
  tools/
    extract-docstring-py  # extract Python module docstrings
    extract-summary-md    # extract YAML frontmatter from markdown
    README.md             # tool interface spec (for Opus to write new tools)
  <system>/agents/      # agent definitions distributed across system modules (scan/, proposal/, implementation/, etc.)
  templates/
    implement-proposal.md   # 10-step implementation schedule
    research-cycle.md       # 7-step research schedule
    rca-cycle.md            # 6-step RCA schedule
```

Workspaces live on native filesystem for performance, separate from project:
- **Planspace**: `~/.claude/workspaces/<task-slug>/` — schedule, state, log, artifacts, coordination database
- **Codespace**: project root — where source code lives

Clean up planspace when workflow is fully complete (`rm -rf` the workspace dir).

## Your Role

**BEFORE DOING ANYTHING ELSE**: Determine your role in the pipeline,
then read the corresponding agent definition file. Agent definitions are
distributed under system-owned directories (e.g., `$WORKFLOW_HOME/<system>/agents/<name>.md`);
the task router resolves agent files by name. Do not proceed until you have read it.

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
9. **Need concern-based problem decomposition** → `audit.md`

## Files

| File | What It Does |
|------|-------------|
| `research.md` | Exploration → alignment → proposal → refinement |
| `evaluate.md` | Proposal alignment review (Accept / Reject / Push Back) |
| `baseline.md` | Atomize proposal into constraints / patterns / tradeoffs |
| `implement.md` | Multi-model implementation with planspace/codespace + dynamic scheduling |
| `rca.md` | Root cause analysis + architectural fix for test failures |
| `audit.md` | Concern-based problem decomposition + alignment tracing |
| `constraints.md` | Surface implicit constraints, validate design principles |
| `models.md` | Model selection guide for multi-model workflows |

## Design Philosophy

These principles govern all pipeline behavior. Violations are alignment
failures.

1. **Alignment over audit** — Check directional coherence between adjacent
   layers ("is it solving the right problem?"), never feature coverage
   against a checklist ("is it done?"). The system is never done.
2. **Strategy over brute force** — Strategy collapses many waves of problems
   in one go. Brute force leads to countless cycles. Fewer tokens, fewer
   cycles, same quality.
3. **Scripts dispatch, agents decide** — Scripts do mechanical coordination
   (dispatch, check, log). Agents do reasoning (explore, understand, decide).
   Strategic decisions (grouping, relatedness, signal interpretation) belong
   to agents, not scripts.
4. **Heuristic exploration, not exhaustive scanning** — Build a routing map
   (codemap), then use it for targeted investigation. Never catalog every
   file. The cost of occasionally routing wrong is far less than exhaustive
   scanning.
5. **Problems, not features** — We decompose problems all the way down, then
   solve tiny problems. Proposals describe strategies, not implementations.
   We never do feature coverage because we generate as we go.
6. **Proposals must solve the same problems** — Alternative proposals are
   valid only if they solve the original problems. An optimization or
   complexity argument is an excuse. Do not introduce constraints the user
   did not specify.
7. **Accuracy over shortcuts — zero tolerance for fabrication and bypasses** —
   We accept zero tolerance for invented understanding, bypassed
   safeguards, or pipeline shortcuts that skip required grounding.
   Agents must follow the full pipeline faithfully: explore before
   proposing, propose before implementing, align before proceeding.
   Operational execution still uses proportional guardrails: the ROAL
   loop scales effort to actual risk and keeps residual risk below the
   configured threshold rather than pretending all execution can be made
   literally risk-free. "This is simple enough to skip a step" is never
   valid reasoning. When in doubt, follow the pipeline.

### Terminology Contract

- **"Audit"** only ever means alignment against stated problems and
  constraints — never feature coverage against a checklist.
- **"Alignment"** is directional coherence between adjacent layers:
  does the work solve the problem it claims to solve?
- **"Feature coverage"** is explicitly banned as a verification method.
  Plans describe problems and strategies, not enumerable features.

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
[Tests → Debug → Constraint Check → Lint → Commit]   ← implement.md + rca.md
```

## Workflow Orchestration

For multi-step workflows, use the orchestration system instead of running
everything from memory.

### Running the Implementation Pipeline

All implementation work goes through the canonical pipeline runner:

    python -m pipeline <planspace> <codespace> --spec <spec-path> [--slug <slug>] [--qa-mode]

The runner owns stages 1-7 end-to-end: planspace initialization, governance bootstrap,
schedule rendering, section decomposition, codemap exploration, substrate discovery,
section-loop (propose -> align -> implement), verification, and promotion.

Do NOT dispatch agents directly via the `agents` binary or write prompts manually.
All dispatch must go through the pipeline runner, which ensures QA interception,
coordination DB tracking, and section-loop discipline.

Never use any sub-agent spawning or delegation mechanism outside this
repo's dispatch and task-submission system — external spawning
causes "sibling" errors and reliability issues.

### Schedule Templates

Pre-built schedules in `$WORKFLOW_HOME/templates/`. Each step specifies its model:
```
[wait] 1. step-name | model-name -- description (skill-section-reference)
```
- `implement-proposal.md` — full 10-step implementation pipeline
- `research-cycle.md` — external research → evaluate → propose → refine (human-facing)
- `rca-cycle.md` — investigate → plan fix → apply → verify

Note: In-runtime section research (`blocking_research_questions`) is handled
automatically through queued `research_plan` tasks within the section loop,
not through this external schedule template.

### Stage 3 Codemap Exploration

Stage 3 dispatches agents to explore and understand the codebase:
1. An Opus agent explores the codespace — reads files, follows its curiosity, builds understanding.
2. The agent writes `<planspace>/artifacts/codemap.md` capturing what it discovered.
3. Per-section Opus agents use the codemap to identify related files for each section.
4. Deep scan dispatches GLM agents to reason about specific file relevance in context.

Control and recovery:
- If `codemap.md` already exists, reuse it only if the codespace
  fingerprint is unchanged or the verifier confirms validity; otherwise
  rebuild.
- If a section already has `## Related Files`, validate the list against
  the current codemap/section content; skip only if unchanged.
- Non-zero codemap exit stops Stage 3 before section exploration.

### Model Roles

| Model | Used For |
|-------|----------|
| `claude-opus` | Section setup (excerpt extraction), alignment checks (shape/direction), decomposition, codemap exploration, per-section file identification |
| `gpt-high` | Integration proposals, strategic implementation, coordinated fixes, extraction, investigation, constraint alignment check |
| `gpt-xhigh` | Escalation-tier synthesis, deep cross-section convergence |
| `glm` | Test running, verification, quick commands, deep file analysis, semantic impact analysis |

### Prompt Files

Step agents receive self-contained prompt files (they cannot read
`$WORKFLOW_HOME`). The orchestrator builds each prompt from:
1. **Skill section text** — copied verbatim from the referenced skill file
2. **Planspace path** — so the agent can read/write state and artifacts
3. **Codespace path** — so the agent knows where source code lives
4. **Context** — relevant content from typed artifacts and `run.db`-backed context sidecars
5. **Output contract** — what the agent should return on success/failure

Written to: `<planspace>/artifacts/step-N-prompt.md`

### Workspace Structure

Each workflow gets a planspace at `~/.claude/workspaces/<task-slug>/`:
- `schedule.md` — task queue with status markers (copied from template)
- `artifacts/` — prompt files, typed JSON artifacts, context sidecars, output files, working files for steps
  - `artifacts/sections/` — section excerpts (proposal + alignment excerpts)
  - `artifacts/proposals/` — integration proposals per section
  - `artifacts/snapshots/` — post-completion file snapshots per section
  - `artifacts/notes/` — cross-section consequence notes
  - `artifacts/coordination/` — global coordinator state and fix prompts
  - `artifacts/decisions/` — accumulated parent decisions per section (from pause/resume)
  - `artifacts/parameters.json` — runtime parameters (e.g., `{"qa_mode": true}` to enable QA dispatch interception)
  - `artifacts/qa-intercepts/` — QA interceptor prompts, outputs, and rationale files (created when qa_mode is enabled)
- `run.db` — coordination database (messages, events, agent registry)
- `constraints/` — discovered constraints (promote later)
- `tradeoffs/` — discovered tradeoffs (promote later)

### Coordination System (db.sh)

SQLite-backed coordination for agent messaging. One `run.db` per pipeline
run — messages are claimed (not consumed), history is preserved, and the
database file is the complete audit trail.

```bash
# Initialize the coordination database (idempotent)
bash "$WORKFLOW_HOME/scripts/db.sh" init <planspace>/run.db

# Send a message to an agent
bash "$WORKFLOW_HOME/scripts/db.sh" send <planspace>/run.db <target> [--from <agent>] "message text"

# Block until a message arrives (agent sleeps, no busy-loop)
bash "$WORKFLOW_HOME/scripts/db.sh" recv <planspace>/run.db <name> [timeout_seconds]

# Check pending count (non-blocking)
bash "$WORKFLOW_HOME/scripts/db.sh" check <planspace>/run.db <name>

# Read all pending messages
bash "$WORKFLOW_HOME/scripts/db.sh" drain <planspace>/run.db <name>

# Agent lifecycle
bash "$WORKFLOW_HOME/scripts/db.sh" register <planspace>/run.db <name> [pid]
bash "$WORKFLOW_HOME/scripts/db.sh" unregister <planspace>/run.db <name>
bash "$WORKFLOW_HOME/scripts/db.sh" agents <planspace>/run.db
bash "$WORKFLOW_HOME/scripts/db.sh" cleanup <planspace>/run.db [name]

# Event logging and querying
bash "$WORKFLOW_HOME/scripts/db.sh" log <planspace>/run.db <kind> [tag] [body] [--agent <name>]
bash "$WORKFLOW_HOME/scripts/db.sh" tail <planspace>/run.db [kind] [--since <id>] [--limit <n>]
bash "$WORKFLOW_HOME/scripts/db.sh" query <planspace>/run.db <kind> [--tag <t>] [--agent <a>] [--since <id>] [--limit <n>]
```

**Key patterns**:
- Orchestrator blocks on `recv` waiting for parallel step results
- Step agents send `done:<step>:<summary>` or `fail:<step>:<error>` when finished
- Section-loop sends `summary:setup:`, `summary:proposal:`, `summary:proposal-align:`, `summary:impl:`, `summary:impl-align:`, `status:coordination:` messages; `complete` only on full success; `fail:<num>:coordination_exhausted:<summary>` on coordination timeout
- Mailbox is required for orchestrator/step coordination boundaries
- Codemap exploration is a single Opus agent that explores the codespace directly
- Agents needing user input send `ask:<step>:<question>`, then block on their own mailbox
- User or orchestrator can send `abort` to any agent to trigger graceful shutdown
- `agents` command shows who's registered and who's waiting — detect stuck agents

## Cross-Cutting Tools

- **audit.md** — Concern-based problem decomposition + alignment tracing
- **constraints.md** — Before implementation or when something feels wrong
- **models.md** — Which external model to use for any given task

## Internal Dispatch Reference (Script Use Only)

> **WARNING**: These are internal implementation details used by the pipeline runner
> and section dispatcher. The orchestrating session must NOT invoke `agents` directly.
> Direct invocation bypasses QA interception, coordination tracking, and pipeline discipline.

```bash
# Sequential dispatch — model directly with prompt file
agents --model <model> --file <planspace>/artifacts/step-N-prompt.md \
  > <planspace>/artifacts/step-N-output.md 2>&1

# Agent file dispatch — agent instructions prepended to prompt
agents --agent-file "$WORKFLOW_HOME/proposal/agents/alignment-judge.md" \
  --file <planspace>/artifacts/alignment-prompt.md

# Parallel dispatch with db.sh coordination
(agents --model gpt-high --file <prompt-A.md> && \
  bash "$WORKFLOW_HOME/scripts/db.sh" send <planspace>/run.db orchestrator "done:block-A") &
(agents --model gpt-high --file <prompt-B.md> && \
  bash "$WORKFLOW_HOME/scripts/db.sh" send <planspace>/run.db orchestrator "done:block-B") &
bash "$WORKFLOW_HOME/scripts/db.sh" recv <planspace>/run.db orchestrator
bash "$WORKFLOW_HOME/scripts/db.sh" recv <planspace>/run.db orchestrator

# Codemap exploration dispatch (Opus explores the codespace)
agents --model claude-opus --project <codespace> \
  --file <planspace>/artifacts/scan-logs/codemap-prompt.md \
  > <planspace>/artifacts/codemap.md 2>&1
```

**Note**: The examples above show **script-level** dispatch — the pipeline runner
launching step agents internally. **Nested strategic work** within step agents (e.g.,
exploration during integration proposals) uses **task submission**: agents write
structured task-request files, and the dispatcher resolves agent file + model.
See `implement.md` Stage 4-5 for task submission details.
