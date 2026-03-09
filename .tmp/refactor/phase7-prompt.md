# Refactoring Phase 7: Cross-Section Services + QA + Task Dispatch Extractions

You are orchestrating the next phase of a bottom-up refactoring of the agent-implementation-skill codebase.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize.

## Completed So Far

### lib/ modules (24 total, all wired)

### Test counts: 994 passed

## Phase 7 Tasks

### Task 1: Extract SnapshotService from cross_section.py

Read `src/scripts/section_loop/cross_section.py`. Find the code that snapshots modified files to `artifacts/snapshots/section-NN/`. This is typically a directory copy/write operation.

Create `src/scripts/lib/snapshot_service.py`:
- `snapshot_modified_files(planspace, section_number, codespace, modified_files)` — copies modified files to snapshot directory
- `compute_text_diff(old_path, new_path) -> str` — unified diff between two files (pure function, already clean)

Wire into `cross_section.py`.

Write tests at `tests/component/test_snapshot_service.py`.

### Task 2: Extract QAVerdictParser from qa_interceptor.py

Read `src/scripts/qa_interceptor.py`. Find the verdict parsing logic that extracts structured verdicts from QA agent output.

Create `src/scripts/lib/qa_verdict_parser.py`:
- `parse_qa_verdict(output: str) -> tuple[str, str, list]` — (verdict, rationale, violations)
- Any supporting parsing helpers

Wire into `qa_interceptor.py`.

Write tests at `tests/component/test_qa_verdict_parser.py`.

### Task 3: Extract TaskParser from task_dispatcher.py

Read `src/scripts/task_dispatcher.py`. Find the task parsing logic that converts DB output into task dicts.

Create `src/scripts/lib/task_parser.py`:
- `parse_task_output(output: str) -> dict | None` — parse db.sh next-task output into a task dict
- Any field extraction helpers

Wire into `task_dispatcher.py`.

Write tests at `tests/component/test_task_parser.py`.

### Task 4: Extract ImpactAnalyzer from cross_section.py

Read `src/scripts/section_loop/cross_section.py`. Find the semantic impact analysis logic that determines which other sections are affected by a section's changes.

Create `src/scripts/lib/impact_analyzer.py`:
- Move the impact analysis dispatch and result parsing
- Keep the dispatch call (it dispatches to an LLM agent), but structure the analysis pipeline

Wire into `cross_section.py`.

Write tests at `tests/component/test_impact_analyzer.py`.

### Task 5: Extract ProposalStateRepository

Read `src/scripts/section_loop/proposal_state.py`. This file is already a clean self-contained module. But it should be in `lib/` since it's a Tier 7 repository.

Create `src/scripts/lib/proposal_state_repository.py`:
- Move all functions from `proposal_state.py`: `validate_proposal_state`, `load_proposal_state`, `save_proposal_state`, `has_blocking_fields`, `extract_blockers`, `_fail_closed_default`, `PROPOSAL_STATE_SCHEMA`, `_BLOCKING_FIELDS`

Update `proposal_state.py` to re-export from the new module.
Update any files that import from `proposal_state.py`.

Write tests at `tests/component/test_proposal_state_repository.py`.

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

Write a summary to `.tmp/refactor/phase7-results.md`.
