# Refactoring Phase 11: Scan Services + Substrate + Remaining Extractions

You are orchestrating the next phase of a bottom-up refactoring of the agent-implementation-skill codebase.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize.

## Completed So Far

### lib/ modules (44 total, all wired)
### Test counts: 1066 passed

## Phase 11 Tasks

### Task 1: Extract ScanRelatedFiles from scan/exploration.py

Read `src/scripts/scan/exploration.py`. Find the related files validation logic (`_validate_existing_related_files`), the section file listing, and the update application logic.

Create `src/scripts/lib/scan_related_files.py`:
- Move `list_section_files`
- Move `apply_related_files_update`
- Move `_validate_existing_related_files`
- Move `_sha256_file` (should use `hash_service.file_hash`)

Wire into `exploration.py`.

Write tests at `tests/component/test_scan_related_files.py`.

### Task 2: Extract ScanFeedbackRouter from scan/feedback.py

Read `src/scripts/scan/feedback.py`. Find the feedback validation, routing, and application logic.

Create `src/scripts/lib/scan_feedback_router.py`:
- Move `_is_valid_updater_signal`
- Move `_validate_feedback_schema`
- Move `_extract_section_number`
- Move `_append_to_log`
- Move `_route_scope_deltas`

Wire into `feedback.py`.

Write tests at `tests/component/test_scan_feedback_router.py`.

### Task 3: Extract SubstratePromptBuilder from substrate/prompts.py

Read `src/scripts/substrate/prompts.py`. This module builds prompts for substrate agents.

Create `src/scripts/lib/substrate_prompt_builder.py`:
- Move the prompt construction logic

Wire into `substrate/prompts.py`.

Write tests at `tests/component/test_substrate_prompt_builder.py`.

### Task 4: Extract CoordinationPlanner from coordination/planning.py

Read `src/scripts/section_loop/coordination/planning.py`. Extract the coordination planning logic.

Create `src/scripts/lib/coordination_planner.py`:
- Move the planning functions

Wire into `coordination/planning.py`.

Write tests at `tests/component/test_coordination_planner.py`.

### Task 5: Extract CoordinationProblemResolver from coordination/problems.py

Read `src/scripts/section_loop/coordination/problems.py`. Extract the problem resolution logic.

Create `src/scripts/lib/coordination_problem_resolver.py`:
- Move problem resolution and problem dispatch functions

Wire into `coordination/problems.py`.

Write tests at `tests/component/test_coordination_problem_resolver.py`.

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
- If the code is too tightly coupled to extract cleanly, note it and skip

## Verification

After ALL tasks, run:
```bash
uv run pytest tests/ -q --tb=short
```

Write a summary to `.tmp/refactor/phase11-results.md`.
