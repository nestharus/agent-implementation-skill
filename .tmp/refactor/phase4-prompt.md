# Refactoring Phase 4: PathRegistry Wiring + Pipeline Control Extraction

You are orchestrating the next phase of a bottom-up refactoring of the agent-implementation-skill codebase.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize.

## Completed So Far

### lib/ components (all created, all wired except PathRegistry):
- `artifact_io.py` — JSON read/write ✓ WIRED
- `hash_service.py` — Canonical hashing ✓ WIRED
- `path_registry.py` — Path construction (created, **NOT wired**)
- `verdict_parsers.py` — Alignment verdict parsing ✓ WIRED
- `database_client.py` — db.sh wrapper ✓ WIRED
- `mailbox_service.py` — Mailbox lifecycle ✓ WIRED
- `monitor_service.py` — Monitor lifecycle ✓ WIRED
- `model_policy.py` — ModelPolicy dataclass ✓ WIRED
- `signal_reader.py` — Signal file reading ✓ WIRED
- `dispatch_metadata.py` — .meta.json sidecar ✓ WIRED
- `context_sidecar.py` — Agent context materialization ✓ WIRED
- `prompt_template.py` — Template loading/rendering ✓ WIRED

## Phase 4 Tasks

### Task 1: Wire PathRegistry into section_loop/ modules

`src/scripts/lib/path_registry.py` provides a `PathRegistry` class initialized with `planspace: Path`. It replaces `planspace / "artifacts" / ...` constructions with typed accessors.

**Strategy**: For each file, replace `planspace / "artifacts" / <path>` constructions with PathRegistry method calls. Where a function receives `planspace: Path`, create a local `paths = PathRegistry(planspace)` at the top of the function and use its methods.

**IMPORTANT**: The PathRegistry may not have methods for every path pattern you find. If you encounter a path like `planspace / "artifacts" / "foo" / "bar"` that has no corresponding PathRegistry method, you have two options:
1. Add the method to `path_registry.py` if it follows an obvious pattern
2. Leave it as-is if it's a one-off path

Read `src/scripts/lib/path_registry.py` first to understand all available methods.

Process these files (highest usage first):
- `src/scripts/section_loop/main.py` (13 occurrences)
- `src/scripts/section_loop/coordination/runner.py` (11 occurrences)
- `src/scripts/section_loop/coordination/execution.py` (7 occurrences)
- `src/scripts/section_loop/intent/expansion.py` (7 occurrences)
- `src/scripts/section_loop/pipeline_control.py` (6 occurrences)
- `src/scripts/section_loop/section_engine/runner.py` (6 occurrences)
- `src/scripts/section_loop/cross_section.py` (6 occurrences)
- `src/scripts/section_loop/coordination/problems.py` (5 occurrences)
- `src/scripts/section_loop/prompts/writers.py` (5 occurrences)
- `src/scripts/section_loop/section_engine/blockers.py` (4 occurrences)
- `src/scripts/section_loop/coordination/planning.py` (4 occurrences)
- `src/scripts/section_loop/section_engine/reexplore.py` (3 occurrences)
- `src/scripts/section_loop/section_engine/todos.py` (3 occurrences)
- `src/scripts/section_loop/intent/surfaces.py` (3 occurrences)
- `src/scripts/section_loop/alignment.py` (3 occurrences)
- `src/scripts/section_loop/dispatch.py` (3 occurrences)
- `src/scripts/section_loop/section_engine/traceability.py` (2 occurrences)
- `src/scripts/section_loop/intent/bootstrap.py` (2 occurrences)
- `src/scripts/section_loop/intent/triage.py` (2 occurrences)
- `src/scripts/section_loop/reconciliation.py` (2 occurrences)
- `src/scripts/section_loop/decisions.py` (1 occurrence)
- `src/scripts/section_loop/readiness.py` (1 occurrence)
- `src/scripts/section_loop/reconciliation_queue.py` (1 occurrence)
- `src/scripts/section_loop/communication.py` (1 occurrence)
- `src/scripts/section_loop/prompts/context.py` (1 occurrence)
- `src/scripts/section_loop/task_ingestion.py` (1 occurrence)

### Task 2: Wire PathRegistry into non-section-loop modules

Same strategy for files outside section_loop/:
- `src/scripts/task_flow.py` (3 occurrences)
- `src/scripts/qa_interceptor.py` (3 occurrences)
- `src/scripts/substrate/prompts.py` (3 occurrences)
- `src/scripts/substrate/related_files.py` (2 occurrences)
- `src/scripts/substrate/runner.py` (1 occurrence)
- `src/scripts/task_dispatcher.py` (1 occurrence)
- `src/scripts/flow_catalog.py` (1 occurrence)
- `src/scripts/scan/cli.py` (1 occurrence)
- `src/scripts/log_extract/cli.py` (1 occurrence)

Skip files already in `lib/` (they define paths, not consume them).

### Task 3: Extract PipelineStateService from pipeline_control.py

Read `src/scripts/section_loop/pipeline_control.py`. This file contains ~5 separate concerns:

1. **Pipeline state** — `check_pipeline_state`, `wait_if_paused`, `pause_for_parent`
2. **Alignment change tracking** — `_set_alignment_changed_flag`, `alignment_changed_pending`, `_check_and_clear_alignment_changed`, `_invalidate_excerpts`
3. **Section input hashing** — `_section_inputs_hash`, `coordination_recheck_hash`
4. **Requeue logic** — `requeue_changed_sections`
5. **Message polling** — `poll_control_messages`, `check_for_messages`, `handle_pending_messages`

For Phase 4, extract concern #3 only:

Create `src/scripts/lib/section_input_hasher.py`:
- Move `_section_inputs_hash` as a public function `section_inputs_hash`
- Move `coordination_recheck_hash`
- Both already use `lib.hash_service.content_hash`
- After PathRegistry wiring (Task 1), these should use PathRegistry methods
- Update `pipeline_control.py` to import from the new module

Write tests at `tests/component/test_section_input_hasher.py`.

## Process for each file

1. Read the file
2. Identify `planspace / "artifacts" / ...` patterns
3. Match each pattern to a PathRegistry method (or add one if needed)
4. Create `paths = PathRegistry(planspace)` at the top of the function
5. Replace ad-hoc paths with `paths.<method>(...)` calls
6. Add `from lib.path_registry import PathRegistry` import
7. Run `uv run pytest tests/ -q --tb=short` — must pass

## Rules

- Do NOT change any behavior
- Test after EVERY file change: `uv run pytest tests/ -q --tb=short`
- If a path pattern has no PathRegistry method, add it to path_registry.py
- Write component tests for the new section_input_hasher module

## Verification

After ALL tasks, run:
```bash
uv run pytest tests/ -q --tb=short
```

Write a summary to `.tmp/refactor/phase4-results.md`.
