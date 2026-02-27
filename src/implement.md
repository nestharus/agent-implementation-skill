# Implement Proposal: Multi-Model Execution Pipeline

### Terminology Contract

"Verification" in this pipeline means **alignment checking** — confirming
that each layer's output solves the problem its input layer describes. It
NEVER means feature-coverage auditing against a checklist. Alignment checks
shape and direction (is this solving the right problem?), not completeness
(did it do everything?). Plans describe problems and strategies, not
enumerable features.

Stage 3 dispatches agents to explore and understand the codebase.
Agents reason about what they find — the script only coordinates dispatch,
checks outputs, and logs failures.

**Terminology enforcement**: `scripts/lint-audit-language.sh` prevents
drift away from alignment terminology. If feature-coverage checklist framing
appears, the lint rejects it. The alignment-judge agent also rejects invalid
frames (`frame_ok=false`) — see `agents/alignment-judge.md`.

## Workflow Orchestration

This skill is designed to be executed via the workflow orchestration system.
Each stage below corresponds to a schedule step in the `implement-proposal.md`
template. The orchestrator pops steps and dispatches agents to the matching
stage section. Stage 3 dispatches agents to explore the codebase and build
a codemap, then per-section agents identify related files, preserving the
public `scan.sh quick|deep|both` interface and `## Related Files` output
format.

Schedule step → Skill section mapping:
- `decompose` → Stage 1: Section Decomposition
- `docstrings` → Stage 2: Docstring Infrastructure
- `scan` → Stage 3: File Relevance Scan
- `section-loop` → Stages 4–5: Integration Proposals + Strategic Implementation + Global Coordination
- `verify` → Stage 6: Verification
- `post-verify` → Stage 7: Post-Task Verification

## Orchestrator Role

**CRITICAL**: The orchestrator (you) coordinates the pipeline. You do NOT
edit source files directly.

- **Read** proposals, source files, agent outputs, test results
- **Write** prompt files for agents (in `<planspace>/artifacts/`)
- **Delegate** all source file editing to agents
- **Manage** section queue, dynamic scheduling
- **Verify** agent outputs match expectations

**You NEVER**: edit `.py` files, place markers yourself, fix code yourself.

**Strategic agents handle multiple files holistically.** When dispatching
implementation agents (GPT/Codex), they tackle coordinated changes across
files. This is intentional — strategic implementation requires understanding
the shape of changes, not file-by-file mechanical edits. The orchestrator
dispatches the agent once per section; the agent handles file scope.

## Prompt Construction Rules

**CRITICAL**: Prompts reference filepaths — agents read files themselves.

Agents dispatched via `agents` have full filesystem access. Prompts
must NOT embed file contents inline. Instead, list filepaths and instruct
the agent to read them.

**Embed only**: summaries (1-3 line section summaries from YAML frontmatter),
alignment feedback (short diagnostic text), and task instructions.

**Reference by filepath**: section files, integration proposals, source files,
alignment excerpts, consequence notes — anything with substantial content.

This keeps prompts small (under 1KB typically) and avoids "prompt too long"
errors that occur when embedding large source files or SQL dumps.

Prompt template pattern:

    # Task: <description>

    ## Summary
    <embedded 1-2 line summary from YAML frontmatter>

    ## Files to Read
    1. <label>: `<absolute filepath>`
    2. <label>: `<absolute filepath>`

    ## Instructions
    Read all files listed above, then <task description>.
    Write output to: `<absolute filepath>`

## Parallel Dispatch Pattern

All parallel agent dispatch uses Bash with fire-and-forget `&` plus
db.sh coordination. **Never** use MCP background-job tools or Bash
`wait` for parallel agents — both block the orchestrator.

**Key rule**: Always have exactly ONE `recv` running as a background
task. It waits for the next message. When it completes, process the
result and immediately start another `recv` if more messages are
expected. This ensures you are always listening.

```bash
DB="$WORKFLOW_HOME/scripts/db.sh"

# 0. Initialize coordination database (idempotent)
bash "$DB" init <planspace>/run.db

# 1. Register orchestrator
bash "$DB" register <planspace>/run.db orchestrator

# 2. Start recv FIRST — always be listening before dispatching
bash "$DB" recv <planspace>/run.db orchestrator 600  # run_in_background: true

# 3. Fire-and-forget: each agent sends message on completion
(agents --model <model> --file <prompt.md> \
  > <planspace>/artifacts/<output.md> 2>&1 && \
  bash "$DB" send <planspace>/run.db orchestrator "done:<tag>") &

# 4. When recv notifies you of completion:
#    - Process the result
#    - Start another recv if more messages expected:
bash "$DB" recv <planspace>/run.db orchestrator 600  # run_in_background: true

# 5. Clean up when ALL messages received (no more agents outstanding)
bash "$DB" cleanup <planspace>/run.db orchestrator
bash "$DB" unregister <planspace>/run.db orchestrator
```

The recv → process → recv loop continues until all agents have reported.
Only clean up the mailbox when no more messages are expected.

## Pipeline Overview

**Core invariant: every layer repeats the same pattern.** Explore → find
problems → propose solution → align with parent layer. This applies
recursively from global proposal down to TODO blocks. Microstrategy/TODO
extraction is not optional convenience — it is the lowest-layer instance
of the same explore-propose-align pattern.

**Zero risk tolerance: no stage is optional.** Every stage exists because
skipping it introduces risk. Agents MUST NOT bypass stages, combine
stages, or rationalize that the project is "simple enough" to skip
exploration, proposal, or alignment steps. Shortcuts are permitted ONLY
when the remaining work is so trivially small that no meaningful risk
exists. This applies equally to greenfield and brownfield projects.

1. **Section Decomposition** — Recursive decomposition into atomic section files
2. **Demand-Driven Docstring Cache** — Ensure relevant source files have module docstrings
3. **File Relevance Scan** — Quick mode dispatches an Opus agent to explore the codespace and build a codemap (which must include a Routing Table section consumed by downstream agents for file selection), then per-section Opus agents identify related files; deep mode dispatches GLM agents to reason about specific file relevance (preserving `## Related Files`)

--- Per-section loop (strategic, agent-driven) ---

4. **Section Setup + Integration Proposal** — Extract proposal/alignment excerpts from
   global documents, then GPT writes integration proposal (how to wire proposal into
   codebase), Opus checks alignment on shape/direction, iterate until aligned
5. **Strategic Implementation + Global Coordination** — GPT implements holistically with
   sub-agents (GLM for exploration, Codex for targeted areas), Opus checks alignment
→ After all sections: cross-section alignment re-check, global coordinator collects
  problems, groups related ones, dispatches coordinated fixes, re-verifies per-section

--- End per-section loop (all sections aligned = done) ---

6. **Verification** — Constraint alignment check + lint + tests
7. **Post-Task Verification** — Full suite + commit

Enter at any stage if prior stages are already complete.

## Worktree Model

Each **task** (proposal, feature, etc.) gets one worktree. All stages
within that task run sequentially in the same worktree. There are no
per-block worktrees.

```
task worktree (one per task)
  ├── Stage 1: writes to planspace only
  ├── Stage 2: updates docstrings in source files
  ├── Stage 3: writes to planspace only (relevance map)
  ├── Stages 4-5: agents implement strategically per section + global coordination
  ├── Stage 6: verify in-place
  └── Stage 7: final verification + commit
```

**Cross-task parallelism**: Multiple tasks can run simultaneously in
separate worktrees. Each task is fully independent.

**Within-task sequencing**: Default behavior is sequential execution
within a task, with explicit per-stage concurrency exceptions documented in
the Stage Concurrency Model. Each agent sees the accumulated state
required by its stage contract.

## Stage Concurrency Model

| Stage | Concurrency |
|-------|-------------|
| 1: Decomposition | **Parallel** — writes to planspace only |
| 2: Docstrings | **Sequential** — one GLM per target file, edits source |
| 3: Scan | **Shell script** — quick: Opus agent explores codespace and builds codemap, then per-section Opus agents identify related files using the codemap; deep: GLM agents reason about specific file relevance in context |
| 4–5: Section Loop | **Sequential** — one section at a time, strategic agent-driven implementation with sub-agent dispatch; global coordination after initial pass |
| 6: Verification | **Sequential** — lint, test, fix cycles |
| 7: Post-Verify | **Single run** — full suite + commit |

## Extraction Tools

Language-specific extraction helpers live in `$WORKFLOW_HOME/tools/`.
Named `extract-docstring-<ext>`.

These tools are used where targeted extraction is needed for specific
file types.

```bash
TOOLS="$WORKFLOW_HOME/tools"

# Single file
python3 "$TOOLS/extract-docstring-py" <file>

# All Python files (batch via stdin)
find <codespace> -name "*.py" | python3 "$TOOLS/extract-docstring-py" --stdin
```

If targeted verification needs an extension with no extraction tool,
dispatch an Opus agent to write one following the interface in
`$WORKFLOW_HOME/tools/README.md`.

## Stage 1: Section Decomposition (Recursive)

The proposal ecosystem includes the proposal itself plus all supplemental
materials: evaluation reports, research findings, resolutions, design
baselines, execution plans, inventories, etc. These materials have their
own sections and sub-sections.

Decomposition has two phases: **identify** (recursive manifests) then
**materialize** (write terminal section files).

### Phase A: Recursive Identification

Each pass identifies sections and classifies them as **atomic** or
**needs further decomposition**. No section files are written — only
manifests.

Complexity signals that warrant further decomposition:
- Multiple distinct concerns that don't naturally belong together
- Section spans many planning documents with different guidance
- A downstream agent would need to juggle too many details at once

#### Pass 1: Initial Decomposition

One agent per proposal. Reads the proposal.

Outputs `<planspace>/artifacts/sections/pass-1-manifest.md`:
- Coarse sections identified
- For each section: **atomic** or **needs further decomposition**

#### Pass N: Recursive Refinement

One agent per compound section from the previous pass. Reads the
compound section from the proposal.

Outputs a sub-manifest at
`<planspace>/artifacts/sections/pass-N-section-SS-manifest.md`:
- Sub-sections identified within the compound section
- For each sub-section: **atomic** or **needs further decomposition**

#### Termination

Repeat until no compound sections remain. The orchestrator tracks which
sections are terminal vs compound after each pass.

### Phase B: Materialize Terminal Section Files

Once all sections are atomic, write terminal section files. One agent
per atomic section, dispatched in parallel using the mailbox pattern
(see "Parallel Dispatch Pattern" above).

Each agent writes `<planspace>/artifacts/sections/section-NN.md`
containing:
- Section text **pasted verbatim** from source material
- Enough information for a downstream agent to understand this section
  without reading any other material

A good terminal section is one a downstream agent can fully understand
and act on without being overwhelmed. It has a clear, focused scope.

Number terminal sections sequentially as they are produced.

**All context comes from planning documents** (proposals, evaluations,
research findings, design baselines, resolutions, etc.). Codebase
research happens in Stage 2 — Stage 1 never reads source code.

Verbatim copies guarantee decomposition accuracy — no verification needed.

### Phase C: Section Summaries (GLM per section)

After all section files are written, each needs a YAML frontmatter
summary block for cheap extraction. One GLM per section file.

GLM reads the section file and prepends:

```yaml
---
summary: <1-2 sentence summary of what this section specifies>
keywords: <comma-separated key concepts>
---
```

The summary captures the core point — what a downstream agent needs
to decide if a file might relate to this section. Keywords aid
quick matching.

Extract summaries in batch (exclude excerpt/surface artifacts):
```bash
find <planspace>/artifacts/sections -maxdepth 1 -type f -name "section-*.md" \
  | grep -E '/section-[0-9]+\.md$' \
  | python3 "$TOOLS/extract-summary-md" --stdin
```

## Stage 2: Demand-Driven Docstring Cache (GLM)

**On-demand** — only processes files that will actually be used by
later stages, not the entire repository.

Module-level docstrings serve as file summaries. They are standard
practice, live in source control, and are cheaply extractable. They
enable Stage 3 to scan relevance without reading full files.

### 2a: Determine Target Files

Only generate docstrings for files that appear in at least one of:
- **Codemap hotspots** — files highlighted by Stage 3 codemap exploration
  (if codemap already exists from a previous run)
- **Per-section related files** — files listed under `## Related Files`
  in section specifications (if sections already have related files)
- **Coordinator shared files** — files flagged by the global coordinator
  as modified by multiple sections

If none of these sources are available yet (first run, no codemap), use
adaptive discovery instead of hardcoded language-specific enumeration:

1. Read repo root docs (`README`, `docs/`, package manifests like
   `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, etc.)
2. Sample only **top-level** directories + a small number of
   representative files per directory (not an exhaustive listing)
3. Let the codemap agent nominate hotspot files without docstrings

This keeps first-run cost bounded and avoids assuming a specific language.
If a language-specific extractor is needed, let the tool-registrar agent
create one on demand (see `tools/README.md` for the interface spec).

### 2b: Extract Existing Docstrings

```bash
python3 "$TOOLS/extract-docstring-py" --stdin < file-list.txt
```

Files with `NO DOCSTRING` need one. Files with existing docstrings may
need updates if stale (check `git diff` since last docstring update).

### 2c: Generate Missing Docstrings (GLM per file)

For each file missing a docstring, dispatch GLM:
1. Reads the full file
2. Writes a module-level docstring summarizing:
   - What the module does (purpose)
   - Key classes/functions and their roles
   - How it relates to neighboring modules
3. Inserts the docstring at the top of the file

GLM only adds/updates the docstring — no other changes.

### 2d: Staleness Detection (incremental)

If docstrings already exist from a previous run:
1. `git diff --name-only <last-docstring-commit>` → changed files
2. For each changed file: GLM re-reads and updates the docstring
3. Unchanged files keep their existing docstrings

### 2e: Caching

Store per-file summaries keyed by content hash in
`<planspace>/artifacts/docstring-cache.json`. On subsequent runs, skip
files whose hash has not changed. This makes docstring generation
proportional to work actually done.

## Stage 3: File Relevance Scan

**Shell-script driven** — dispatches agents, checks outputs, logs failures.

```bash
bash "$WORKFLOW_HOME/scripts/scan.sh" both <planspace> <codespace>
# or separately:
bash "$WORKFLOW_HOME/scripts/scan.sh" quick <planspace> <codespace>
bash "$WORKFLOW_HOME/scripts/scan.sh" deep  <planspace> <codespace>
```

Stage placement is unchanged: this runs after Stage 2 and before the
section-loop (Stages 4-5). The public CLI remains unchanged.

### CLI contract (public interface)

- `bash "$WORKFLOW_HOME/scripts/scan.sh" quick <planspace> <codespace>`
- `bash "$WORKFLOW_HOME/scripts/scan.sh" deep <planspace> <codespace>`
- `bash "$WORKFLOW_HOME/scripts/scan.sh" both <planspace> <codespace>`

### Parameter types

- `<mode>`: enum `{quick, deep, both}`
- `<planspace>`: path to directory containing `artifacts/sections/section-*.md`
- `<codespace>`: path to target repository root

### Input contract

- Required:
  - `<planspace>/artifacts/sections/section-*.md`
  - `<codespace>/`
- Optional:
  - `<planspace>/proposal.md`
  - Alignment/evaluation/research docs used to improve exploration quality

### Output contract

- Canonical output:
  - Append/update `## Related Files` blocks in each section file with `### <filepath>` entries
- Intermediate artifacts:
  - `<planspace>/artifacts/codemap.md`
  - Per-section exploration logs in `<planspace>/artifacts/scan-logs/`

### Quick mode control flow

1. Dispatch an Opus agent to explore the codespace. The agent reads files,
   follows its curiosity, and writes `codemap.md` capturing its understanding
   of the codebase (what it is, how it's organized, key files, relationships).
2. For each section, dispatch an Opus agent with the codemap + section content.
   The agent reasons about which files are relevant to this section's goals
   and writes `## Related Files` entries into the section file.
3. If a section already has `## Related Files`, validate the list against
   the current codemap and section content; apply updates if stale, skip
   only if unchanged.
4. Section exploration failures are isolated — if one section agent fails,
   others continue.

The codemap format is not prescribed — it should reflect what the agent
discovered. The agent decides what's important, not a template.

### Deep mode control flow

1. Run after quick exploration. Process only confirmed matches already
   listed under `## Related Files`.
2. For each section, rank related files into tiers (tier-1 core, tier-2
   supporting, tier-3 peripheral) via a single GLM pass. Deep scan only
   tier-1 files by default. Full analysis stored in `file-cards/`; section
   file gets summary only.
3. Dispatch a GLM agent with the full section content + full file content.
   The agent reasons about what specific parts of the file matter for this
   section — functions, classes, configurations, risks, dependencies.
4. No fixed output format — the agent writes what it discovers naturally.
5. Skip missing/invalid file paths with diagnostics. On per-pair failures,
   record diagnostics and continue remaining pairs.

### Deep scan feedback authority

Deep scan feedback is authoritative **only via structured JSON fields** in
the feedback file (`relevant`, `missing_files`, `out_of_scope`,
`summary_lines`). The agent's prose response is archived in file-cards for
context but is never parsed or interpreted by the script. Section file
annotations come exclusively from the `summary_lines` field — if absent,
no annotation is written (fail-closed).

### Both mode control flow

Run `quick` then `deep` in sequence.

### Related-files accumulation format

For each confirmed match, the section file contains:

```markdown
## Related Files

### path/to/file.py
<reason this file is relevant>
```

The section file becomes the single source of truth — it contains the
verbatim proposal text, the YAML summary, and related file matches.
A file can appear in multiple section files.

### Resume support

- Full resume: if `codemap.md` already exists, reuse it only if the
  codespace fingerprint is unchanged or the verifier says reuse; otherwise
  rebuild.
- Section resume: if a section already has `## Related Files`, validate
  the list against current codemap/section; skip only if unchanged.
- Deep resume: if a file entry already has deep analysis, skip it.
- Diagnostics retention: failures are logged to `scan-logs/failures.log`.

### Error handling

- Unknown mode or missing path args: exit non-zero with usage.
- Missing section files or inaccessible codespace: exit non-zero with
  explicit diagnostic.
- If codemap agent fails or produces empty output: stop Stage 3.
- If a section exploration agent fails: log failure, continue others.
- Deep scan runs only on confirmed matches.
- No fixed output format enforcement — agents reason freely.

## Section-at-a-Time Execution

### Scripts and templates

| File | Purpose |
|------|---------|
| `$WORKFLOW_HOME/scripts/section-loop.py` | Strategic section-loop orchestrator (integration proposals, implementation, cross-section communication, global coordination) |
| `$WORKFLOW_HOME/scripts/task-agent-prompt.md` | Task agent prompt template |
| `$WORKFLOW_HOME/scripts/db.sh` | SQLite-backed coordination database |

### Launching task agents

The UI orchestrator:
1. Copies the task-agent prompt template
2. Fills in `{{PLANSPACE}}`, `{{CODESPACE}}`, `{{TAG}}`, etc.
3. Writes the filled prompt to `<planspace>/artifacts/task-agent-prompt.md`
4. Launches via: `agents --model claude-opus --file <planspace>/artifacts/task-agent-prompt.md`
5. Runs `recv` on its own mailbox to receive reports from the task agent

The task agent then owns the section-loop lifecycle:

```bash
python3 "$WORKFLOW_HOME/scripts/section-loop.py" <planspace> <codespace> \
  --global-proposal <proposal-path> --global-alignment <alignment-path> \
  --parent <agent-name>
```

The script runs as a **background task** under a **task agent**. The task
agent is launched via `agents` and is responsible for:
- Starting the section-loop script as a background subprocess
- Monitoring status mail from the script via mailbox recv
- Detecting stuck states (repeated alignment problems, stalled progress, crashes)
- Reporting progress and problems to the UI orchestrator
- Fixing issues autonomously when possible

The UI orchestrator does NOT directly launch or monitor section-loop
scripts. It spawns task agents and receives their reports.

### Communication model (3 layers, all db.sh)

```
UI Orchestrator (talks to user, high-level decisions)
  ├─ recv on orchestrator queue (listens for task-agent reports)
  └─ Task Agent (one per task, via agents)
       ├─ launches section-loop + monitor
       ├─ recv on task-agent queue (section-loop messages + escalations)
       ├─ send to orchestrator queue (reports progress + problems)
       ├─ Task Monitor (GLM, section-level pattern matcher)
       │    ├─ db.sh tail summary --since <cursor> (cursor-based event query)
       │    ├─ db.sh log lifecycle pipeline-state "paused" (pause/resume)
       │    └─ send to task-agent queue (escalations)
       ├─ QA Monitor (Opus, deep QA detection)
       │    ├─ db.sh tail summary + signal (cursor-based event queries)
       │    ├─ reads artifact files for content analysis
       │    ├─ db.sh log qa-finding (structured findings)
       │    ├─ db.sh log lifecycle pipeline-state "paused" (PAUSE authority)
       │    └─ send to task-agent queue (qa:warning, qa:paused, qa:abort-recommended)
       └─ section-loop.py (background subprocess)
            ├─ db.sh send (messages) + db.sh log summary (events)
            ├─ db.sh query lifecycle --tag pipeline-state (pause check)
            ├─ recv on section-loop queue (when paused by signals)
            └─ per agent dispatch:
                 ├─ agent (agents, sends narration via db.sh)
                 └─ Agent Monitor (GLM, per-dispatch loop detector)
                      ├─ reads agent's narration queue via db.sh
                      └─ db.sh log signal (NOT message send)
```

All coordination goes through `db.sh` and a single `run.db` per pipeline
run. No team/SendMessage infrastructure — agents are standalone processes
launched via `agents`, not Claude teammates. Every coordination
operation (send, recv, log) is automatically recorded in the database.
Messages are claimed, not consumed — the database file is the complete
audit trail.

**UI Orchestrator**: Launches task agents via `agents --file`,
runs `db.sh recv` on its own queue, receives reports, makes decisions,
communicates with user. Does NOT directly launch or monitor section-loop
scripts.

**Task Agent**: Intelligent overseer launched via `agents`. Launches
the section-loop script and monitor agent. Has full filesystem access to
investigate issues when the monitor escalates. Reads logs, diagnoses root
causes, fixes what it can autonomously, and escalates to the orchestrator
what it can't.

**Task Monitor** (GLM): Section-level pattern matcher. Queries summary
events from the coordination database via
`db.sh tail <planspace>/run.db summary --since <cursor>`, using cursor-based
pagination (tracks last-seen event ID). Tracks counters (alignment
attempts, coordination rounds), detects stuck states and cycles. Can pause
the pipeline by logging a lifecycle event:
`db.sh log <planspace>/run.db lifecycle pipeline-state "paused" --agent monitor`.
Escalates to task agent with diagnosis. Does NOT read files, fix issues,
or make judgment calls beyond pattern detection. Does NOT use `recv` for
summary data — queries events instead, avoiding message consumption
conflicts with the task agent.

**QA Monitor** (Opus): Deep QA detection agent with 26 rules across 5
categories: cycle detection (A1-A6), workflow compliance (B1-B6), strategic
behavior (C1-C6), bug detection (D1-D4), and big-picture friction (E1-E4).
Runs alongside section-loop for the duration of the pipeline. Unlike the
lightweight task monitor, the QA monitor actively reads artifact files,
compares outputs for similarity, and performs content analysis. Uses
graduated escalation levels: LOG (record only), WARN (notify task agent),
PAUSE (pause pipeline + notify), ABORT-RECOMMEND (recommend abort, does
not abort autonomously). Writes findings to
`<planspace>/artifacts/qa-report.md` and logs them as `qa-finding` events
in the database. Has authority to PAUSE the pipeline — if both the task
monitor and QA monitor detect the same issue, the QA monitor's PAUSE takes
priority over the task monitor's escalation.

**Agent Monitor** (GLM): Per-dispatch loop detector. Launched by
section-loop alongside each agent dispatch. Reads the agent's narration
messages via `db.sh drain` on the agent's named queue, tracks `plan:`
messages, detects repetition patterns indicating the agent has entered an
infinite loop (typically from context compaction). Reports `LOOP_DETECTED`
by logging a signal event:
`db.sh log <planspace>/run.db signal <agent-name> "LOOP_DETECTED:..." --agent <monitor-name>`.
One monitor per agent dispatch, exits when agent finishes.

**Section-loop script**: Strategic orchestrator. Runs sections sequentially
through the integration proposal + implementation flow, dispatches agents,
manages cross-section communication (snapshots, impact analysis, consequence
notes), and runs the global coordination phase after the initial pass.
Sends messages to the task agent via `db.sh send` and logs summary events
via `db.sh log summary` for each lifecycle transition. Queries pipeline
state before each agent dispatch via
`db.sh query <planspace>/run.db lifecycle --tag pipeline-state --limit 1`
— if paused, waits until resumed. For each per-section Codex/GPT agent
dispatch (setup, proposal, implementation), registers a narration queue
and launches a per-agent GLM monitor alongside the agent. Two categories
of dispatch are exempt from per-agent monitoring: (1) Opus alignment
checks — alignment prompts do not include narration instructions, and a
monitor would false-positive STALLED after 5 minutes of expected silence;
(2) Coordinator fix agents — fix prompts use strategic GLM sub-agents
internally for verification, and the task-level monitor detects
cross-section stuck states at the coordination round level. Cleans up
agent registrations via `db.sh cleanup` after each per-section dispatch.

**Pipeline state** (lifecycle events in `run.db`): Controls the pipeline.
The latest `lifecycle` event with `tag='pipeline-state'` determines current
state (`running` or `paused`). The task monitor logs a `paused` event to
pause; the task agent logs a `running` event to resume after investigating.
State changes are append-only — the full history of pause/resume transitions
is preserved in the database.

**Summary events** (events table in `run.db`): All summary, status, done,
complete, fail, and pause messages are recorded as `kind='summary'` events
via `db.sh log`. The task monitor queries these events via cursor-based
`db.sh tail summary --since <cursor>`. The task agent reads messages via
`db.sh recv` on its own queue.

**Agent narration**: Each dispatched agent (setup, integration proposal,
strategic implementation) is instructed to send messages about what it's
planning before each action. Messages go to the agent's own named queue
(e.g., `intg-proposal-01`) via `db.sh send`, which the per-agent monitor
watches. The agent narrates instead of maintaining state files — agents are
reliable narrators but unreliable at file management. If an agent detects
it's repeating work, it sends `LOOP_DETECTED` to its own queue and stops.

### Mail protocols

**Section-loop → Task Agent** (via db.sh send + db.sh log summary):

| Message | Meaning |
|---------|---------|
| `summary:setup:<num>:<text>` | Section setup (excerpt extraction) result summary |
| `summary:proposal:<num>:<text>` | Integration proposal agent result summary |
| `summary:proposal-align:<num>:<text>` | Integration proposal alignment check result |
| `summary:impl:<num>:<text>` | Strategic implementation agent result summary |
| `summary:impl-align:<num>:<text>` | Implementation alignment check result |
| `status:coordination:round-<N>` | Global coordinator starting round N |
| `status:paused` | Pipeline entered paused state (lifecycle event) |
| `status:resumed` | Pipeline resumed from paused state |
| `done:<num>:<count> files modified` | Section complete |
| `fail:<num>:<error>` | Section failed (includes `fail:<num>:aborted`, `fail:<num>:coordination_exhausted:<summary>`) |
| `fail:aborted` | Global abort (may occur at any time when no specific section context is available) |
| `complete` | All sections aligned and coordination done |
| `pause:underspec:<num>:<detail>` | Script paused — needs information |
| `pause:needs_parent:<num>:<detail>` | Script paused — needs parent decision |
| `pause:need_decision:<num>:<question>` | Script paused — needs human answer |
| `pause:dependency:<num>:<needed_section>` | Script paused — needs other section first |
| `pause:loop_detected:<num>:<detail>` | Script paused — agent entered infinite loop |

All messages above are sent to the task agent's queue via `db.sh send`
AND recorded as summary events via `db.sh log summary <tag> <body>`. Both
writes go to `run.db`. The task monitor queries summary events via
`db.sh tail`; the task agent reads messages via `db.sh recv`.

**Task Agent → Section-loop** (control):

| Message | Meaning |
|---------|---------|
| `resume:<payload>` | Continue after pause — payload contains answer/context |
| `abort` | Clean shutdown |
| `alignment_changed` | User input changed alignment docs, re-evaluate |

**Task Agent → UI Orchestrator** (progress reports + escalations):

| Message | Meaning |
|---------|---------|
| `progress:<task>:<num>:ALIGNED` | Section completed successfully |
| `progress:<task>:complete` | All sections done |
| `problem:stuck:<task>:<num>:<diagnosis>` | Stuck state detected |
| `problem:crash:<task>:<detail>` | Script crashed |
| `problem:escalate:<task>:<detail>` | Issue needs human input |

**Task Monitor → Task Agent** (escalations):

| Message | Meaning |
|---------|---------|
| `problem:stuck:<section>:<diagnosis>` | Alignment stuck for section |
| `problem:coordination:<round>:<diagnosis>` | Coordination not converging |
| `problem:loop:<section>:<agent-detail>` | Agent loop detected |
| `problem:stalled` | No activity detected |

**QA Monitor → Task Agent** (findings):

| Message | Meaning |
|---------|---------|
| `qa:warning:<category>:<detail>` | Compliance or strategic issue detected |
| `qa:paused:<category>:<detail>` | Critical issue — pipeline PAUSED |
| `qa:abort-recommended:<category>:<detail>` | Abort recommended (not autonomous) |

**Two signal routes per background task:**
1. Task completion — the background process exits (done or error)
2. Mailbox message — the process sends a signal while still running

The task agent always has a `recv` running as a background task so it is
always listening. When `recv` completes (message arrived), process it,
then immediately start another `recv`.

### Signal protocol

**section-loop → task agent (parent):**
- `pause:underspec:<section>:<description>` — needs research/proposal
- `pause:need_decision:<section>:<question>` — needs human answer
- `pause:dependency:<section>:<needed_section>` — needs another section first
- `done:<num>:<count> files modified` — section completed
- `fail:<num>:<error>` — section failed
- `complete` — all sections done

**task agent → section-loop:**
- `resume:<payload>` — continue (answer or context attached; payload
  is persisted to `artifacts/decisions/section-NN.md` and included in
  subsequent prompts)
- `abort` — clean shutdown
- `alignment_changed` — user input changed alignment docs; section-loop
  invalidates all excerpt files and re-queues completed sections

### Pause/resume flow

When an agent signals underspecification, dependency, or needs a decision:

1. section-loop sends `pause:*` to task agent's mailbox
2. section-loop blocks on its own `recv` (waiting for response)
3. Task agent's `recv` fires, task agent reads the signal
4. Task agent handles it:
   - `underspec` → trigger research/evaluate cycle, or ask user
   - `need_decision` → present question to user, collect answer
   - `dependency` → resolve the dependency, then resume
5. Task agent sends `resume:<answer>` to section-loop's mailbox
6. section-loop's `recv` fires, reads answer, persists to decisions
   file, and **retries the current step** (not continues forward)

After resume, section-loop:
- Persists the payload to `artifacts/decisions/section-NN.md`
- Re-runs the step (proposal generation or implementation) with the
  decision context included in the prompt
- The decisions file accumulates across multiple pause/resume cycles

If the task agent is not the top-level orchestrator, it may need
to bubble the signal up further — send its own `pause` to the
orchestrator and block on its own `recv`.

### User input cascade

When the user answers a tradeoff/constraint question, their answer may
change alignment documentation or design constraints. This cascades:

1. User provides answer → alignment docs updated
2. Task agent sends `alignment_changed` to section-loop's mailbox
3. section-loop invalidates ALL excerpt files (deletes them) and marks
   ALL completed sections dirty (back in queue)
4. When dirty sections re-run, setup re-extracts excerpts from the
   updated global documents, then re-creates integration proposals
   with updated context
5. Updated proposals cascade to new implementations

The cascade is intentionally coarse-grained: any alignment change
invalidates excerpts and re-queues everything.

### Per-section flow

```
Phase 1 — Initial pass (per-section):

  For each section in queue:
    Check for pending messages (abort, alignment_changed)
    Read incoming notes from other sections (consequence notes + diffs)

    Step 1: Section setup (Opus, once per section)
      Extract proposal excerpt from global proposal (copy/paste + context)
      Extract alignment excerpt from global alignment (copy/paste + context)
      → if excerpts already exist: skip (idempotent)

    Step 2: Integration proposal loop
      GPT (Codex) reads excerpts + source files, explores codebase (GLM sub-agents)
      Writes integration proposal: how to wire proposal into codebase
        → if agent signals: pause, wait for parent, resume
      Opus checks alignment (shape and direction, NOT tiny details)
        → ALIGNED: proceed to implementation
        → PROBLEMS: feed problems back, GPT revises proposal, re-check
        → UNDERSPECIFIED: pause, wait for parent, resume

    Step 3: Strategic implementation
      GPT (Codex) implements holistically with sub-agents
        (GLM for exploration, Codex for targeted areas)
        → if agent signals: pause, wait for parent, resume
      Opus checks implementation alignment (still solving right problem?)
        → ALIGNED: section done
        → PROBLEMS: feed problems back, GPT fixes, re-check
        → UNDERSPECIFIED: pause, wait for parent, resume

    Step 4: Post-completion (cross-section communication)
      Snapshot modified files to artifacts/snapshots/section-NN/
      Run semantic impact analysis via GLM (MATERIAL vs NO_IMPACT)
      Leave consequence notes for impacted sections:
        what changed, why, contracts defined, scope exceeded
      Send done:<section> to parent

Phase 2 — Global coordination (after all sections complete):

  Re-check alignment across ALL sections (cross-section changes may
  have introduced problems invisible during per-section pass)

  Coordination loop (max rounds):
    Collect outstanding problems across all sections
    Group related problems (GLM confirms file-overlap relationships)
    Size work and dispatch:
      Few related → single Codex agent
      Many unrelated → fan out to multiple Codex agents
    Re-run per-section alignment to verify fixes
    Repeat until all sections ALIGNED or max rounds reached
```

### Queue management

1. All sections start in the queue (ordered by dependency if known)
2. Pop one section, run it through the per-section flow
3. After each section completes: snapshot modified files, run semantic
   impact analysis (GLM), leave consequence notes for affected sections
4. Pop next section from queue (next section reads incoming notes
   from previously completed sections before starting)
5. Queue empty = all sections done → enter Phase 2 (global coordination)
6. Global alignment re-check across ALL sections
7. Coordinator collects problems, groups related ones, dispatches fixes
8. Re-verify per-section alignment, repeat until all ALIGNED
9. All sections ALIGNED → send `complete` to parent

### Alignment checks (shape and direction)

There are two alignment checks per section, both applied by Opus:

**Integration proposal alignment** — after GPT writes the integration
proposal, Opus reads the section alignment excerpt, proposal excerpt,
section specification, and the integration proposal. Checks whether
the integration strategy is still solving the RIGHT PROBLEM. Has intent
drifted? Does the strategy make sense given the codebase?

**Implementation alignment** — after GPT implements strategically, Opus
reads the same alignment/proposal context plus all implemented files.
Checks whether the code changes match the intent. Has anything drifted
from the original problem definition?

Both checks answer: "Is this still addressing the problem?" — not "Did
you follow every instruction?" Tiny details (code style, variable names,
edge cases not in constraints) are NOT checked.

Opus checks **go beyond the listed files**. The section spec may require
creating new files, modifying files not in the original list, or producing
artifacts at specific worktree paths. Opus verifies the worktree for any
file the section mentions should exist — not just what's enumerated.

If problems found → feedback goes back to GPT, which revises the
integration proposal or fixes the implementation. Each check is a loop:
propose/implement, check alignment, iterate until ALIGNED.

The integration proposal is NEVER modified by the implementation
alignment check. If implementation drifts, GPT fixes the implementation,
not the proposal.

### Cross-section communication

When a section completes, it communicates consequences to other sections
through three mechanisms:

**File snapshots** — modified files are copied to
`artifacts/snapshots/section-NN/`, preserving the state as the
completing section left them. Later sections can diff these snapshots
against current file state to see exactly what changed.

**Semantic impact analysis** — GLM evaluates whether the changes
MATERIALLY affect other sections' problems, or are just coincidental
file overlap. A change is material if it modifies interfaces, control
flow, or data structures another section depends on. A change is
no-impact if the overlap is in unrelated parts.

**Consequence notes** — for materially impacted sections, the script
writes notes to `artifacts/notes/from-NN-to-MM.md` explaining: what
changed, why, contracts/interfaces defined, what the target section may
need to accommodate. Notes reference the integration proposal for
contract details and the snapshot directory for exact diffs.

When a section starts (including during the global coordination phase),
it reads all incoming notes addressed to it. Notes provide context about
cross-section dependencies that inform the integration proposal and
implementation strategy.

### Global problem coordinator

After the initial per-section pass, a global coordination phase handles
cross-section issues that are invisible during isolated per-section
execution.

**Step 1**: Re-run alignment checks across ALL sections. Cross-section
changes (shared files modified by later sections) may have introduced
problems that were not visible during each section's individual pass.

**Step 2**: Collect all outstanding problems (MISALIGNED sections,
unresolved signals, consequence conflicts).

**Step 3**: Group related problems. Problems sharing files are candidate
groups. GLM confirms whether shared-file groups are truly related (same
root cause) or independent (different issues on the same files).

**Step 4**: Size the work and dispatch fixes:
- Few related problems → single Codex agent
- Few independent groups → one agent per group, sequential
- Many groups → fan out to multiple Codex agents in parallel

**Step 5**: Re-run per-section alignment on affected sections to verify
fixes actually resolved the problems.

**Step 6**: Repeat steps 2-5 until all sections ALIGNED or max
coordination rounds reached.

The coordinator replaces blind rescheduling cascades. Instead of redoing
entire sections when shared files change, problems are analyzed
holistically, grouped by root cause, and fixed in coordinated batches.

### Cleanup

section-loop.py cleans up its own agent registration on exit via
`db.sh cleanup` (normal completion, abort, or error). The `finally` block
in `main()` ensures cleanup runs even on exceptions. The parent should also
verify cleanup after the background task exits. Messages and events remain
in `run.db` as part of the audit trail — only agent registration status
is updated to `cleaned`.

## Stage 4: Section Setup + Integration Proposal

**Per-section** — run for each section in the queue.

### Document hierarchy

The pipeline uses a three-level document hierarchy:

**Global level** (exist before the pipeline runs):
- **Global proposal** — the original proposal document. Says WHAT to build.
- **Global alignment** — problem definition, constraints, what good/bad
  looks like, alignment criteria. Agents check their work against this.

**Section level** (derived from global, copy/paste with context):
- **Section proposal excerpt** — copied/pasted excerpt from the global
  proposal with enough surrounding context to be self-contained. NOT
  interpreted or rewritten — literal excerpt.
- **Section alignment excerpt** — copied/pasted excerpt from the global
  alignment with section-specific context. Same principle.

**Integration level** (GPT's new work):
- **Integration proposal** — GPT reads the section excerpts + actual
  source files, explores the codebase, then writes HOW to wire the
  existing proposal into the codebase. Strategic, not line-by-line.

### Section setup (Opus, once per section)

Opus reads the global proposal and global alignment, finds the parts
relevant to this section, and writes two excerpt files:
- `<planspace>/artifacts/sections/section-NN-proposal-excerpt.md`
- `<planspace>/artifacts/sections/section-NN-alignment-excerpt.md`

These are excerpts, not summaries. The original text is preserved with
enough surrounding context for each file to stand alone. Setup is
idempotent — if excerpts already exist, this step is skipped.

### Integration proposal (GPT, iterative with Opus alignment)

GPT reads the section proposal excerpt, alignment excerpt, section
specification, and related source files. Before writing anything, GPT
explores the codebase strategically:

**Dispatch GLM sub-agents for targeted exploration:**
```bash
agents --model glm --project <codespace> "<instructions>"
```

Use GLM to read files, find callers/callees, check existing interfaces,
understand module organization, and verify assumptions. Explore
strategically: form a hypothesis, verify with a targeted read, adjust.

After exploring, GPT writes an integration proposal to
`<planspace>/artifacts/proposals/section-NN-integration-proposal.md`:
1. **Problem mapping** — how the section proposal maps onto existing code
2. **Integration points** — where new functionality connects to existing code
3. **Change strategy** — which files change, what kind of changes, in what order
4. **Risks and dependencies** — what could go wrong, what depends on other sections

This is STRATEGIC — not line-by-line changes. The shape of the solution,
not the exact code.

### Intent layer (conditional, per-section)

Before alignment, the section loop runs intent triage to decide whether
a full or lightweight intent cycle is needed:

1. **Intent triage** (GLM) — evaluates section complexity factors
   (integration breadth, cross-section coupling, environment uncertainty,
   failure history) and returns `full` or `lightweight` intent mode.
   If uncertain, the agent escalates to a stronger model.

2. **Full intent mode** (when selected):
   - **Philosophy distillation** (Opus) — distills operational philosophy
     from source files into numbered principles with expansion guidance.
     Runs once globally, not per-section.
   - **Intent pack generation** (Codex-high) — produces per-section
     `problem.md` (seed problem definition with axes) and
     `problem-alignment.md` (rubric) from section spec, excerpts, code
     context, and TODOs.
   - **Intent-judge alignment** (Opus) — replaces the standard alignment
     judge; checks proposal coherence against the problem definition and
     rubric, discovers problem and philosophy surfaces.
   - **Expansion cycle** — dispatches problem-expander and
     philosophy-expander to integrate discovered surfaces into the living
     problem definition and philosophy. May trigger proposal restart if
     axes materially change.

3. **Lightweight mode** — skips intent pack and expanders, uses the
   standard alignment judge directly.

Artifacts: `artifacts/intent/global/philosophy.md`,
`artifacts/intent/sections/section-NN/problem.md`,
`artifacts/intent/sections/section-NN/problem-alignment.md`,
`artifacts/intent/sections/section-NN/surface-registry.json`.
See `loop-contract.md` for the full inputs list.

### Integration alignment check (Opus)

Opus reads the alignment excerpt, proposal excerpt, section spec, and
integration proposal. Checks SHAPE AND DIRECTION only:
- Is the integration proposal still solving the RIGHT PROBLEM?
- Has intent drifted from the original proposal/alignment?
- Does the strategy make sense given the actual codebase?

Does NOT check tiny details (exact code patterns, edge cases,
completeness). Those get resolved during implementation.

If problems found → GPT receives the specific problems and revises the
integration proposal. Iterate until ALIGNED.

## Stage 5: Strategic Implementation + Global Coordination

**Per-section** — GPT implements the aligned integration proposal.

### Strategic implementation (GPT, iterative with Opus alignment)

GPT reads the aligned integration proposal, section excerpts, and
source files. Implements the changes **holistically** — multiple files
at once, coordinated changes. NOT mechanical per-file execution.

**Dispatch sub-agents as needed:**

For cheap exploration (reading, checking, verifying):
```bash
agents --model glm --project <codespace> "<instructions>"
```

For targeted implementation of specific areas, write a prompt file first
(Codex models require `--file`, not inline instructions):
```bash
PROMPT="$(mktemp)"
cat > "$PROMPT" <<'EOF'
<instructions>
EOF
agents --model gpt-5.3-codex-high --project <codespace> --file "$PROMPT"
```

GPT has authority to go beyond the integration proposal where necessary
(e.g., a file that needs changing but was not in the proposal, an
interface that does not work as expected).

After implementation, GPT writes a list of all modified files to
`<planspace>/artifacts/impl-NN-modified.txt`.

### Implementation alignment check (Opus)

Opus reads the alignment excerpt, proposal excerpt, integration proposal,
section spec, and all implemented files. Checks whether the
implementation is still solving the right problem. Same shape/direction
check as the integration alignment.

If problems found → GPT receives the problems and fixes the
implementation. Iterate until ALIGNED.

### Post-completion (cross-section communication)

After a section is ALIGNED:
1. Snapshot modified files to `artifacts/snapshots/section-NN/`
2. Run semantic impact analysis (GLM): which other sections are
   materially affected by these changes?
3. Leave consequence notes for impacted sections at
   `artifacts/notes/from-NN-to-MM.md`

### Global coordination (Phase 2)

After all sections complete their initial pass:
1. Re-check alignment across ALL sections (cross-section changes may
   have broken previously-aligned sections)
2. Global coordinator collects outstanding problems across all sections
3. Groups related problems (GLM confirms relationships via shared files)
4. Dispatches coordinated fixes (Codex agents, sized by problem count)
5. Re-runs per-section alignment to verify fixes
6. Repeats until all sections ALIGNED or max coordination rounds reached

Integration proposals, consequence notes, and file snapshots are
external artifacts — no markers placed in source code.

## Stage 6: Verification

After the section queue is empty (all sections clean), verify in the
task worktree:

### 6a: Constraint Alignment Check (Codex-high)
Check against design principles. Fix violations.

### 6b: Lint Fix
```bash
uv run lint-fix --changed-only
```
Run repeatedly until clean. A clean run looks like:
```
=== Initial lint run ===
No lint errors found.
```

### 6c: Tests
```bash
uv run pytest <test-dir> -k "<relevant-tests>" -x -v -p no:randomly
```

### 6d: Debug/RCA
If tests fail: Codex-high reads failures, fixes root cause, re-runs.
Persistent after one round → escalate.

## Stage 7: Post-Task Verification

1. Full test suite in the task worktree
2. Test count check (compare against baseline)
3. Cross-file import check
4. Commit

## Test Baseline

Capture before Stages 4-5 (in the task worktree):
```bash
uv run pytest <test-dir> -v -p no:randomly > <planspace>/artifacts/baseline-failures.log 2>&1
```

## Handling Underspecified / Missing Information

**CRITICAL**: Do NOT solve underspecified problems in-place during
implementation. If any stage reveals something missing or ambiguous,
the agent signals via its output and the section-loop pauses.

### Signal flow

```
Agent output contains UNDERSPECIFIED/NEED_DECISION/DEPENDENCY/OUT_OF_SCOPE/NEEDS_PARENT
  → section-loop detects signal
  → section-loop sends pause:* to parent mailbox
  → section-loop blocks on its own recv (context preserved)
  → parent handles the signal (research cycle, ask user, resolve dependency)
  → parent sends resume:<answer> to section-loop mailbox
  → section-loop unblocks, incorporates answer, continues
```

If the parent is the orchestrator (not the interactive session), it
bubbles the signal up: sends its own pause to its parent, blocks on
its own recv, and forwards the answer back down when it arrives.

### Case 1: Missing specification (needs new research)

Agent signals: `UNDERSPECIFIED: <what's missing>`
section-loop sends: `pause:underspec:<section>:<description>`

The parent handles:
1. **Research**: create a sub-proposal via the research skill
   (`research.md` Phase C — model per policy; see `models.md`)
2. **Evaluate**: review the sub-proposal via the evaluate skill
   (`evaluate.md` — alignment check against design principles)
3. **Human gate**: present proposal to user for approval
   (if parent is orchestrator, bubble up to interactive session)
4. **Decompose**: the sub-proposal becomes new section files added to
   the planspace (same decomposition pipeline as Stage 1)
5. **Resume**: send `resume:researched` to section-loop's mailbox
6. section-loop re-runs the current section with updated context
7. **Important**: newly created section files are NOT visible to the
   running section-loop process (sections are loaded once at startup).
   The parent must **restart** section-loop to pick up new sections.
8. Original section picks up changes via cross-section communication
   (consequence notes + snapshots from the new sections)

### Case 2: Dependency on another section in the queue

Agent signals: `DEPENDENCY: <which section and why>`
section-loop sends: `pause:dependency:<section>:<needed_section>`

The parent handles:
1. **Resolve** the dependency externally (ensure the needed section has
   been implemented, or provide the missing context through other means)
2. **Resume**: send `resume:proceed` to section-loop's mailbox
3. section-loop retries the current step with updated context (including
   any changes the dependency resolution made visible through cross-section
   communication — consequence notes and snapshots)

Do NOT try to work around the dependency or implement both simultaneously.

### Case 3: Needs human decision (tradeoff/constraint)

Agent signals: `NEED_DECISION: <question about tradeoffs/constraints>`
section-loop sends: `pause:need_decision:<section>:<question>`

The parent handles:
1. **Present** the question to the user (bubble up if needed)
2. **Collect** the user's answer
3. **Update** alignment docs / constraints if the answer changes them
4. **Resume**: send `resume:<answer>` to section-loop's mailbox
5. If alignment docs changed: also send `alignment_changed` which
   causes section-loop to mark all completed sections dirty

### Case 4: Missing information clearly available elsewhere

The agent does NOT signal — it notes in the integration proposal
referencing the existing code and continues. No pause needed.

If the target is in another section, cross-section communication
handles it. Consequence notes and file snapshots from the completing
section inform the dependent section's integration proposal.

### Case 5: Work is out of scope for this section

Agent signals: `OUT_OF_SCOPE: <what and why>`

The agent has discovered work that does not belong in the current
section's problem frame. The section-loop routes the finding to
scope-delta artifacts. The parent may create new sections or route
the finding to an existing section. The current section continues
without addressing the out-of-scope item.

### Case 6: Needs parent-level authority

Agent signals: `NEEDS_PARENT: <what authority is needed>`
section-loop sends: `pause:needs_parent:<section>:<detail>`

The agent has encountered a decision that exceeds its authority
(e.g., cross-section architectural changes, external dependency
decisions). The parent investigates, makes the decision, and
sends `resume:<decision>`.

## Other Escape Hatches

**Mutual dependency (same section)** → GPT handles holistically during
strategic implementation. Multiple files at once, coordinated changes.

**Cross-section dependency** → Cross-section communication handles it.
Consequence notes, file snapshots, and the global coordinator resolve
conflicts after the initial pass.

## Model Roles

| Stage | Model | Role |
|-------|-------|------|
| 1: Decomposition | Opus | Recursive section identification + materialization |
| 1C: Section Summaries | GLM | YAML frontmatter per section file |
| 2: Docstrings | GLM | Add/update module docstrings per file |
| 3: Codemap Exploration | Opus | Explore the codespace, build understanding, write `codemap.md` |
| 3: Section File Identification | Opus | Per-section agent: reason over codemap + section goals, identify related files |
| 3: Deep Scan | GLM | Per-file analysis: reason about specific relevance in section context |
| 4: Section Setup | Opus | Extract proposal/alignment excerpts from global documents |
| 4: Integration Proposal | Codex (GPT) | Write integration proposal with GLM sub-agent exploration |
| 4: Integration Alignment | Opus | Shape/direction check on integration proposal |
| 5: Strategic Implementation | Codex (GPT) | Holistic implementation with sub-agents (GLM + Codex) |
| 5: Implementation Alignment | Opus | Shape/direction check on implemented code |
| 5: Impact Analysis | GLM | Semantic impact analysis for cross-section communication |
| 5: Global Coordination | Codex (GPT) | Coordinated fixes for grouped cross-section problems |
| 5: Coordination Alignment | Opus | Per-section re-verification after coordinated fixes |
| 6a: Constraint Alignment Check | Codex-high | Design principle check |
| 6d: Debug/RCA | Codex-high | Fix test failures |

## Anti-Patterns

- **DO NOT edit source files yourself** — delegate ALL editing to agents
- **DO NOT place markers in source code** — integration proposals, consequence notes, and snapshots are external artifacts
- **DO NOT skip the docstring stage** — it's the scan infrastructure (but only target relevant files, not the entire repo)
- **DO NOT prescribe solutions in alignment docs** — alignment defines constraints and the problem, NOT the solution. GPT writes integration proposals.
- **DO NOT check tiny details in alignment** — alignment checks shape and direction only. Code style, variable names, and edge cases are resolved during implementation.
- **DO NOT solve underspecified problems in-place** — stop the section, trigger a research/evaluate cycle, decompose the sub-proposal into new sections
- **DO NOT work around section dependencies** — if section A needs section B, resolve the dependency externally (ensure B is implemented or provide the missing context), then `resume:proceed`. Do not guess or stub the dependency
- **DO NOT skip alignment checks** — both integration proposal and implementation alignment are mandatory
- **DO NOT skip tests** — verify before moving to next section
- **DO NOT skip constraint alignment check** — verify before committing
- **DO NOT reschedule entire sections on shared-file changes** — use cross-section communication (snapshots, impact analysis, consequence notes) and global coordination instead
