# Refactoring Orchestrator: ArtifactIO Wiring

You are orchestrating a bottom-up refactoring of the agent-implementation-skill codebase.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize — they are from other parallel workers.

## Current State

Three foundational components have been created in `src/scripts/lib/`:
- `artifact_io.py` — JSON read/write with corruption preservation
- `hash_service.py` — Canonical SHA-256 hashing
- `path_registry.py` — Centralized artifact path construction

Three files have already been wired to use `artifact_io`:
- `src/scripts/section_loop/decisions.py` ✓
- `src/scripts/section_loop/reconciliation.py` ✓
- `src/scripts/section_loop/section_engine/runner.py` ✓

## Your Task

Wire `artifact_io` into ALL remaining source files that have JSON file I/O patterns. For each file:

1. Read the file
2. Find `json.loads(path.read_text(...))`, `path.write_text(json.dumps(...))`, and `.rename(*.malformed.json)` patterns
3. Replace with `from lib.artifact_io import read_json, write_json, rename_malformed` calls
4. Keep `json.loads()` for parsing in-memory strings (agent output, not files)
5. Remove `import json` only if no longer used
6. Run tests after each file: `uv run pytest tests/ -q --tb=short`

## Files to Process

Check each of these for JSON file I/O patterns:
- `src/scripts/section_loop/main.py`
- `src/scripts/section_loop/proposal_state.py`
- `src/scripts/section_loop/readiness.py`
- `src/scripts/section_loop/reconciliation_queue.py`
- `src/scripts/section_loop/cross_section.py`
- `src/scripts/section_loop/section_engine/todos.py`
- `src/scripts/section_loop/section_engine/reexplore.py`
- `src/scripts/section_loop/section_engine/blockers.py`
- `src/scripts/section_loop/section_engine/traceability.py`
- `src/scripts/section_loop/coordination/runner.py`
- `src/scripts/section_loop/coordination/execution.py`
- `src/scripts/section_loop/coordination/planning.py`
- `src/scripts/section_loop/coordination/problems.py`
- `src/scripts/section_loop/intent/bootstrap.py`
- `src/scripts/section_loop/intent/triage.py`
- `src/scripts/section_loop/intent/expansion.py`
- `src/scripts/section_loop/intent/surfaces.py`
- `src/scripts/section_loop/dispatch.py`
- `src/scripts/section_loop/alignment.py`
- `src/scripts/section_loop/prompts/writers.py`
- `src/scripts/section_loop/prompts/context.py`
- `src/scripts/section_loop/context_assembly.py`
- `src/scripts/task_flow.py`
- `src/scripts/task_dispatcher.py`
- `src/scripts/task_router.py`
- `src/scripts/flow_schema.py`
- `src/scripts/flow_catalog.py`
- `src/scripts/qa_interceptor.py`
- `src/scripts/scan/codemap.py`
- `src/scripts/scan/exploration.py`
- `src/scripts/scan/deep_scan.py`
- `src/scripts/scan/feedback.py`
- `src/scripts/scan/cache.py`
- `src/scripts/scan/related_files.py`
- `src/scripts/substrate/runner.py`
- `src/scripts/substrate/prompts.py`

## Rules

- Do NOT change any behavior
- Preserve all custom error handling, logging, return values
- Only replace FILE I/O JSON patterns, not in-memory string parsing
- If a file has no JSON file I/O patterns, skip it
- Test after EVERY file change

## Using Sub-Agents

You can use the `agents` binary to dispatch sub-agents for individual files:

```bash
agents --model gpt-high -p /home/nes/projects/agent-implementation-skill "Wire artifact_io into src/scripts/section_loop/main.py. Read the file, replace json.loads(path.read_text()) with read_json(), path.write_text(json.dumps()) with write_json(), and .rename(.malformed.json) with rename_malformed(). Add 'from lib.artifact_io import read_json, write_json' at top. Keep json.loads for in-memory strings. Run: uv run pytest tests/ -q --tb=short"
```

## Verification

After ALL files are done, run the full test suite:
```bash
uv run pytest tests/ -q --tb=short
```

All 843 tests must pass. Report which files were modified and which had no JSON patterns.
