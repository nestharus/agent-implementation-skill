# Refactoring Phase 14: Decompose coordination/runner.py and main.py

You are orchestrating the decomposition of the remaining large orchestrator files.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize. In particular, `src/scripts/section_loop/section_engine/runner.py` is being modified by another agent right now — do NOT touch that file.

## Completed So Far

### lib/ modules (54+ total, all wired)
### Test counts: 1111+ passed

## Phase 14 Tasks

### Task 1: Extract ProjectModeResolver from main.py

Read `src/scripts/section_loop/main.py` lines 134-212. This handles project mode detection — reading JSON/text fallbacks, pausing for parent, writing mode contract.

Create `src/scripts/lib/project_mode.py`:
- `resolve_project_mode(planspace, parent) -> tuple[str, list[str]]` — returns (mode, constraints)
  Handles JSON/text fallback, fail-closed pause, post-resume re-read.
- `write_mode_contract(planspace, mode, constraints)` — writes the mode contract JSON

Wire into main.py.

Write tests at `tests/component/test_project_mode.py`.

### Task 2: Extract SectionLoader from main.py

Read `src/scripts/section_loop/main.py` lines 52-79. `parse_related_files` and `load_sections` are pure file-reading utilities.

Create `src/scripts/lib/section_loader.py`:
- Move `parse_related_files(section_path) -> list[str]`
- Move `load_sections(sections_dir) -> list[Section]`

Wire into main.py and any files importing from main.

Write tests at `tests/component/test_section_loader.py`.

### Task 3: Extract ProposalPassOrchestrator from main.py

Read `src/scripts/section_loop/main.py` lines 234-380 (Phase 1a: Proposal pass). This iterates through sections, running each through proposal mode, handling re-exploration, collecting results.

Create `src/scripts/lib/proposal_pass.py`:
- `run_proposal_pass(all_sections, sections_by_num, planspace, codespace, parent, policy) -> dict[str, ProposalPassResult]`

Wire into main.py.

Write tests at `tests/component/test_proposal_pass.py`.

### Task 4: Extract ImplementationPassOrchestrator from main.py

Read `src/scripts/section_loop/main.py` lines 518-636 (Phase 1c: Implementation pass). This iterates through execution-ready sections, running each through implementation mode.

Create `src/scripts/lib/implementation_pass.py`:
- `run_implementation_pass(proposal_results, sections_by_num, planspace, codespace, parent) -> dict[str, SectionResult]`

Wire into main.py.

Write tests at `tests/component/test_implementation_pass.py`.

### Task 5: Extract CoordinationSteps from coordination/runner.py

Read `src/scripts/section_loop/coordination/runner.py`. The `run_global_coordination` function has 4 steps:
1. Collect outstanding problems (lines ~77-108)
2. Aggregate scope deltas + dispatch adjudication (lines ~109-364)
3. Dispatch coordination-planner + execute plan (lines ~365-783)
4. Re-run per-section alignment (lines ~784-end)

Extract Step 2 (scope delta aggregation) which is the largest self-contained block:

Create `src/scripts/lib/scope_delta_aggregator.py`:
- `aggregate_scope_deltas(planspace, parent, policy) -> list[dict]` — collects scope deltas, dispatches adjudication agent, returns adjudicated results
- Any parsing/validation helpers for scope delta adjudication

Wire into coordination/runner.py.

Write tests at `tests/component/test_scope_delta_aggregator.py`.

### Task 6: Extract CoordinationExecutor from coordination/runner.py

Read `src/scripts/section_loop/coordination/runner.py` Step 3 (lines ~446-783). This executes the coordination plan — dispatching fix-group agents, handling bridge candidates, processing results.

Create `src/scripts/lib/coordination_executor.py`:
- `execute_coordination_plan(plan, sections_by_num, planspace, codespace, parent, policy) -> list[str]` — returns list of affected section numbers

Wire into coordination/runner.py.

Write tests at `tests/component/test_coordination_executor.py`.

## Process for each extraction

1. Read the relevant code
2. Extract into a new lib/ module
3. Write component tests
4. Replace inline code with function call
5. Run `uv run pytest tests/ -q --tb=short` — must pass

## Rules

- Do NOT change any behavior
- Do NOT touch section_engine/runner.py (another agent is working on it)
- Test after EVERY extraction
- main.py and coordination/runner.py should become thin orchestrators

## Verification

After ALL tasks, run:
```bash
uv run pytest tests/ -q --tb=short
```

Write a summary to `.tmp/refactor/phase14-results.md` including final line counts.
