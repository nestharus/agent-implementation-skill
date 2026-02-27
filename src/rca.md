# Agentic RCA: Iterative Root Cause Analysis + Fix

Fix test failures using sequential investigate → plan → fix → verify waves
until all tests pass.

**Key principle**: Codex investigates and reports. An Opus sub-agent
independently plans the correct fix (which may differ from Codex's
suggestion). Tests get rerun. Repeat for remaining failures.

## Prerequisites

- Failing tests with reproduction command
- Clean git state on the working branch

## Setup

### Create RCA Worktree

A single worktree for Codex to investigate in. Codex only reads and
reports — it does not modify files. Fixes are applied to the working
branch, not the worktree.

```bash
git worktree add .worktrees/rca-<issue-slug> -b rca/<issue-slug>
mkdir -p .worktrees/rca-<issue-slug>/.tmp
```

### Run All Tests — Capture Baseline

```bash
cd .worktrees/rca-<issue-slug>
uv run pytest <test-path> -v -p no:randomly --timeout=120 2>&1 | tail -80
```

Record which tests fail. This is wave 0.

## Wave Loop

Repeat until all tests pass. Each wave targets one failing test file.

### Step 1: RCA Report (Codex — report only, no code changes)

Write a prompt at `.worktrees/rca-<issue-slug>/.tmp/rca-wave-N.md`:

```markdown
# Root Cause Analysis: <failing test file>

You are an **investigator**. You produce a report. You do NOT modify any files.

## Failing Test(s)
<test file path>

## Reproduction
<exact pytest command>

## Your Task
1. Run the failing tests and capture the full error output
2. Read the test code to understand what it expects
3. Read the source code the tests exercise to understand the current API
4. Trace each failure to its root cause — don't stop at the symptom

## Output: Write rca-report-wave-N.md in the .tmp directory

### Symptom
What the failure looks like (error message, stack trace).

### Root Cause
The actual underlying issue — what changed, what assumption was violated.

### Files Involved
Which source and test files are relevant.

### Suggested Fix Direction
Your recommendation — but do NOT implement it.
```

Run from within the worktree:

```bash
cd .worktrees/rca-<issue-slug>
uv run agents --model gpt-codex-high --file .tmp/rca-wave-N.md
```

**Critical**: The prompt says "do NOT modify any files." Codex produces a
report only.

### Step 2: Plan + Apply Fix (Opus sub-agent)

Spawn an Opus sub-agent per wave. The sub-agent:

1. **Reads Codex's RCA report** to understand what Codex found
2. **Reads the source code independently** — does NOT trust Codex's
   suggestion blindly
3. **Plans the correct fix** — considers whether Codex's suggestion is a
   band-aid or the right approach. May reject Codex's suggestion entirely.
4. **Explains its reasoning** before applying (brief plan summary)
5. **Applies the fix to the working branch** (not the worktree)

```python
Task(
    subagent_type="general-purpose",
    model="opus",
    mode="bypassPermissions",
    prompt="""You are an architect fixing test failures on the working branch.

## Context
A Codex agent investigated failures and wrote an RCA report at:
`.worktrees/rca-<slug>/.tmp/rca-report-wave-N.md`

The report describes what Codex found. Your job is to understand the
problem independently and plan the correct fix.

## Failing Test
<test file and test name>

## Your Process

### 1. Understand the Problem
- Read Codex's RCA report
- Read the test file ON THE WORKING BRANCH (not the worktree)
- Read the source code the test depends on

### 2. Plan the Correct Fix
Think about:
- Is Codex's diagnosis correct?
- Is the test stale or is the source wrong?
- What is the architecturally correct fix?
- Should the test be updated, deleted, or should source be fixed?
Write your plan as a brief summary before applying.

### 3. Apply Your Planned Fix
Edit files on the working branch at: <working-branch-path>
"""
)
```

**Why a sub-agent?** The sub-agent reads the report, reads the source, and
makes an independent judgment. This prevents the orchestrator from
rubber-stamping Codex's work or taking shortcuts.

**Why Opus?** Planning requires architectural judgment — understanding
whether a fix is a band-aid or the correct approach. Codex finds facts;
Opus makes design decisions.

### Step 3: Verify (Rerun Tests)

Run all tests on the working branch (not the worktree):

```bash
uv run pytest <test-path> -v -p no:randomly --timeout=120 2>&1 | tail -80
```

Compare against previous wave:
- **Fewer failures** → progress. Next wave.
- **Same failures** → fix didn't work. Re-examine.
- **New failures** → fix introduced regressions. Revert and re-plan.
- **Zero failures** → done. Proceed to cleanup.

### Step 4: Next Wave

If failures remain, return to Step 1 targeting the next failing test file.
Increment wave number.

## Cleanup

After all tests pass:

```bash
git worktree remove .worktrees/rca-<issue-slug>
git branch -D rca/<issue-slug>
```

## Model Roles

| Step | Model | Role |
|------|-------|------|
| 1: RCA | Codex-high | Investigate + report (no code changes) |
| 2: Plan + Fix | Opus sub-agent | Read report, plan correct fix, apply to working branch |
| 3: Verify | (automated) | pytest on working branch |

## Anti-Patterns

- **DO NOT let Codex modify files** — it reports, Opus fixes
- **DO NOT diagnose in the prompt** — describe symptoms, let Codex investigate
- **DO NOT rubber-stamp Codex** — the Opus sub-agent must read source independently
- **DO NOT apply fixes in the worktree** — fixes go on the working branch
- **DO NOT batch all failures into one agent call** — one test file per wave
- **DO NOT skip verification between waves** — fixes can introduce regressions
- **DO NOT over-engineer** — if the test just needs deleting, delete it
