---
name: agent-implementation-skill
description: Multi-model agent implementation workflow for software development. Orchestrates research, evaluation, design baseline, implementation, RCA, structured decomposition, constraint discovery, model selection, and agent-driven Stage 3 codemap exploration across external AI models (GPT, GLM, Claude). Use when implementing features through a structured multi-phase pipeline with worktrees, dynamic scheduling, and SQLite-backed agent coordination.
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
    db.sh               # SQLite-backed coordination database
    scan.sh             # Stage 3 coordinator: dispatches agents to explore codespace and build codemap, then per-section file identification
    substrate.sh        # Stage 3.5 shim: sets PYTHONPATH, runs python -m substrate
    substrate/          # Stage 3.5: shared integration substrate discovery (shards → prune → seed) for greenfield/vacuum sections
    section-loop.py     # strategic section-loop orchestrator: integration proposals, strategic implementation, cross-section communication, global coordination (Stages 4-5 of implement.md)
  tools/
    extract-docstring-py  # extract Python module docstrings
    extract-summary-md    # extract YAML frontmatter from markdown
    README.md             # tool interface spec (for Opus to write new tools)
  agents/              # agent role definitions
    agent-monitor.md            # per-agent monitor: watches narration mailbox for loops/repetition
    alignment-judge.md          # checks alignment between layers
    bridge-agent.md             # resolves cross-section interface friction
    bridge-tools.md             # tool-aware bridge resolution
    coordination-planner.md     # groups related cross-section problems
    exception-handler.md        # RCA on failed steps
    implementation-strategist.md # strategic multi-file implementation
    integration-proposer.md     # writes integration proposals
    intent-judge.md             # problem + philosophy alignment checking
    intent-pack-generator.md    # generates intent packs for sections
    intent-triager.md           # triages intent signals
    microstrategy-writer.md     # tactical per-file breakdowns
    monitor.md                  # pipeline cycle/stuck detection
    orchestrator.md             # event-driven workflow dispatch
    philosophy-distiller.md     # distills design philosophy
    philosophy-expander.md      # validates + integrates philosophy surfaces, gates tensions
    philosophy-source-selector.md # selects philosophy source files from mechanical catalog
    problem-expander.md         # validates problem surfaces, integrates into section problem definition
    qa-monitor.md               # deep QA with PAUSE authority
    section-re-explorer.md      # re-explores sections with no related files
    setup-excerpter.md          # extracts section-level excerpts from global proposal/alignment
    state-adjudicator.md        # lightweight classifier for ambiguous agent output states
    state-detector.md           # workspace state reporting
    substrate-shard-explorer.md  # produces needs/provides/shared-seam JSON shards per section
    substrate-pruner.md          # merges shards, prunes contradictions, produces substrate + seed plan
    substrate-seeder.md          # creates minimal anchor files from seed plan, wires substrate refs
    tool-registrar.md           # manages tool lifecycle: validates, catalogs, makes tools available
  templates/
    implement-proposal.md   # 10-step implementation schedule
    research-cycle.md       # 7-step research schedule
    rca-cycle.md            # 6-step RCA schedule
```

Workspaces live on native filesystem for performance, separate from project:
- **Planspace**: `~/.claude/workspaces/<task-slug>/` — schedule, state, log, artifacts, coordination database
- **Codespace**: project root or worktree — where source code lives

Clean up planspace when workflow is fully complete (`rm -rf` the workspace dir).

## Your Role

**BEFORE DOING ANYTHING ELSE**: Determine your role in the pipeline,
then read the corresponding file from `$WORKFLOW_HOME/agents/`. That
file defines your rules. Do not proceed until you have read it.

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
7. **Accuracy over shortcuts — zero risk tolerance** — Every shortcut or
   bypass of the pipeline introduces risk. We do not accept any risk.
   Agents must follow the full pipeline faithfully: explore before
   proposing, propose before implementing, align before proceeding.
   Shortcuts are permitted ONLY when the remaining work is so small that
   no meaningful risk exists (e.g., a single trivial cleanup after
   everything else is aligned and verified). "This is simple enough to
   skip a step" is never valid reasoning — simplicity is not the same as
   zero risk. When in doubt, follow the pipeline.

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

### Dispatch: All Agents via `agents`

**CRITICAL**: All step dispatch goes through `agents` via Bash.
Never use Claude's Task tool to spawn sub-agents — it causes "sibling"
errors and reliability issues. The agent runner automatically unsets
`CLAUDECODE` so sibling Claude sessions can launch.

```bash
# Sequential dispatch — model directly with prompt file
agents --model <model> --file <planspace>/artifacts/step-N-prompt.md \
  > <planspace>/artifacts/step-N-output.md 2>&1

# Agent file dispatch — agent instructions prepended to prompt
agents --agent-file "$WORKFLOW_HOME/agents/exception-handler.md" \
  --file <planspace>/artifacts/exception-prompt.md

# Parallel dispatch with db.sh coordination
(agents --model gpt-codex-high --file <prompt-A.md> && \
  bash "$WORKFLOW_HOME/scripts/db.sh" send <planspace>/run.db orchestrator "done:block-A") &
(agents --model gpt-codex-high --file <prompt-B.md> && \
  bash "$WORKFLOW_HOME/scripts/db.sh" send <planspace>/run.db orchestrator "done:block-B") &
bash "$WORKFLOW_HOME/scripts/db.sh" recv <planspace>/run.db orchestrator
bash "$WORKFLOW_HOME/scripts/db.sh" recv <planspace>/run.db orchestrator

# Codemap exploration dispatch (Opus explores the codespace)
agents --model claude-opus --project <codespace> \
  --file <planspace>/artifacts/scan-logs/codemap-prompt.md \
  > <planspace>/artifacts/codemap.md 2>&1
```

### Schedule Templates

Pre-built schedules in `$WORKFLOW_HOME/templates/`. Each step specifies its model:
```
[wait] 1. step-name | model-name -- description (skill-section-reference)
```
- `implement-proposal.md` — full 10-step implementation pipeline
- `research-cycle.md` — research → evaluate → propose → refine
- `rca-cycle.md` — investigate → plan fix → apply → verify

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
| `gpt-codex-high` | Integration proposals, strategic implementation, coordinated fixes, extraction, investigation, constraint alignment check |
| `gpt-codex-xhigh` | Deep architectural synthesis, proposal drafting |
| `glm` | Test running, verification, quick commands, deep file analysis, semantic impact analysis, sub-agent exploration during integration proposals |

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

- **audit.md** — Structured decomposition + delegation for any large task
- **constraints.md** — Before implementation or when something feels wrong
- **models.md** — Which external model to use for any given task
