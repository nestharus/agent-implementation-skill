# Audit Cycle: Handling New Responses

When the user says **"New response up. Assess, implement, commit, push, update audit history, rezip etc."**, execute the full Round N cycle below.

---

## Step 1: Read Inputs

Read both files in parallel:

- `/tmp/execution-philosophy/response.md` — the new audit response
- `/tmp/execution-philosophy/audit-history.md` — full cycle history for cycle detection

---

## Step 2: Assess Violations

For each violation in the response, check audit history:

- **Cycle** — the exact fix was already tried in a prior round and reverted or dismissed. Do NOT reimplement. Note the round numbers.
- **False positive** — the "violation" describes behavior that is intentional by design (e.g. tests excluded from audit bundle, interchangeable model names in docs). Dismiss with rationale.
- **New surface** — a real violation at a code location not previously addressed. **Implement it.**

Document your cycle/dismiss decisions clearly before writing any code.

---

## Step 3: Implement

For each non-dismissed violation, make the minimal code change that resolves it.

Key patterns to apply (established by prior rounds):

- **Corruption-preservation**: malformed JSON → rename to `.malformed.json` (best-effort) + warn log, then proceed fail-closed
- **Fail-closed**: on parse failure, default to the conservative/safe behavior (e.g. `rebuild=True`, `friction=True`, fall through to full processing)
- **No script heuristics**: scripts parse structured signals only; all semantic decisions go through agents
- **Policy-driven models**: no hardcoded model strings in dispatch callsites or prompt text

After editing source files, add regression guard tests in `tests/test_regression_guards.py`. Use source-inspection style (read file, assert pattern present near landmark). Match the style of existing tests in that file.

---

## Step 4: Run Tests

```bash
cd /home/nes/projects/agent-implementation-skill
uv run python -m pytest tests/test_regression_guards.py -x -q
```

Fix any failures before proceeding.

---

## Step 5: Commit and Push

```bash
cd /home/nes/projects/agent-implementation-skill
git add -A
git commit -m "feat(audit): Round N — <summary of violations>"
git push
```

Note the commit SHA for the audit history entry.

---

## Step 6: Update Audit History

Edit `/tmp/execution-philosophy/audit-history.md`:

1. **Per-round index table** — add a row: `| N | <sha7> | <test count> | <violation count> | <one-line summary> |`
2. **Active Concern Threads** — update any threads that this round progressed (add a "What was done in RN" entry)
3. **Round Details** — append a new `#### Round N — Commit <sha>` section with one paragraph per violation describing the change and files modified

---

## Step 7: Rezip Codebase

Delete the old zip and recreate from `src/`:

```bash
cd /home/nes/projects/agent-implementation-skill
rm -f /tmp/execution-philosophy/codebase.zip
cd src && zip -r /tmp/execution-philosophy/codebase.zip . && cd ..
```

Verify the zip contains the expected file count and no dev artifacts (`pyproject.toml`, `tests/`, `lint-*.sh` are excluded — they live outside `src/`).

---

## Cycle Detection Reference

Common dismissal patterns from history:

- **Tests / pyproject.toml absent from zip** — settled in R46/R47: audit bundle = deployed layout (`src/`), tests in dev repo. Not a violation.
- **Model names in `models.md`** — `gpt-5.3-codex-high` and `high2` are documented as interchangeable quota pools. Listing both is correct.
- **Any violation in a "Settled Concerns" section** of audit-history.md — already guarded by tests; re-raising is a cycle.

---

## Files

| Path | Purpose |
|------|---------|
| `/tmp/execution-philosophy/response.md` | Current round's audit response (written by external model) |
| `/tmp/execution-philosophy/audit-history.md` | Cumulative cycle history — use for cycle detection and updating |
| `/tmp/execution-philosophy/codebase.zip` | Deployed skill snapshot sent to external model for auditing |
| `tests/test_regression_guards.py` | All regression guard tests — append new tests here |
| `src/` | Deployed skill content — the only thing that goes in the zip |
