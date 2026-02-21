# Implement Proposal: Multi-Model Execution Pipeline

Stage 3 prompt templates are canonical in
`artifacts/sections/section-07.md` (prompts 7.1, 7.2, 7.3), and any inline
template retained here must remain byte-for-byte aligned with section-07.
This document defines orchestration flow, dispatch mechanics, and parsing/output
constraints only, including Tier 2 intermediate region-summary artifacts
at `artifacts/scan-logs/codemap-region-*-output.md`, with Stage 3
per-section strategic exploration defined as concurrent dispatch
(max 5 in-flight) without requiring a specific orchestration mechanism.

## Workflow Orchestration

This skill is designed to be executed via the workflow orchestration system.
Each stage below corresponds to a schedule step in the `implement-proposal.md`
template. The orchestrator pops steps and dispatches agents to the matching
stage section. Stage 3 uses a Tier 1 + Tier 2 + Tier 3 scan contract: quick
mode starts with a local structural scan artifact generation step (Tier 1),
then concurrent Opus region exploration with GLM file-characterization reads
(Tier 2), then codemap synthesis into `planspace/artifacts/codemap.md`
(Tier 3), then concurrent per-section Opus strategic agents that run
codemap reasoning + targeted GLM verification + adjacency/beyond-codemap
discovery before a deep refinement pass over quick-confirmed matches while preserving the public
`scan.sh quick|deep|both` interface and
`## Related Files` output format.

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

**You NEVER**: edit `.py` files, place markers yourself, fix code yourself,
combine multiple files into one agent call.

## Prompt Construction Rules

**CRITICAL**: Prompts reference filepaths — agents read files themselves.

Agents dispatched via `uv run agents` have full filesystem access. Prompts
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
(uv run agents --model <model> --file <prompt.md> \
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

1. **Section Decomposition** — Recursive decomposition into atomic section files
2. **Docstring Infrastructure** — Ensure all source files have module docstrings
3. **File Relevance Scan** — Quick mode runs Tier 1 structural scan, Tier 2 concurrent Opus region exploration with GLM file reads, and Tier 3 synthesis into `planspace/artifacts/codemap.md`; then run per-section strategic exploration and deep scan confirmed matches (preserving `## Related Files`)

--- Per-section loop (strategic, agent-driven) ---

4. **Section Setup + Integration Proposal** — Extract proposal/alignment excerpts from
   global documents, then GPT writes integration proposal (how to wire proposal into
   codebase), Opus checks alignment on shape/direction, iterate until aligned
5. **Strategic Implementation + Global Coordination** — GPT implements holistically with
   sub-agents (GLM for exploration, Codex for targeted areas), Opus checks alignment
→ After all sections: cross-section alignment re-check, global coordinator collects
  problems, groups related ones, dispatches coordinated fixes, re-verifies per-section

--- End per-section loop (all sections aligned = done) ---

6. **Verification** — Constraint audit + lint + tests
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
| 2: Docstrings | **Sequential** — one GLM per file, edits source |
| 3: Scan | **Shell script** — quick: sequential local structural scan (Tier 1), concurrent Opus region exploration (Tier 2), sequential single-compare GLM sub-reads within each region, single-run codemap synthesis (Tier 3), then concurrent per-section Opus strategic agents (max 5 in-flight); each section agent coordinates single-compare GLM verification calls and writes only to its own section file; deep: full-content analysis for confirmed matches |
| 4–5: Section Loop | **Sequential** — one section at a time, strategic agent-driven implementation with sub-agent dispatch; global coordination after initial pass |
| 6: Verification | **Sequential** — lint, test, fix cycles |
| 7: Post-Verify | **Single run** — full suite + commit |

## Extraction Tools

Language-specific extraction helpers live in `$WORKFLOW_HOME/tools/`.
Named `extract-docstring-<ext>`.

Stage 3 quick mode does not depend on brute-force per-file docstring
extraction. These tools are used only where targeted verification/deep
scan needs extension-specific extraction support.

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

Verbatim copies guarantee decomposition accuracy — no audit needed.

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

Extract summaries in batch:
```bash
find <planspace>/artifacts/sections -name "section-*.md" \
  | python3 "$TOOLS/extract-summary-md" --stdin
```

## Stage 2: Docstring Infrastructure (GLM)

**Sequential** — one GLM agent per file, edits source to add/update
module docstrings.

Module-level docstrings serve as file summaries. They are standard
practice, live in source control, and are cheaply extractable. They
enable Stage 3 to scan relevance without reading full files.

### 2a: Discover Files

```bash
find <codespace>/scripts/spec_manager -name "*.py" -not -name "__init__.py"
```

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

## Stage 3: File Relevance Scan

**Shell-script driven** — no Claude orchestrator agent needed.

```bash
bash "$WORKFLOW_HOME/scripts/scan.sh" both <planspace> <codespace>
# or separately:
bash "$WORKFLOW_HOME/scripts/scan.sh" quick <planspace> <codespace>
bash "$WORKFLOW_HOME/scripts/scan.sh" deep  <planspace> <codespace>
```

Stage placement is unchanged: this runs after Stage 2 and before the
section-loop (Stages 4-5). Quick mode now starts with a local Tier 1
structural scan pre-step (pure Python/shell, no LLM/agent calls) before
codemap construction. The public CLI remains unchanged.

### CLI contract (public interface)

- `bash "$WORKFLOW_HOME/scripts/scan.sh" quick <planspace> <codespace>`
- `bash "$WORKFLOW_HOME/scripts/scan.sh" deep <planspace> <codespace>`
- `bash "$WORKFLOW_HOME/scripts/scan.sh" both <planspace> <codespace>`

### Tier 1 helper contract (internal sub-step)

```bash
python3 "$WORKFLOW_HOME/scripts/structural-scan.py" <codespace> <output-path>
```

Implementation requirement (Section 02): `scripts/scan.sh` quick mode
invokes this helper via `run_structural_scan()`. Therefore
`$WORKFLOW_HOME/scripts/structural-scan.py` is a required concrete
deliverable for this section, not a deferred/future task.

- `<codespace>`: directory path to repository root
- `<output-path>`: file path for markdown structural artifact
- Output: non-empty markdown artifact containing directory tree summary,
  file-type distribution, and key files/project markers

### Parameter types

- `<mode>`: enum `{quick, deep, both}`
- `<planspace>`: path to directory containing `artifacts/sections/section-*.md`
- `<codespace>`: path to target repository root
- `project_size`: enum `{small, medium, large}` derived from file-count thresholds
- `region`: directory-path string
- `region_summary`: markdown artifact per region
- `codemap`: markdown artifact with required sections: `Project Shape`, `Directory Map`, `Cross-Cutting Patterns`

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
  - `<planspace>/artifacts/structural-scan.md`
  - `<planspace>/artifacts/codemap.md`
  - `<planspace>/artifacts/scan-logs/codemap-region-*-output.md` (region summaries)
  - Per-section exploration logs (for debug/resume)

### Tier contracts (internal)

- Tier 1 input: `<codespace>`; output: non-empty `structural-scan.md`
- Tier 2 input: structural scan artifact + region list; output: region summaries
  (region characterization template is canonical in section-07 prompt 7.3)
- Tier 3 input: region summaries + codemap schema template; output: `codemap.md`

### Codemap prompt contract (`scripts/codemap_build.py`)

- `build_region_summary()` must embed the canonical section-07 prompt 7.3
  wording verbatim for region characterization:

```markdown
# Task: Characterize Directory Region

Read the following files in {directory}:
{file_list}

Write a summary covering:
- What this directory is for (1-2 sentences)
- Key files and their roles
- How this directory relates to the rest of the project
```

- `build_region_summary()` must also include this explicit graceful-degradation
  instruction in the prompt body:
  `If a GLM dispatch fails, note the failure and continue with the remaining files. Produce a region summary from whatever files you successfully read.`
- `build_codemap_small()` prompt must include:
  `Total output should be under 5KB.`
- `synthesize_codemap()` prompt must include a size-adaptive budget line:
  - medium projects: `Total output should be under 15KB.`
  - large projects: `Total output should be under 30KB.`
- All `scripts/codemap_build.py` subprocess dispatch lists that launch
  agents must use the canonical command token order:
  `"uv", "run", "--frozen", "agents", ...` (all three call sites:
  region summary dispatch, small-project codemap dispatch, and codemap
  synthesis dispatch).

### Quick mode control flow

1. Validate Tier 1 artifact: run local structural scan once (resume-safe)
   and require a non-empty `structural-scan.md`.
2. Derive `project_size` (`small|medium|large`) from file-count thresholds.
3. Identify scan regions from repository structure.
4. Run Tier 2 region exploration: dispatch Opus agents concurrently by
   region (size-adaptive strategy), and collect all region summaries.
   Use section-07 prompt 7.3 as the canonical region-characterization template.
5. Inside each region agent, run GLM file-characterization reads as
   single-compare sequential calls (no batching).
6. Run Tier 3 synthesis once: combine region summaries into
   `<planspace>/artifacts/codemap.md`.
7. Dispatch one Opus strategic agent per section file concurrently
   (max 5 in-flight). Each agent receives: section file
   path (`<planspace>/artifacts/sections/section-*.md`), codemap path
   (`<planspace>/artifacts/codemap.md`), codespace root (`<codespace>`),
   and an embedded 1-2 line section summary extracted from YAML frontmatter.
8. Inside each section agent, execute this loop:
   - Step 1: reason from codemap + section to form candidate hypotheses.
   - Step 2: run targeted GLM verification for each `(section, file)` pair
     as single-compare calls (`RELATED: <reason>` or `NOT_RELATED`).
   - Step 3: expand from confirmed matches via adjacency exploration.
   - Step 4: perform beyond-codemap discovery (directory listing, grep,
     direct reads) when coverage appears incomplete.
   - Step 5: append canonical `## Related Files` entries (`### <repo-relative-path>`
     + `- Relevance: <reason>`) to that section file.
   - Exploration bound: verify approximately 20-30 candidate files per
     section unless hard evidence requires additional checks.
9. Persist section-local outputs only; each agent writes to its own section
   file and local diagnostics.
10. Per-section strategic exploration is partial-success tolerant: if some
    section agents fail but others succeed, keep diagnostics for failed
    sections, keep artifacts for successful sections, and continue the
    pipeline (including deep scan) for successfully explored sections.

### Section-agent prompt contract (Stage 3 quick)

- The prompt must prescribe the full 5-step strategy above
  (hypothesize -> GLM verify -> adjacency -> beyond-codemap -> write).
- The prompt must source the full GLM quick verification template from
  section-07 prompt 7.1. Write this exact template to a prompt file for
  each GLM check:
  ```text
  # Task: File-Section Relevance Check

  Is this source file related to this proposal section?

  ## Section Summary
  {section_summary}

  ## File: {filepath}
  {file_content_or_docstring}

  ## Instructions
  Reply with exactly one line:
  RELATED: <brief reason>
  or
  NOT_RELATED

  Nothing else.
  ```
- This inline block must remain byte-for-byte aligned with
  `artifacts/sections/section-07.md` prompt 7.1.
- The prompt must include this GLM dispatch command pattern exactly:
  `uv run --frozen agents --model glm --project <codespace> --file <prompt-file>`.
- Prompt size discipline: reference section/codemap by filepath, and embed
  only the short section summary from YAML frontmatter.

Strategic exploration is Opus-driven. GLM verification calls are
single-compare only (no batching assumptions).

### Deep mode control flow

1. Run after quick exploration and process only confirmed matches already
   listed under `## Related Files`.
2. For each `(section, file)` pair, skip entries already deep-annotated
   (resume-safe).
3. Read full section content + full file content, then run one GLM
   single-compare deep call for that pair.
   - Source the deep prompt body from section-07 prompt 7.2; keep
     `implement.md` limited to execution constraints and parse contracts.
4. Deep prompt guidance for `Affected areas` is file-type-aware:
   - `.py`: functions/classes/methods/code regions
   - `.md`: headings/sections/rules/instruction blocks
   - `.sh`: functions/sections/command blocks
   - other: most specific structural elements for that file type
5. Refresh details under each `### <filepath>` entry using the canonical
   deep output fields:
   - `Relevance`
   - `Affected areas`
   - `Confidence`
   - `Open questions`
6. For exact deep template wording, see section-07 prompt 7.2
   (including `Write your analysis in this exact format:`).
   - Prompt structure alignment: do not insert any extra sentence between
     the deep prompt title and `## Section`; it must transition directly.
   - Output placeholders must use:
     - `- Affected areas: <specific functions, classes, or regions>`
     - `- Open questions: <uncertainties, or "none">`
7. Skip missing/invalid related file paths with diagnostics. On per-pair
   GLM/update failures, record diagnostics and continue remaining pairs.

GLM deep calls are also single-compare only: one full section and one
full file per call.

### Both mode control flow

Run `quick` then `deep` in sequence. Deep flow is unchanged, with the
inherited dependency that quick has already produced Tier 1 structural
scan output and completed the Tier 2/3 codemap pipeline.

### Related-files accumulation format

For each confirmed match, the script appends/updates in the section file:

```markdown
## Related Files

### path/to/file.py
- Relevance: <why this file relates>
```

The section file becomes the single source of truth — it contains the
verbatim proposal text, the YAML summary, and related file matches.
A file can appear in multiple section files.

### Resume support

- Full resume: if `<planspace>/artifacts/codemap.md` already exists and is
  valid, skip Tier 1/2/3 codemap build and continue with section/deep flows.
- Partial resume: if region summaries are retained from an interrupted run,
  they may be reused for synthesis when policy/config allows.
- Diagnostics retention: failures in one region must remain inspectable via
  per-region logs without discarding other region diagnostics.
- Existing `## Related Files` entries must be read before exploration;
  already-listed files are skipped on reruns (no duplicate rework).

### Error handling

- Unknown mode or missing path args: exit non-zero with usage.
- Missing section files or inaccessible codespace: exit non-zero with an
  explicit diagnostic.
- If structural scan invocation fails: stop Stage 3 before codemap build
  (Tier 2/3) and per-section exploration.
- If structural scan artifact validation fails (missing/empty/unreadable):
  stop Stage 3 before codemap build (Tier 2/3) and per-section exploration.
- If a GLM sub-read fails inside a region: record per-file diagnostics and
  continue region processing with available data.
- If a region agent fails: record region-level diagnostics; continue only if
  synthesis policy allows incomplete regions, otherwise fail fast.
- If codemap synthesis (Tier 3) fails: stop Stage 3 before per-section
  exploration.
- If one section strategic agent fails: record section-local diagnostics,
  keep artifacts, and continue with other section agents where possible
  (per-section failure isolation under concurrent section-agent execution).
- Quick scan orchestration must treat partial section-agent failure as
  non-fatal when at least one section exploration succeeds: return success
  after logging failed sections so downstream deep scan can run on
  successfully explored sections.
- If a GLM verification sub-read fails inside a section agent: record
  section-local diagnostics and continue remaining candidates/adjacencies.
- Deep scan runs only on confirmed matches.
- No fallback to deprecated brute-force paths.

### What Stage 3 replaces

The strategic scan replaces v1 scan internals entirely:

| Removed v1 component | Replacement |
|---|---|
| Import graph builder | Codemap structural characterization (`codemap.md`) |
| Seed ingestion / frontier walk / convergence sweeps / sentinel scan | Per-section strategic exploration against codemap |
| Controls index | Shared codemap + per-section exploration logs |
| Structured seed YAML | Candidate discovery inside section exploration and targeted verification |

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
4. Launches via: `uv run agents --model claude-opus --file <planspace>/artifacts/task-agent-prompt.md`
5. Runs `recv` on its own mailbox to receive reports from the task agent

The task agent then owns the section-loop lifecycle:

```bash
python3 "$WORKFLOW_HOME/scripts/section-loop.py" <planspace> <codespace> \
  --global-proposal <proposal-path> --global-alignment <alignment-path> \
  --parent <agent-name>
```

The script runs as a **background task** under a **task agent**. The task
agent is launched via `uv run agents` and is responsible for:
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
  └─ Task Agent (one per task, via uv run agents)
       ├─ launches section-loop + monitor
       ├─ recv on task-agent queue (section-loop messages + escalations)
       ├─ send to orchestrator queue (reports progress + problems)
       ├─ Task Monitor (GLM, section-level pattern matcher)
       │    ├─ db.sh tail summary --since <cursor> (cursor-based event query)
       │    ├─ db.sh log lifecycle pipeline-state "paused" (pause/resume)
       │    └─ send to task-agent queue (escalations)
       └─ section-loop.py (background subprocess)
            ├─ db.sh send (messages) + db.sh log summary (events)
            ├─ db.sh query lifecycle --tag pipeline-state (pause check)
            ├─ recv on section-loop queue (when paused by signals)
            └─ per agent dispatch:
                 ├─ agent (uv run agents, sends narration via db.sh)
                 └─ Agent Monitor (GLM, per-dispatch loop detector)
                      ├─ reads agent's narration queue via db.sh
                      └─ db.sh log signal (NOT message send)
```

All coordination goes through `db.sh` and a single `run.db` per pipeline
run. No team/SendMessage infrastructure — agents are standalone processes
launched via `uv run agents`, not Claude teammates. Every coordination
operation (send, recv, log) is automatically recorded in the database.
Messages are claimed, not consumed — the database file is the complete
audit trail.

**UI Orchestrator**: Launches task agents via `uv run agents --file`,
runs `db.sh recv` on its own queue, receives reports, makes decisions,
communicates with user. Does NOT directly launch or monitor section-loop
scripts.

**Task Agent**: Intelligent overseer launched via `uv run agents`. Launches
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
uv run --frozen agents --model glm --project <codespace> "<instructions>"
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
uv run --frozen agents --model glm --project <codespace> "<instructions>"
```

For targeted implementation of specific areas:
```bash
uv run --frozen agents --model gpt-5.3-codex-high --project <codespace> "<instructions>"
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

### 6a: Constraint Audit (Codex-high2)
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
Agent output contains UNDERSPECIFIED/NEED_DECISION/DEPENDENCY
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
   (`research.md` Phase C — codex-xhigh generates the proposal)
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
| 3: Structural Scan | (local) | Directory walk, file counts/types, and project markers; produce structural scan artifact |
| 3: Codemap Region Exploration | Opus | Per-region characterization using structural scan context and region-level reasoning |
| 3: Codemap File Characterization | GLM | Single-compare file reads inside each region agent to characterize key files |
| 3: Codemap Synthesis | Opus (or orchestrator) | Combine region summaries into `codemap.md` with `Project Shape`, `Directory Map`, and `Cross-Cutting Patterns` |
| 3: Strategic Exploration | Opus | Per-section strategic agent: reason over codemap + section, orchestrate targeted single-compare GLM checks, explore adjacencies and beyond-codemap candidates, and append section-local `## Related Files` entries |
| 3: Verification + Deep Scan | GLM | Single-compare verification/deep analysis for quick-confirmed matches only; one `(section, file)` pair per call with file-type-aware `Affected areas` analysis |
| 3: Tool Creation | Opus | Write extraction tools needed for targeted verification/deep scan |
| 4: Section Setup | Opus | Extract proposal/alignment excerpts from global documents |
| 4: Integration Proposal | Codex (GPT) | Write integration proposal with GLM sub-agent exploration |
| 4: Integration Alignment | Opus | Shape/direction check on integration proposal |
| 5: Strategic Implementation | Codex (GPT) | Holistic implementation with sub-agents (GLM + Codex) |
| 5: Implementation Alignment | Opus | Shape/direction check on implemented code |
| 5: Impact Analysis | GLM | Semantic impact analysis for cross-section communication |
| 5: Global Coordination | Codex (GPT) | Coordinated fixes for grouped cross-section problems |
| 5: Coordination Alignment | Opus | Per-section re-verification after coordinated fixes |
| 6a: Constraint Audit | Codex-high2 | Design principle check |
| 6d: Debug/RCA | Codex-high | Fix test failures |

## Anti-Patterns

- **DO NOT edit source files yourself** — delegate ALL editing to agents
- **DO NOT place markers in source code** — integration proposals, consequence notes, and snapshots are external artifacts
- **DO NOT skip the docstring stage** — it's the scan infrastructure
- **DO NOT prescribe solutions in alignment docs** — alignment defines constraints and the problem, NOT the solution. GPT writes integration proposals.
- **DO NOT check tiny details in alignment** — alignment checks shape and direction only. Code style, variable names, and edge cases are resolved during implementation.
- **DO NOT solve underspecified problems in-place** — stop the section, trigger a research/evaluate cycle, decompose the sub-proposal into new sections
- **DO NOT work around section dependencies** — if section A needs section B, resolve the dependency externally (ensure B is implemented or provide the missing context), then `resume:proceed`. Do not guess or stub the dependency
- **DO NOT skip alignment checks** — both integration proposal and implementation alignment are mandatory
- **DO NOT skip tests** — verify before moving to next section
- **DO NOT skip constraint audit** — verify before committing
- **DO NOT reschedule entire sections on shared-file changes** — use cross-section communication (snapshots, impact analysis, consequence notes) and global coordination instead
