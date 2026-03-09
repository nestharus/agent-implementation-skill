# Refactoring Phase 9: Section Engine + Task Flow Decomposition

You are orchestrating the next phase of a bottom-up refactoring of the agent-implementation-skill codebase.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize.

## Completed So Far

### lib/ modules (34 total, all wired)
### Test counts: 1035 passed

## Phase 9 Tasks

### Task 1: Extract ToolSurfaceManager from section_engine/runner.py

Read `src/scripts/section_loop/section_engine/runner.py`. Find the `_write_tool_surface` function and any tool registry loading/filtering/repair logic within `run_section`.

Create `src/scripts/lib/tool_surface.py`:
- Move `_write_tool_surface` as `write_tool_surface`
- Move any tool registry repair logic
- Move tool friction handling if found

Wire into `runner.py`.

Write tests at `tests/component/test_tool_surface.py`.

### Task 2: Extract ScopeDeltaParser from coordination/runner.py

Read `src/scripts/section_loop/coordination/runner.py`. Find `_parse_scope_delta_adjudication` and `_normalize_section_id`.

Create `src/scripts/lib/scope_delta_parser.py`:
- Move `_parse_scope_delta_adjudication` as `parse_scope_delta_adjudication`
- Move `_normalize_section_id` as `normalize_section_id`

Wire into `coordination/runner.py`.

Write tests at `tests/component/test_scope_delta_parser.py`.

### Task 3: Extract FlowContextService from task_flow.py

Read `src/scripts/task_flow.py`. Find the flow context building/writing functions:
- `build_flow_context`
- `write_dispatch_prompt`
- `_write_flow_context`
- The relpath helpers (`_flow_context_relpath`, `_continuation_relpath`, etc.)

Create `src/scripts/lib/flow_context.py`:
- Move the flow context building and writing functions
- Move the relpath helpers

Wire into `task_flow.py`.

Write tests at `tests/component/test_flow_context.py`.

### Task 4: Extract FlowSubmitter from task_flow.py

Read `src/scripts/task_flow.py`. Find `submit_chain` and `submit_fanout`.

Create `src/scripts/lib/flow_submitter.py`:
- Move `submit_chain` and `submit_fanout`
- Move any ID generation helpers they use (`_new_instance_id`, `_new_flow_id`, etc.)

Wire into `task_flow.py`.

Write tests at `tests/component/test_flow_submitter.py`.

### Task 5: Extract FlowReconciler from task_flow.py

Read `src/scripts/task_flow.py`. Find `reconcile_task_completion` and its helpers:
- `build_result_manifest`
- `build_gate_aggregate_manifest`
- `_read_origin_refs`
- `_find_gate_for_chain`
- Gate member update functions
- `_cancel_chain_descendants`
- `_check_and_fire_gate`

Create `src/scripts/lib/flow_reconciler.py`:
- Move all completion reconciliation logic

Wire into `task_flow.py`.

Write tests at `tests/component/test_flow_reconciler.py`.

## Process for each extraction

1. Read the source file(s) thoroughly
2. Identify the concern boundary
3. Create the new module in `src/scripts/lib/`
4. Write component tests in `tests/component/`
5. Update the original file to import from the new module
6. Update any other files that imported from the original
7. Run `uv run pytest tests/ -q --tb=short` — must pass at each step

## Rules

- Do NOT change any behavior
- Test after EVERY change
- Write component tests for every new module
- Do not create abstractions that are more complex than the code they replace

## Verification

After ALL tasks, run:
```bash
uv run pytest tests/ -q --tb=short
```

Write a summary to `.tmp/refactor/phase9-results.md`.
