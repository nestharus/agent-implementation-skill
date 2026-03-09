# Refactoring Phase 12: Final Extractions — Log Extract, Alignment, Dispatch Helpers

You are orchestrating the final phase of a bottom-up refactoring of the agent-implementation-skill codebase.

## Important: Parallel Work

You are working in parallel with other agents on this repository. Do NOT revert changes you do not recognize.

## Completed So Far

### lib/ modules (49 total, all wired)
### Test counts: 1088 passed

## Phase 12 Tasks

### Task 1: Extract AlignmentService from alignment.py

Read `src/scripts/section_loop/alignment.py`. It has `_run_alignment_check_with_retries`, `_extract_problems`, `_parse_alignment_verdict`, and `collect_modified_files`.

Create `src/scripts/lib/alignment_service.py`:
- Move `collect_modified_files` — pure file collection from output
- Move `_extract_problems` as `extract_problems` — verdict-to-problems transformation (or delegate to verdict_parsers if appropriate)

Keep the dispatch-heavy functions (`_run_alignment_check_with_retries`) in alignment.py since they compose dispatch + monitoring.

Wire into `alignment.py`.

Write tests at `tests/component/test_alignment_service.py`.

### Task 2: Extract DispatchHelpers from dispatch.py

Read `src/scripts/section_loop/dispatch.py`. Find utility functions that don't involve subprocess dispatch:
- `summarize_output` — truncation of agent output for logging
- `write_model_choice_signal` — writing model choice artifacts
- `check_agent_signals` — reading signal files

Create `src/scripts/lib/dispatch_helpers.py`:
- Move these utility functions

Wire into `dispatch.py`.

Write tests at `tests/component/test_dispatch_helpers.py`.

### Task 3: Extract TaskIngestionService from task_ingestion.py

Read `src/scripts/section_loop/task_ingestion.py`. This handles task ingestion — parsing agent output for task submissions and routing them.

Create `src/scripts/lib/task_ingestion.py`:
- Move the task parsing and flow signal extraction logic
- Keep the dispatch/submission calls in the original file

Wire into `task_ingestion.py`.

Write tests at `tests/component/test_task_ingestion.py`.

### Task 4: Extract LogExtractUtils from log_extract/

Read `src/scripts/log_extract/utils.py`, `src/scripts/log_extract/correlator.py`, `src/scripts/log_extract/timeline.py`, `src/scripts/log_extract/formatters.py`. These are utility modules for the log extraction subsystem.

Create `src/scripts/lib/log_extract_utils.py`:
- Move shared utility functions from `utils.py` that could be reused
- Move timeline/correlation helpers if they're generic enough

Wire into the log_extract modules.

Write tests at `tests/component/test_log_extract_utils.py`.

### Task 5: Extract ScanDispatchService from scan/dispatch.py

Read `src/scripts/scan/dispatch.py`. This handles scan-specific agent dispatch.

Create `src/scripts/lib/scan_dispatch.py`:
- Move scan dispatch configuration and routing logic

Wire into `scan/dispatch.py`.

Write tests at `tests/component/test_scan_dispatch.py`.

## Process for each extraction

1. Read the source file(s) thoroughly
2. Identify the concern boundary — only extract what cleanly separates
3. Create the new module in `src/scripts/lib/`
4. Write component tests in `tests/component/`
5. Update the original file to import from the new module
6. Run `uv run pytest tests/ -q --tb=short` — must pass at each step

## Rules

- Do NOT change any behavior
- Test after EVERY change
- Write component tests for every new module
- Do not create abstractions that are more complex than the code they replace
- If the code is too tightly coupled to extract, note it in results and skip

## Verification

After ALL tasks, run:
```bash
uv run pytest tests/ -q --tb=short
```

Write a summary to `.tmp/refactor/phase12-results.md`.
