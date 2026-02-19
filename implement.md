# Implement Proposal: Multi-Model Execution Pipeline

## Workflow Orchestration

This skill is designed to be executed via the workflow orchestration system.
Each stage below corresponds to a schedule step in the `implement-proposal.md`
template. The orchestrator pops steps and dispatches agents to the matching
stage section.

Schedule step → Skill section mapping:
- `decompose` → Stage 1: Section Decomposition
- `docstrings` → Stage 2: Docstring Infrastructure
- `scan` → Stage 3: File Relevance Scan
- `section-loop` → Stages 4–5: Per-Section Execution (includes alignment)
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

**Reference by filepath**: section files, solution docs, source files, plan
files, integration stories — anything with substantial content.

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
mailbox coordination. **Never** use MCP background-job tools or Bash
`wait` for parallel agents — both block the orchestrator.

**Key rule**: Always have exactly ONE `recv` running as a background
task. It waits for the next message. When it completes, process the
result and immediately start another `recv` if more messages are
expected. This ensures you are always listening.

```bash
MAILBOX="$HOME/.claude/skills/workflow/scripts/mailbox.sh"

# 1. Register orchestrator mailbox
bash "$MAILBOX" register <planspace> orchestrator

# 2. Start recv FIRST — always be listening before dispatching
bash "$MAILBOX" recv <planspace> orchestrator 600  # run_in_background: true

# 3. Fire-and-forget: each agent sends mailbox message on completion
(uv run agents --model <model> --file <prompt.md> \
  > <planspace>/artifacts/<output.md> 2>&1 && \
  bash "$MAILBOX" send <planspace> orchestrator "done:<tag>") &

# 4. When recv notifies you of completion:
#    - Process the result
#    - Start another recv if more messages expected:
bash "$MAILBOX" recv <planspace> orchestrator 600  # run_in_background: true

# 5. Clean up when ALL messages received (no more agents outstanding)
bash "$MAILBOX" cleanup <planspace> orchestrator
bash "$MAILBOX" unregister <planspace> orchestrator
```

The recv → process → recv loop continues until all agents have reported.
Only clean up the mailbox when no more messages are expected.

## Pipeline Overview

1. **Section Decomposition** — Recursive decomposition into atomic section files
2. **Docstring Infrastructure** — Ensure all source files have module docstrings
3. **File Relevance Scan** — GLM checks each file's docstring against each section

--- Per-section loop (one section at a time) ---

4. **Solution** — Opus reads section + related files → writes solution doc
5. **Plan + Implement** — Per file: Codex plans then implements, Codex updates docstrings
→ After all files: Opus exploratory alignment check → if misaligned, redo plan/impl
→ Cross-section file changes → reschedule affected sections

--- End per-section loop (queue empty = all sections clean) ---

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
  ├── Stages 4-5: agents edit source files sequentially
  ├── Stage 6: verify in-place
  └── Stage 7: final verification + commit
```

**Cross-task parallelism**: Multiple tasks can run simultaneously in
separate worktrees. Each task is fully independent.

**Within-task sequencing**: All stages and all agents within a stage run
one at a time. Each agent sees the full accumulated state from all prior
agents — no merge conflicts, no stale snapshots.

## Stage Concurrency Model

| Stage | Concurrency |
|-------|-------------|
| 1: Decomposition | **Parallel** — writes to planspace only |
| 2: Docstrings | **Sequential** — one GLM per file, edits source |
| 3: Scan | **Shell script** — GLM per file×section (quick) + per hit (deep) |
| 4–5: Section Loop | **Sequential** — one section at a time, one file at a time |
| 6: Verification | **Sequential** — lint, test, fix cycles |
| 7: Post-Verify | **Single run** — full suite + commit |

## Extraction Tools

Language-specific tools for extracting docstrings live in
`$WORKFLOW_HOME/tools/`. Named `extract-docstring-<ext>`.

```bash
TOOLS="$HOME/.claude/skills/workflow/tools"

# Single file
python3 "$TOOLS/extract-docstring-py" <file>

# All Python files (batch via stdin)
find <codespace> -name "*.py" | python3 "$TOOLS/extract-docstring-py" --stdin
```

If the pipeline encounters a file extension with no extraction tool,
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

The script enumerates all section files × source files, extracts
summaries from each using the extraction tools, passes the two summaries
to GLM for matching, and appends GLM's response to the section file.

### Script behavior

```
for each section file:
  extract section summary (via extract-summary-md)
  for each source file:
    determine extraction tool by file extension
    if no tool exists:
      dispatch Opus agent to write one → $WORKFLOW_HOME/tools/
    extract file docstring (via extract-docstring-<ext>)
    dispatch GLM with: section summary + file docstring
    GLM writes response file (RELATED/NOT_RELATED + reasoning)
    if RELATED:
      script appends the match to the section file's ## Related Files
```

### Extraction tool discovery

The script checks for `$WORKFLOW_HOME/tools/extract-docstring-<ext>`
where `<ext>` is the source file's extension. If the tool doesn't exist,
it dispatches an Opus agent to write one following the interface in
`$WORKFLOW_HOME/tools/README.md`, then continues.

### GLM input

Each GLM call receives exactly two summaries:
- The section summary (from YAML frontmatter)
- The file docstring (from module-level docstring)

GLM outputs: `RELATED` or `NOT_RELATED` + brief reasoning about why.

### Section file accumulation

For each `RELATED` match, the script appends to the section file:

```markdown
## Related Files

### path/to/file.py
- Relevance: <GLM's reasoning for why this file relates>
```

The section file becomes the single source of truth — it contains the
verbatim proposal text, the YAML summary, and all related file matches.
A file can appear in multiple section files. That's expected.

### Deep scan (GLM per hit)

After the quick scan populates Related Files, a second pass reads the
full source file + full section for each hit. GLM writes detailed notes:

- WHY this file relates to the section
- WHICH parts of the file are affected (functions, classes, regions)
- Confidence: high | medium | low
- Open questions: what GLM isn't sure about

The script updates the match entry in the section file:

```markdown
### path/to/file.py
- Relevance: <GLM's reasoning>
- Affected areas: <functions, classes, or regions>
- Confidence: high | medium | low
- Open questions: <what GLM isn't sure about>
```

### Resume support

The script skips file × section pairs that already have an entry in
the section file's Related Files block.

## Section-at-a-Time Execution

### Scripts and templates

| File | Purpose |
|------|---------|
| `$WORKFLOW_HOME/scripts/section-loop.py` | Section-loop template (adapt per task) |
| `$WORKFLOW_HOME/scripts/task-agent-prompt.md` | Task agent prompt template |
| `$WORKFLOW_HOME/scripts/mailbox.sh` | File-based mailbox system |

### Launching task agents

The UI orchestrator:
1. Copies the task-agent prompt template
2. Fills in `{{PLANSPACE}}`, `{{CODESPACE}}`, `{{TAG}}`, etc.
3. Writes the filled prompt to `<planspace>/artifacts/task-agent-prompt.md`
4. Launches via: `uv run agents --model claude-opus --file <planspace>/artifacts/task-agent-prompt.md`
5. Runs `recv` on its own mailbox to receive reports from the task agent

The task agent then owns the section-loop lifecycle:

```bash
python3 "$WORKFLOW_HOME/scripts/section-loop.py" <planspace> <codespace> <tag> <agent-name>
```

The script runs as a **background task** under a **task agent**. The task
agent is launched via `uv run agents` and is responsible for:
- Starting the section-loop script as a background subprocess
- Monitoring status mail from the script via mailbox recv
- Detecting stuck states (repeated MISALIGNED, stalled progress, crashes)
- Reporting progress and problems to the UI orchestrator
- Fixing issues autonomously when possible

The UI orchestrator does NOT directly launch or monitor section-loop
scripts. It spawns task agents and receives their reports.

### Communication model (3 layers, all mailbox)

```
UI Orchestrator (talks to user, high-level decisions)
  ├─ recv on orchestrator mailbox (listens for task agent reports)
  └─ Task Agent × N (one per task, via uv run agents)
       ├─ recv on task-agent mailbox (listens for section-loop status)
       ├─ send to orchestrator mailbox (reports progress + problems)
       └─ section-loop.py (background subprocess)
            ├─ send to task-agent mailbox (status events)
            ├─ recv on section-loop mailbox (when paused)
            └─ agents (sequential uv run agents calls)
```

All communication uses the file-based mailbox system. No team/SendMessage
infrastructure — task agents are standalone processes launched via
`uv run agents`, not Claude teammates.

**UI Orchestrator**: Launches task agents via `uv run agents --file`,
runs recv on its own mailbox, receives reports, makes decisions,
communicates with user. Does NOT directly launch or monitor section-loop
scripts.

**Task Agent**: Intelligent monitor launched via `uv run agents`. The
prompt file specifies planspace, codespace, tag, and monitoring
instructions. The agent has full filesystem access to diagnose issues.

The task agent: launches the section-loop script, reads status mail via
mailbox recv, detects patterns (repeated identical MISALIGNED feedback,
stalled progress, crashes). Fixes what it can autonomously, sends
problem reports to the UI orchestrator via mailbox, and waits for
guidance on issues it can't resolve. Can be told to reload skills and
rewrite/restart scripts.

**Section-loop script**: Dumb executor. Runs sections sequentially,
dispatches agents, sends status mail for every significant event. Does
not make judgment calls about stuck states — that's the task agent's job.

### Mail protocols

**Section-loop → Task Agent** (status events):

| Message | Meaning |
|---------|---------|
| `status:section-start:<num>` | Starting work on a section |
| `status:solve:<num>` | Running Opus solution |
| `status:plan:<num>:<file>` | Planning a file |
| `status:fix plan:<num>:<file>` | Re-planning after MISALIGNED |
| `status:impl:<num>:<file>` | Implementing a file |
| `status:fix impl:<num>:<file>` | Re-implementing after MISALIGNED |
| `status:align:<num>:attempt-N` | Running alignment check |
| `status:align:<num>:ALIGNED` | Section passed alignment |
| `status:align:<num>:MISALIGNED-attempt-N:<feedback>` | Failed alignment |
| `done:<num>:<count> files modified` | Section complete |
| `status:complete` | All sections done |
| `pause:<signal>:<num>:<detail>` | Script paused, needs parent input |

**Task Agent → UI Orchestrator** (progress reports + escalations):

| Message | Meaning |
|---------|---------|
| `progress:<task>:<num>:ALIGNED` | Section completed successfully |
| `progress:<task>:complete` | All sections done |
| `problem:stuck:<task>:<num>:<diagnosis>` | Stuck state detected |
| `problem:crash:<task>:<detail>` | Script crashed |
| `problem:escalate:<task>:<detail>` | Issue needs human input |

**Two signal routes per background task:**
1. Task completion — the background process exits (done or error)
2. Mailbox message — the process sends a signal while still running

The parent always has a `recv` running as a background task so it is
always listening. When `recv` completes (message arrived), process it,
then immediately start another `recv`.

### Signal protocol

**section-loop → parent:**
- `pause:underspec:<section>:<description>` — needs research/proposal
- `pause:need_decision:<section>:<question>` — needs human answer
- `pause:dependency:<section>:<needed_section>` — needs another section first
- `done:<section>:<summary>` — section completed
- `fail:<section>:<error>` — section failed
- `complete` — all sections done

**parent → section-loop:**
- `resume:<payload>` — continue (answer or context attached)
- `abort` — clean shutdown
- `alignment_changed` — user input changed alignment docs, re-evaluate

### Pause/resume flow

When an agent signals underspecification, dependency, or needs a decision:

1. section-loop sends `pause:*` to parent mailbox
2. section-loop blocks on its own `recv` (waiting for response)
3. Parent's `recv` fires, parent reads the signal
4. Parent handles it:
   - `underspec` → trigger research/evaluate cycle, or ask user
   - `need_decision` → present question to user, collect answer
   - `dependency` → reorder queue, implement dependency first
5. Parent sends `resume:<answer>` to section-loop's mailbox
6. section-loop's `recv` fires, reads answer, continues

If the parent is the orchestrator (not interactive session), it may need
to bubble the signal up further — send its own `pause` to its parent
(the interactive session) and block on its own `recv`.

### User input cascade

When the user answers a tradeoff/constraint question, their answer may
change alignment documentation or design constraints. This cascades:

1. User provides answer → alignment docs updated
2. Parent sends `alignment_changed` to section-loop's mailbox
3. section-loop marks ALL completed sections dirty (back in queue)
4. When dirty sections re-run, they re-solve with updated context
5. Changed solutions cascade to new plans and implementations

The cascade is intentionally coarse-grained: any alignment change
reschedules everything. Fine-grained invalidation would be fragile.

### Per-section flow

```
For each section in queue:
  Check for pending messages (abort, alignment_changed)
  Stage 4: Solution (Opus) — direction for the section
    → if agent signals: pause, wait for parent, resume
  Stage 5: For each file in section:
    Plan (Codex) — what to change in this file
      → if agent signals: pause, wait for parent, resume
    Implement (Codex) — make the changes, update docstring
      → if agent signals: pause, wait for parent, resume
  Alignment check (Opus) — do implementations match the solution?
    → ALIGNED: continue
    → MISALIGNED: patch plan/impl (back to Stage 5, solution unchanged)
    → UNDERSPECIFIED: pause, wait for parent, resume
  Send done:<section> to parent
  Rescheduling check → mark other sections dirty if their files changed
```

### Queue management

1. All sections start in the queue (ordered by dependency if known)
2. Pop one section, run it through the per-section flow
3. If implementing this section modifies files in other sections,
   reschedule those sections (mark dirty, add back to queue)
4. Pop next section from queue
5. Queue empty = all sections clean → send `complete` to parent

### Alignment check (exploratory)

After all files in a section have been plan+implemented, Opus reads:
- The section specification (the requirements)
- The solution doc (the source of truth)
- Known implemented files (from section file list + Codex modified-file reports)

Opus checks **go beyond the listed files**. The section spec may require
creating new files, modifying files not in the original list, or producing
artifacts at specific worktree paths. Opus verifies the worktree for any
file the section mentions should exist — not just what's enumerated.

Specific checks:
- Do the implementations match the solution direction?
- Are the file changes internally consistent?
- Did any implementation drift from what the solution specified?
- Do all files that should exist (per the section spec) actually exist?

The solution is NEVER modified by alignment. If issues found → Codex
gets the alignment feedback and patches the existing plans and
implementations in place to match the solution. Not a rewrite — a fix.

The solution only gets updated during **rescheduling** — when other
sections' implementations change files that this section depends on.

### Rescheduling

**Triggers** (during Stage 5):
- Direct edits to files that appear in another section's relevance map
- The implementing agent reports which files it modified

**When a rescheduled section starts its turn**:
1. Opus re-reads the section + all related files (with accumulated
   changes from other sections' implementations)
2. Writes a new solution doc accounting for the changes
3. Proceeds through 4 → 5 as normal

**Rescheduling is at the section level.** Changes accumulate before
re-alignment. When Opus re-solves a rescheduled section, it sees all
changes at once.

If a section keeps getting rescheduled, escalate — possible circular
dependency needing restructuring.

### Cleanup

section-loop.py cleans up its own mailbox on exit (normal completion,
abort, or error). The `finally` block in `main()` ensures cleanup
runs even on exceptions. The parent should also verify cleanup after
the background task exits.

## Stage 4: Solution (Opus per section)

**Per-section** — one Opus run per section in the queue.

Opus is given filepaths to read (per Prompt Construction Rules):
- The section file path
- The related source file paths (from relevance map)
- The existing solution path (if rescheduled)

Opus writes a solution doc to
`<planspace>/artifacts/solutions/section-NN-solution.md`:
- How to approach the changes for this section
- Per-file: what needs to change and why
- Constraints and risks
- Resolution of GLM's open questions (Opus determines if the file is
  truly related and what the uncertainty means)
- Cross-section dependencies (which other sections' files are affected)

NOT detailed code changes — setting direction for the planner.

## Stage 5: Plan + Implement (Codex per file)

**Per-section** — for each related file in the current section, Codex
plans then implements, one file at a time.

### Per-file flow

For each file in the section's relevance map:

**Plan**: Codex is given filepaths to the solution doc, section file, and
source file (it reads them itself). Writes a change plan to
`<planspace>/artifacts/plans/section-NN/<filename>-plan.md`:
- Specific changes needed in this file
- Interface contracts (function signatures, types)
- Control flow changes
- Error handling changes
- Integration points (what calls this, what this calls)

**Implement**: Codex is given filepaths to the change plan and solution doc
(it reads them itself). Source file path is provided for editing.
- Implements code changes in the file
- Updates the module docstring to reflect the changes
- Reports which files it modified

Plans are external artifacts — no markers placed in source code.

### Cross-section rescheduling

The implementing agent reports which files it modified. The orchestrator
checks if any modified files appear in other sections' relevance maps.
If so, those sections are rescheduled (marked dirty, added back to queue).

Changes accumulate — the rescheduled section re-solves when its turn comes.

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

Capture before Stage 5 (in the task worktree):
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
  → parent handles the signal (research cycle, ask user, reorder queue)
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
   the section queue (same decomposition pipeline as Stage 1)
5. **Resume**: send `resume:researched` to section-loop's mailbox
6. section-loop re-solves the section with updated context
7. New sections from the sub-proposal run through the queue normally
8. Original section is rescheduled to pick up changes

### Case 2: Dependency on another section in the queue

Agent signals: `DEPENDENCY: <which section and why>`
section-loop sends: `pause:dependency:<section>:<needed_section>`

The parent handles:
1. **Reorder**: push the dependency section to the front of the queue
2. **Resume**: send `resume:reordered` to section-loop's mailbox
3. section-loop exits the current section, processes the dependency
   section next, then reschedules the original

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

The agent does NOT signal — it notes in the solution doc referencing
the existing code and continues. No pause needed.

If the target is in another section, that section is rescheduled
when the current section's modified files overlap.

## Other Escape Hatches

**Mutual dependency (same section)** → Implement files back-to-back.
Test as a unit before moving on.

**Cross-section dependency** → Rescheduling handles it. Changes accumulate
and re-solve when that section's turn comes.

## Model Roles

| Stage | Model | Role |
|-------|-------|------|
| 1: Decomposition | Opus | Recursive section identification + materialization |
| 1C: Section Summaries | GLM | YAML frontmatter per section file |
| 2: Docstrings | GLM | Add/update module docstrings per file |
| 3: Quick Scan | GLM | Per file×section: docstring vs section summary match |
| 3: Deep Scan | GLM | Per hit: full file + full section detailed relevance |
| 3: Tool Creation | Opus | Write extraction tool if file extension unrecognized |
| 4: Solution | Opus | Per-section solution direction from related files |
| 5: Plan | Codex | Per-file change plans from solution doc |
| 5: Implement | Codex | Code changes + docstring updates |
| 5: Alignment | Opus | Per-section coherence check after all files done |
| 6a: Constraint Audit | Codex-high2 | Design principle check |
| 6d: Debug/RCA | Codex-high | Fix test failures |

## Anti-Patterns

- **DO NOT edit source files yourself** — delegate ALL editing to agents
- **DO NOT place markers in source code** — relevance and plans are external artifacts
- **DO NOT skip the docstring stage** — it's the scan infrastructure
- **DO NOT combine multiple files into one agent call**
- **DO NOT put detailed changes in solution docs** — Opus sets direction only
- **DO NOT solve underspecified problems in-place** — stop the section, trigger a research/evaluate cycle, decompose the sub-proposal into new sections, and reschedule
- **DO NOT work around section dependencies** — if section A needs section B, push B to the front and implement it first. Do not guess or stub the dependency
- **DO NOT reschedule individual files** — reschedule sections, let changes accumulate
- **DO NOT skip alignment check** — verify section coherence before moving on
- **DO NOT skip tests** — verify before moving to next section
- **DO NOT skip constraint audit** — verify before committing
